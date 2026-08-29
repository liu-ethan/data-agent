from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import sqlglot
from sqlglot import exp

from backend.app.catalog.models import MetricSpec
from backend.app.types import CompiledQuery, FilterCond, QuerySkeleton, TimeRange

TABLE_ALIASES = {
    "fact_order_item": "oi",
    "fact_order": "o",
    "fact_refund": "r",
    "fact_traffic": "t",
    "fact_ad_spend": "a",
    "dim_user": "u",
    "dim_sku": "s",
    "dim_store": "st",
    "dim_category": "c",
    "dim_channel": "ch",
    "dim_campaign": "ca",
    "fact_payment": "p",
}
ALIAS_TABLE = {alias: table for table, alias in TABLE_ALIASES.items()}
GRAIN_PRIORITY = ("oi", "r", "a", "t", "o", "u")
_FORMULA_LIKE = re.compile(r"(?i)\b(SUM|COUNT|AVG|MIN|MAX|NULLIF|COALESCE)\s*\(")
_OP_SQL = {
    "=": "=",
    "!=": "!=",
    ">": ">",
    ">=": ">=",
    "<": "<",
    "<=": "<=",
    "like": "LIKE",
    "in": "IN",
    "not_in": "NOT IN",
}
_OP_TOKEN = {
    "=": "eq",
    "!=": "ne",
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
    "like": "like",
    "in": "in",
    "not_in": "not_in",
}


def compile(
    skeleton: QuerySkeleton,
    metrics: list[MetricSpec],
    time: TimeRange,
) -> CompiledQuery:
    params: dict[str, Any] = {"start": time.start, "end": time.end}
    comparisons = list(skeleton.comparisons)
    if "yoy" in comparisons:
        params["yoy_start"] = _shift_years(time.start, -1)
        params["yoy_end"] = _shift_years(time.end, -1)
    if "mom" in comparisons:
        mom_start, mom_end = _prev_period(time.start, time.end)
        params["mom_start"] = mom_start
        params["mom_end"] = mom_end

    by_id = {metric.metric_id: metric for metric in metrics}
    ordered = [by_id[metric_id] for metric_id in skeleton.metric_ids if metric_id in by_id]
    dims = _dims(skeleton)
    dim_aliases = _dim_aliases(dims)

    grain_cols: dict[str, list[tuple[str, str]]] = {}
    grain_metrics: dict[str, list[MetricSpec]] = {}
    outer_select: list[str] = []
    current_exprs: dict[str, str] = {}
    wrap_current = any(kind in comparisons for kind in ("yoy", "mom"))

    for metric in ordered:
        current_wrap = _time_pred(metric, "start", "end") if wrap_current else None
        current_expr, current_pieces = _metric_plan(metric, wrap=current_wrap)
        _add_grain_cols(grain_cols, grain_metrics, metric, current_pieces)
        current_exprs[metric.metric_id] = current_expr
        outer_select.append(f"{current_expr} AS {metric.metric_id}")
        for kind in ("yoy", "mom"):
            if kind not in comparisons:
                continue
            prior_expr, prior_pieces = _metric_plan(
                metric, wrap=_time_pred(metric, f"{kind}_start", f"{kind}_end")
            )
            _add_grain_cols(grain_cols, grain_metrics, metric, prior_pieces)
            outer_select.append(
                f"({current_expr} - {prior_expr}) / NULLIF({prior_expr}, 0) AS {metric.metric_id}_{kind}"
            )

    grains = sorted(grain_cols)
    cte_sql: list[str] = []
    for grain in grains:
        sql, _tables = _grain_cte(
            name=_cte_name(grain),
            grain=grain,
            columns=grain_cols[grain],
            metrics=grain_metrics[grain],
            skeleton=skeleton,
            dim_aliases=dim_aliases,
            params=params,
            comparisons=comparisons,
        )
        cte_sql.append(sql)

    if "ratio" in comparisons:
        for metric in ordered:
            expr = current_exprs[metric.metric_id]
            outer_select.append(
                f"{expr} / NULLIF(SUM({expr}) OVER (), 0) AS {metric.metric_id}_ratio"
            )

    versions = ",".join(f"{metric.metric_id}={metric.version}" for metric in ordered)
    header = f"/* metric_versions:{versions} */"
    with_sql = "WITH\n" + ",\n".join(cte_sql)
    select_sql = _outer_select(
        grains=grains,
        dim_aliases=dim_aliases,
        metric_selects=outer_select,
        skeleton=skeleton,
        params=params,
        comparisons=comparisons,
        ordered=ordered,
    )
    return CompiledQuery(sql=f"{header}\n{with_sql}\n{select_sql}", params=params)


