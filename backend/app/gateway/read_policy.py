from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlglot import exp

from backend.app.catalog.models import CatalogSnapshot, SchemaColumn, TableRelation
from backend.app.gateway.ast import (
    ParseMysqlError,
    alias_map,
    cte_names,
    direct_aggs,
    direct_joins,
    forbidden_function_names,
    has_aggregation,
    has_lock,
    has_numeric_tautology,
    limit_value,
    numeric_literals,
    parse_mysql,
    physical_tables,
    scope_columns,
    scope_physical_tables,
    select_has_star,
    string_literals,
    where_columns,
)
from backend.app.types import CompiledQuery, PermissionSet, QueryTask

DEFAULT_MAX_EXPLAIN_ROWS = 5_000_000
DEFAULT_MAX_DETAIL_LIMIT = 100_000


class GatewayDecision(BaseModel):
    ok: bool
    reason: str | None = None
    kind: Literal["unsafe", "too_broad", "ok"]


def _unsafe(reason: str) -> GatewayDecision:
    return GatewayDecision(ok=False, reason=reason, kind="unsafe")


def _too_broad(reason: str) -> GatewayDecision:
    return GatewayDecision(ok=False, reason=reason, kind="too_broad")


def _ok() -> GatewayDecision:
    return GatewayDecision(ok=True, reason=None, kind="ok")


def _split_field(field: str) -> tuple[str | None, str]:
    if "." in field:
        table, col = field.rsplit(".", 1)
        return table, col
    return None, field


def _task_allowed_tables(
    task: QueryTask,
    catalog: CatalogSnapshot,
    allowed_joins: list[TableRelation],
) -> set[str]:
    tables: set[str] = set()
    metrics = {m.metric_id: m for m in catalog.metrics}
    for metric_id in task.metric_ids:
        metric = metrics.get(metric_id)
        if metric is None:
            continue
        tables.add(metric.grain_table)
        tables.update(metric.needs_tables)
        time_table, _ = _split_field(metric.time_field)
        if time_table:
            tables.add(time_table)
        for dep in metric.deps:
            dep_table, _ = _split_field(dep)
            if dep_table:
                tables.add(dep_table)
        for cond in metric.filters:
            cond_table, _ = _split_field(cond.field)
            if cond_table:
                tables.add(cond_table)
    for dim in task.dimensions:
        dim_table, _ = _split_field(dim)
        if dim_table:
            tables.add(dim_table)
    for cond in task.filters:
        cond_table, _ = _split_field(cond.field)
        if cond_table:
            tables.add(cond_table)
    for rel in allowed_joins:
        tables.add(rel.left_table)
        tables.add(rel.right_table)
    return tables


def _permission_columns(permissions: PermissionSet) -> tuple[set[str], set[tuple[str, str]]]:
    wildcards: set[str] = set()
    columns: set[tuple[str, str]] = set()
    for spec in permissions.allowed_columns:
        parts = spec.split(".")
        if len(parts) < 2:
            continue
        table, col = parts[-2], parts[-1]
        if col == "*":
            wildcards.add(table)
        else:
            columns.add((table, col))
    return wildcards, columns


def _filter_literal_values(task: QueryTask) -> set[str]:
    values: set[str] = set()
    for cond in task.filters:
        raw = cond.value
        if isinstance(raw, (list, tuple, set)):
            values.update(str(item) for item in raw)
        else:
            values.add(str(raw))
    return values


def _resolve_column(
    col: exp.Column,
    mapping: dict[str, str],
    scope_tables: set[str],
    col_index: dict[tuple[str, str], SchemaColumn],
) -> list[tuple[str, str]]:
    name = col.name
    table_ref = col.table
    if table_ref:
        physical = mapping.get(table_ref, table_ref)
        return [(physical, name)]
    matches = [table for table in scope_tables if (table, name) in col_index]
    if matches:
        return [(table, name) for table in matches]
    if len(scope_tables) == 1:
        return [(next(iter(scope_tables)), name)]
    return [("", name)]


def _is_time_column(
    table: str,
    column: str,
    col_index: dict[tuple[str, str], SchemaColumn],
    catalog: CatalogSnapshot,
) -> bool:
    spec = col_index.get((table, column))
    if spec is not None and any(token in spec.data_type.lower() for token in ("date", "time")):
        return True
    if column.endswith("_at"):
        return True
    return any(metric.time_field == f"{table}.{column}" for metric in catalog.metrics)


def _join_on_pairs(join: exp.Join, mapping: dict[str, str]) -> list[tuple[str, str, str, str]]:
    on = join.args.get("on")
    if on is None:
        return []
    pairs: list[tuple[str, str, str, str]] = []
    for eq in on.find_all(exp.EQ):
        left, right = eq.this, eq.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        t1 = mapping.get(left.table, left.table) if left.table else ""
        t2 = mapping.get(right.table, right.table) if right.table else ""
        pairs.append((t1, left.name, t2, right.name))
    return pairs


