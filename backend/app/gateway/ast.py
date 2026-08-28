from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


class ParseMysqlError(ValueError):
    pass


def parse_mysql(sql: str) -> exp.Expression:
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except SqlglotError as exc:
        raise ParseMysqlError(str(exc)) from exc
    statements = [stmt for stmt in statements if stmt is not None]
    if len(statements) != 1:
        raise ParseMysqlError("expected exactly one statement")
    return statements[0]


def cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name for cte in tree.find_all(exp.CTE)}


def physical_tables(tree: exp.Expression) -> set[str]:
    skip = cte_names(tree)
    names: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name
        if name and name not in skip:
            names.add(name)
    return names


def _nested_in_other_select(node: exp.Expression, root: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and parent is not root:
        if isinstance(parent, exp.Select):
            return True
        parent = parent.parent
    return False


def select_has_star(select: exp.Select) -> bool:
    for item in select.expressions:
        if isinstance(item, exp.Star):
            return True
        if isinstance(item, exp.Column) and isinstance(item.this, exp.Star):
            return True
        if isinstance(item, exp.Alias) and (
            isinstance(item.this, exp.Star)
            or (isinstance(item.this, exp.Column) and isinstance(item.this.this, exp.Star))
        ):
            return True
    return False


def has_lock(tree: exp.Expression) -> bool:
    return any(True for _ in tree.find_all(exp.Lock))


FORBIDDEN_FUNCS = frozenset(
    {
        "load_file",
        "sleep",
        "benchmark",
        "updatexml",
        "extractvalue",
        "get_lock",
        "release_lock",
    }
)


def forbidden_function_names(tree: exp.Expression) -> set[str]:
    found: set[str] = set()
    for func in tree.find_all(exp.Func):
        name = (func.name or "").lower()
        if name in FORBIDDEN_FUNCS:
            found.add(name)
    return found


def _under_limit(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Limit):
            return True
        parent = parent.parent
    return False


def string_literals(tree: exp.Expression) -> list[str]:
    values: list[str] = []
    for lit in tree.find_all(exp.Literal):
        if lit.is_string and not _under_limit(lit):
            values.append(str(lit.this))
    return values


def numeric_literals(tree: exp.Expression) -> list[str]:
    values: list[str] = []
    for lit in tree.find_all(exp.Literal):
        if not lit.is_string and not _under_limit(lit):
            values.append(str(lit.this))
    return values


def has_numeric_tautology(tree: exp.Expression) -> bool:
    for eq in tree.find_all(exp.EQ):
        left, right = eq.this, eq.expression
        if (
            isinstance(left, exp.Literal)
            and isinstance(right, exp.Literal)
            and not left.is_string
            and not right.is_string
            and str(left.this) == str(right.this)
        ):
            return True
    return False


def alias_map(select: exp.Select) -> dict[str, str]:
    mapping: dict[str, str] = {}
    from_ = select.args.get("from_")
    tables: list[exp.Expression] = []
    if from_ is not None:
        tables.append(from_.this)
    for join in select.args.get("joins") or []:
        tables.append(join.this)
    for table in tables:
        if isinstance(table, exp.Table):
            mapping[table.alias_or_name] = table.name
            mapping[table.name] = table.name
    return mapping


def scope_physical_tables(select: exp.Select, cte: set[str]) -> set[str]:
    names: set[str] = set()
    for name in alias_map(select).values():
        if name not in cte:
            names.add(name)
    return names


def direct_joins(select: exp.Select) -> list[exp.Join]:
    return list(select.args.get("joins") or [])


def direct_aggs(select: exp.Select) -> list[exp.AggFunc]:
    return [node for node in select.find_all(exp.AggFunc) if not _nested_in_other_select(node, select)]


def has_aggregation(select: exp.Select) -> bool:
    return bool(direct_aggs(select) or select.args.get("group"))


def limit_value(select: exp.Select, params: dict[str, Any]) -> int | None:
    limit = select.args.get("limit")
    if limit is None:
        return None
    expr = limit.expression if isinstance(limit, exp.Limit) else limit
    if isinstance(expr, exp.Literal) and not expr.is_string:
        return int(expr.this)
    if isinstance(expr, exp.Placeholder) and expr.this in params:
        return int(params[expr.this])
    return None


def scope_columns(select: exp.Select) -> list[exp.Column]:
    nodes: list[exp.Expression] = list(select.expressions)
    where = select.args.get("where")
    if where is not None:
        nodes.append(where)
    for join in direct_joins(select):
        on = join.args.get("on")
        if on is not None:
            nodes.append(on)
    cols: list[exp.Column] = []
    for node in nodes:
        for col in node.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                continue
            if not _nested_in_other_select(col, select):
                cols.append(col)
    return cols


def where_columns(select: exp.Select) -> list[exp.Column]:
    where = select.args.get("where")
    if where is None:
        return []
    return list(where.find_all(exp.Column))