def _add_grain_cols(
    grain_cols: dict[str, list[tuple[str, str]]],
    grain_metrics: dict[str, list[MetricSpec]],
    metric: MetricSpec,
    pieces: list[tuple[str, str, str]],
) -> None:
    for grain, alias, expr in pieces:
        cols = grain_cols.setdefault(grain, [])
        for index, (name, _old) in enumerate(cols):
            if name == alias:
                cols[index] = (alias, expr)
                break
        else:
            cols.append((alias, expr))
        grain_metrics.setdefault(grain, []).append(metric)


def _dims(skeleton: QuerySkeleton) -> list[str]:
    fields = list(skeleton.group_by) or list(skeleton.select_dims)
    return [field for field in fields if not _FORMULA_LIKE.search(field)]


def _dim_aliases(dims: list[str]) -> list[tuple[str, str]]:
    used: set[str] = set()
    result: list[tuple[str, str]] = []
    for field in dims:
        alias = field.rsplit(".", 1)[-1]
        if alias in used:
            alias = field.replace(".", "_")
        used.add(alias)
        result.append((field, alias))
    return result


def _cte_name(grain: str) -> str:
    return f"grain_{grain}"


def _alias(table: str) -> str:
    return TABLE_ALIASES.get(table, table[:2] if len(table) > 2 else table)


def _qualify(field: str) -> str:
    if "." not in field:
        return field
    table, column = field.rsplit(".", 1)
    return f"{_alias(table)}.{column}"


def _split_field(field: str) -> tuple[str | None, str]:
    if "." in field:
        table, column = field.rsplit(".", 1)
        return table, column
    return None, field


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _shift_years(value: str, years: int) -> str:
    dt = _parse_iso(value)
    try:
        return dt.replace(year=dt.year + years).isoformat()
    except ValueError:
        return dt.replace(year=dt.year + years, day=28).isoformat()


def _prev_period(start: str, end: str) -> tuple[str, str]:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    delta: timedelta = end_dt - start_dt
    return (start_dt - delta).isoformat(), start_dt.isoformat()


def _time_pred(metric: MetricSpec, start_key: str, end_key: str) -> str:
    col = _qualify(metric.time_field)
    return f"{col} >= :{start_key} AND {col} < :{end_key}"


def _parse_expr(sql: str) -> exp.Expression:
    return sqlglot.parse_one(sql, read="mysql")


def _inside_agg(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.AggFunc):
            return True
        parent = parent.parent
    return False


def _top_aggs(tree: exp.Expression) -> list[exp.AggFunc]:
    return [node for node in tree.find_all(exp.AggFunc) if not _inside_agg(node)]


def _grain_of(node: exp.Expression, default: str) -> str:
    aliases: set[str] = set()
    for col in node.find_all(exp.Column):
        if col.table:
            aliases.add(col.table)
    for alias in GRAIN_PRIORITY:
        if alias in aliases:
            return ALIAS_TABLE[alias]
    for alias in aliases:
        if alias in ALIAS_TABLE:
            return ALIAS_TABLE[alias]
        if alias in TABLE_ALIASES:
            return alias
    return default


def _wrap_agg_node(agg: exp.AggFunc, pred: str) -> None:
    inner = agg.this
    if isinstance(inner, exp.Distinct):
        wrapped = [
            _parse_expr(f"CASE WHEN {pred} THEN {expr.sql(dialect='mysql')} END")
            for expr in inner.expressions
        ]
        inner.set("expressions", wrapped)
        return
    inner_sql = inner.sql(dialect="mysql") if inner is not None else "1"
    agg.set("this", _parse_expr(f"CASE WHEN {pred} THEN {inner_sql} END"))


def _wrap_formula(formula: str, pred: str | None) -> str:
    if not pred:
        return formula
    tree = _parse_expr(formula)
    for agg in _top_aggs(tree):
        _wrap_agg_node(agg, pred)
    return tree.sql(dialect="mysql")