def _join_allowed(
    join: exp.Join,
    mapping: dict[str, str],
    allowed_joins: list[TableRelation],
    cte: set[str],
) -> bool:
    if not isinstance(join.this, exp.Table):
        return False
    right = join.this.name
    if right in cte:
        return True
    pairs = _join_on_pairs(join, mapping)
    if not pairs:
        return False
    for t1, c1, t2, c2 in pairs:
        if t1 in cte or t2 in cte:
            return True
        for rel in allowed_joins:
            tables = {rel.left_table, rel.right_table}
            if {t1, t2} != tables:
                continue
            if (
                t1 == rel.left_table
                and c1 == rel.left_col
                and t2 == rel.right_table
                and c2 == rel.right_col
            ) or (
                t2 == rel.left_table
                and c2 == rel.left_col
                and t1 == rel.right_table
                and c1 == rel.right_col
            ):
                return True
    return False


def _is_one_to_many(grain: str, other: str, relations: list[TableRelation]) -> bool:
    for rel in relations:
        if rel.cardinality == "many_to_one" and rel.right_table == grain and rel.left_table == other:
            return True
        if rel.cardinality == "one_to_many" and rel.left_table == grain and rel.right_table == other:
            return True
    return False


def _agg_distinct_on_grain(agg: exp.AggFunc, grain: str, mapping: dict[str, str]) -> bool:
    inner = agg.this
    if not isinstance(inner, exp.Distinct):
        return False
    cols = list(inner.expressions or [])
    if not cols and isinstance(inner.this, exp.Column):
        cols = [inner.this]
    if not cols:
        return False
    for col in cols:
        if not isinstance(col, exp.Column):
            return False
        physical = mapping.get(col.table, col.table) if col.table else grain
        if physical != grain:
            return False
    return True


def check_read_sql(
    query: CompiledQuery,
    task: QueryTask,
    catalog: CatalogSnapshot,
    allowed_joins: list[TableRelation] | None = None,
    *,
    permissions: PermissionSet | None = None,
    explain_rows: int | None = None,
    max_explain_rows: int = DEFAULT_MAX_EXPLAIN_ROWS,
    max_detail_limit: int = DEFAULT_MAX_DETAIL_LIMIT,
) -> GatewayDecision:
    joins = list(allowed_joins or [])
    try:
        tree = parse_mysql(query.sql)
    except ParseMysqlError as exc:
        return _unsafe(str(exc))

    if not isinstance(tree, exp.Select):
        return _unsafe("expected a single SELECT")

    selects = list(tree.find_all(exp.Select))
    if any(select_has_star(sel) for sel in selects):
        return _unsafe("SELECT * is not allowed")
    if has_lock(tree):
        return _unsafe("locking reads are not allowed")
    forbidden = forbidden_function_names(tree)
    if forbidden:
        return _unsafe(f"forbidden function: {min(forbidden)}")
    if string_literals(tree) or has_numeric_tautology(tree):
        return _unsafe("user filters must be bound parameters")
    inlined = _filter_literal_values(task).intersection(numeric_literals(tree))
    if inlined:
        return _unsafe("user filters must be bound parameters")

    catalog_tables = {table.table_name for table in catalog.tables}
    col_index = {(col.table_name, col.column_name): col for col in catalog.columns}
    cte = cte_names(tree)
    allowed_tables = _task_allowed_tables(task, catalog, joins)
    if permissions is not None:
        allowed_tables &= set(permissions.allowed_tables)
        wildcards, perm_cols = _permission_columns(permissions)
    else:
        wildcards, perm_cols = set(), set()

    used_tables = physical_tables(tree)
    extra = used_tables - allowed_tables
    if extra or not used_tables <= catalog_tables:
        return _unsafe("table is not in the task allowlist")

    for sel in selects:
        mapping = alias_map(sel)
        scope = scope_physical_tables(sel, cte)
        for col in scope_columns(sel):
            for table, name in _resolve_column(col, mapping, scope, col_index):
                if table in cte:
                    continue
                spec = col_index.get((table, name))
                if spec is None:
                    return _unsafe("column is not in the task allowlist")
                if spec.is_sensitive:
                    return _unsafe("sensitive column")
                if permissions is not None:
                    permitted = table in wildcards or (table, name) in perm_cols
                    if not permitted:
                        return _unsafe("column is not in the task allowlist")

        for join in direct_joins(sel):
            if not _join_allowed(join, mapping, joins, cte):
                return _unsafe("JOIN is not in the recalled reviewed relations")

        aggs = direct_aggs(sel)
        if aggs:
            grain_tables = [
                m.grain_table
                for m in catalog.metrics
                if m.metric_id in task.metric_ids
            ]
            relations = list(catalog.relations) + joins
            for grain in grain_tables:
                if grain not in scope:
                    continue
                fanout = any(_is_one_to_many(grain, other, relations) for other in scope if other != grain)
                if not fanout:
                    continue
                if not all(_agg_distinct_on_grain(agg, grain, mapping) for agg in aggs):
                    return _unsafe("fan-out join would double-count the metric grain")

    root = tree
    detail = not any(has_aggregation(sel) for sel in selects)
    has_time = False
    for sel in selects:
        mapping = alias_map(sel)
        scope = scope_physical_tables(sel, cte)
        for col in where_columns(sel):
            for table, name in _resolve_column(col, mapping, scope, col_index):
                if table in cte:
                    continue
                if _is_time_column(table, name, col_index, catalog):
                    has_time = True
    limit = limit_value(root, query.params)
    limit_too_broad = limit is None or limit > max_detail_limit
    if detail and not has_time and limit_too_broad:
        return _too_broad("unconstrained detail scan")

    if explain_rows is not None and explain_rows > max_explain_rows:
        return _too_broad("EXPLAIN row estimate exceeds max_explain_rows")

    return _ok()