def _metric_plan(
    metric: MetricSpec, *, wrap: str | None
) -> tuple[str, list[tuple[str, str, str]]]:
    tree = _parse_expr(metric.formula)
    aggs = _top_aggs(tree)
    grains = {_grain_of(agg, metric.grain_table) for agg in aggs} or {metric.grain_table}
    suffix = ""
    if wrap and ":yoy_start" in wrap:
        suffix = "_yoy_prior"
    elif wrap and ":mom_start" in wrap:
        suffix = "_mom_prior"

    if len(grains) <= 1:
        grain = next(iter(grains))
        alias = f"{metric.metric_id}{suffix}"
        expr = _wrap_formula(metric.formula, wrap)
        return f"{_cte_name(grain)}.{alias}", [(grain, alias, expr)]

    pieces: list[tuple[str, str, str]] = []
    for index, agg in enumerate(aggs):
        grain = _grain_of(agg, metric.grain_table)
        alias = f"{metric.metric_id}__{index}{suffix}"
        agg_sql = agg.sql(dialect="mysql")
        expr = _wrap_formula(agg_sql, wrap)
        pieces.append((grain, alias, expr))
        agg.replace(_parse_expr(f"{_cte_name(grain)}.{alias}"))
    return tree.sql(dialect="mysql"), pieces


def _tables_in_expr(sql: str) -> set[str]:
    tree = _parse_expr(sql)
    tables: set[str] = set()
    for col in tree.find_all(exp.Column):
        if not col.table:
            continue
        if col.table in ALIAS_TABLE:
            tables.add(ALIAS_TABLE[col.table])
        elif col.table in TABLE_ALIASES:
            tables.add(col.table)
    return tables


def _join_edges(joins: list[dict[str, str]]) -> list[tuple[str, str, str, str, bool]]:
    edges: list[tuple[str, str, str, str, bool]] = []
    for join in joins:
        left, right = join["left"], join["right"]
        on_left, on_right = join["on_left"], join["on_right"]
        card = join.get("cardinality", "many_to_one")
        fanout_lr = card == "one_to_many"
        fanout_rl = card == "many_to_one"
        edges.append((left, right, on_left, on_right, fanout_lr))
        edges.append((right, left, on_right, on_left, fanout_rl))
    return edges


def _join_path(grain: str, needed: set[str], joins: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    edges = _join_edges(joins)
    prev: dict[str, tuple[str, str, str] | None] = {grain: None}
    queue: deque[str] = deque([grain])
    while queue:
        node = queue.popleft()
        for src, dest, src_col, dest_col, fanout in edges:
            if fanout or src != node or dest in prev:
                continue
            prev[dest] = (src, src_col, dest_col)
            queue.append(dest)
    path: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for target in sorted(needed):
        if target == grain or target not in prev:
            continue
        chain: list[tuple[str, str, str, str]] = []
        cur = target
        while prev[cur] is not None:
            src, src_col, dest_col = prev[cur]
            chain.append((src, cur, src_col, dest_col))
            cur = src
        for step in reversed(chain):
            key = (step[0], step[1])
            if key in seen:
                continue
            seen.add(key)
            path.append(step)
    return path


def _filter_sql(cond: FilterCond, params: dict[str, Any]) -> str:
    col = _qualify(cond.field)
    if cond.value is None:
        if cond.op == "=":
            return f"{col} IS NULL"
        if cond.op == "!=":
            return f"{col} IS NOT NULL"
    table, column = _split_field(cond.field)
    base = f"{table or 'col'}_{column}_{_OP_TOKEN[cond.op]}"
    name = base
    index = 2
    while name in params and params[name] != cond.value:
        name = f"{base}_{index}"
        index += 1
    params[name] = cond.value
    op_sql = _OP_SQL[cond.op]
    return f"{col} {op_sql} :{name}"


def _unique_filters(filters: list[FilterCond]) -> list[FilterCond]:
    seen: set[tuple[Any, ...]] = set()
    out: list[FilterCond] = []
    for cond in filters:
        value = cond.value
        if isinstance(value, list):
            key = (cond.field, cond.op, tuple(value))
        else:
            key = (cond.field, cond.op, value)
        if key in seen:
            continue
        seen.add(key)
        out.append(cond)
    return out


def _grain_cte(
    *,
    name: str,
    grain: str,
    columns: list[tuple[str, str]],
    metrics: list[MetricSpec],
    skeleton: QuerySkeleton,
    dim_aliases: list[tuple[str, str]],
    params: dict[str, Any],
    comparisons: list[str],
) -> tuple[str, set[str]]:
    needed: set[str] = {grain}
    for _, expr in columns:
        needed |= _tables_in_expr(expr)
    unique_metrics = []
    seen_ids: set[str] = set()
    for metric in metrics:
        if metric.metric_id in seen_ids:
            continue
        seen_ids.add(metric.metric_id)
        unique_metrics.append(metric)
        table, _ = _split_field(metric.time_field)
        if table:
            needed.add(table)
        for cond in metric.filters:
            t, _ = _split_field(cond.field)
            if t:
                needed.add(t)
        needed.update(metric.needs_tables)
    for field, _ in dim_aliases:
        t, _ = _split_field(field)
        if t:
            needed.add(t)
    for cond in skeleton.filters:
        t, _ = _split_field(cond.field)
        if t:
            needed.add(t)

    path = _join_path(grain, needed, skeleton.joins)
    tables = {grain}
    from_sql = [f"FROM {grain} AS {_alias(grain)}"]
    for src, dest, src_col, dest_col in path:
        if dest in tables:
            continue
        from_sql.append(
            f"JOIN {dest} AS {_alias(dest)} ON {_alias(src)}.{src_col} = {_alias(dest)}.{dest_col}"
        )
        tables.add(dest)

    select_parts = [f"{_qualify(field)} AS {alias}" for field, alias in dim_aliases if _split_field(field)[0] in tables or "." not in field]
    seen_col: set[str] = set()
    for alias, expr in columns:
        if alias in seen_col:
            continue
        seen_col.add(alias)
        select_parts.append(f"{expr} AS {alias}")

    where_parts: list[str] = []
    time_preds: list[str] = []
    for metric in unique_metrics:
        periods = [("start", "end")]
        if "yoy" in comparisons:
            periods.append(("yoy_start", "yoy_end"))
        if "mom" in comparisons:
            periods.append(("mom_start", "mom_end"))
        period_sql = [_time_pred(metric, a, b) for a, b in periods]
        metric_pred = period_sql[0] if len(period_sql) == 1 else "(" + ") OR (".join(period_sql) + ")"
        if metric_pred not in time_preds:
            time_preds.append(metric_pred)
    if len(time_preds) == 1:
        where_parts.append(time_preds[0])
    elif time_preds:
        where_parts.append("(" + ") OR (".join(time_preds) + ")")

    filters = _unique_filters([cond for metric in unique_metrics for cond in metric.filters] + list(skeleton.filters))
    for cond in filters:
        t, _ = _split_field(cond.field)
        if t is not None and t not in tables:
            continue
        where_parts.append(_filter_sql(cond, params))

    lines = [f"{name} AS (", f"  SELECT {', '.join(select_parts)}"]
    lines.extend(f"  {clause}" for clause in from_sql)
    if where_parts:
        bound = [f"({part})" if " OR " in part else part for part in where_parts]
        lines.append("  WHERE " + " AND ".join(bound))
    grouped = [alias for field, alias in dim_aliases if _split_field(field)[0] in tables or "." not in field]
    if grouped:
        lines.append("  GROUP BY " + ", ".join(_qualify(field) for field, alias in dim_aliases if alias in grouped))
    lines.append(")")
    return "\n".join(lines), tables


def _outer_select(
    *,
    grains: list[str],
    dim_aliases: list[tuple[str, str]],
    metric_selects: list[str],
    skeleton: QuerySkeleton,
    params: dict[str, Any],
    comparisons: list[str],
    ordered: list[MetricSpec],
) -> str:
    cte_names = [_cte_name(grain) for grain in grains]
    dim_select = [f"{cte_names[0]}.{alias} AS {alias}" for _, alias in dim_aliases] if cte_names else []
    select_list = dim_select + metric_selects
    if len(cte_names) == 1:
        from_sql = f"FROM {cte_names[0]}"
    elif not dim_aliases:
        from_sql = "FROM " + " CROSS JOIN ".join(cte_names)
    else:
        key_cols = ", ".join(alias for _, alias in dim_aliases)
        unions = " UNION ".join(f"SELECT {key_cols} FROM {name}" for name in cte_names)
        joins = []
        for name in cte_names:
            on = " AND ".join(f"_keys.{alias} = {name}.{alias}" for _, alias in dim_aliases)
            joins.append(f"LEFT JOIN {name} ON {on}")
        from_sql = f"FROM ({unions}) AS _keys\n  " + "\n  ".join(joins)
        select_list = [f"_keys.{alias} AS {alias}" for _, alias in dim_aliases] + metric_selects

    lines = [f"SELECT {', '.join(select_list)}", f"  {from_sql}"]
    if "topn" in comparisons and skeleton.limit is not None and ordered:
        params["limit"] = skeleton.limit
        lines.append(f"  ORDER BY {ordered[0].metric_id} DESC")
        lines.append("  LIMIT :limit")
    elif skeleton.limit is not None:
        params["limit"] = skeleton.limit
        lines.append("  LIMIT :limit")
    return "\n".join(lines)
