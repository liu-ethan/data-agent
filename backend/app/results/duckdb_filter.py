from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import duckdb

from backend.app.types import FilterCond, LocalFilterSpec

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_OPS = {
    "=": "=",
    "!=": "!=",
    ">": ">",
    ">=": ">=",
    "<": "<",
    "<=": "<=",
    "like": "LIKE",
}


def _quote_ident(name: str, allowed: set[str]) -> str:
    if name not in allowed or _IDENT.fullmatch(name) is None:
        raise ValueError(f"unknown or illegal column: {name}")
    return f'"{name}"'


def _compile_cond(cond: FilterCond, allowed: set[str], params: list[Any]) -> str:
    col = _quote_ident(cond.field, allowed)
    if cond.op in {"in", "not_in"}:
        values = list(cond.value)
        if not values:
            return "FALSE" if cond.op == "in" else "TRUE"
        placeholders = ", ".join("?" * len(values))
        params.extend(values)
        kw = "IN" if cond.op == "in" else "NOT IN"
        return f"{col} {kw} ({placeholders})"
    params.append(cond.value)
    return f"{col} {_OPS[cond.op]} ?"


def _compile_order(item: str, allowed: set[str]) -> str:
    parts = item.split()
    if len(parts) == 1:
        return f"{_quote_ident(parts[0], allowed)} ASC"
    if len(parts) == 2 and parts[1].upper() in {"ASC", "DESC"}:
        return f"{_quote_ident(parts[0], allowed)} {parts[1].upper()}"
    raise ValueError(f"illegal order_by: {item}")


def compile_local_filter(
    parquet_path: str | Path,
    spec: LocalFilterSpec,
    columns: Sequence[str],
) -> tuple[str, list[Any]]:
    allowed = set(columns)
    if spec.select:
        select_sql = ", ".join(_quote_ident(name, allowed) for name in spec.select)
    else:
        select_sql = ", ".join(_quote_ident(name, allowed) for name in columns)
    params: list[Any] = [str(parquet_path)]
    where_parts = [_compile_cond(cond, allowed, params) for cond in spec.filters]
    where_sql = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    order_parts = [_compile_order(item, allowed) for item in spec.order_by]
    order_sql = f" ORDER BY {', '.join(order_parts)}" if order_parts else ""
    limit_sql = ""
    if spec.topn is not None:
        if spec.topn < 0:
            raise ValueError("topn must be >= 0")
        limit_sql = " LIMIT ?"
        params.append(spec.topn)
    sql = f"SELECT {select_sql} FROM read_parquet(?){where_sql}{order_sql}{limit_sql}"
    return sql, params


def _cell(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def filter_parquet(
    parquet_path: str | Path,
    spec: LocalFilterSpec,
    columns: Sequence[str],
) -> list[dict[str, Any]]:
    sql, params = compile_local_filter(parquet_path, spec, columns)
    con = duckdb.connect()
    try:
        con.execute(sql, params)
        names = [col[0] for col in con.description]
        return [dict(zip(names, (_cell(v) for v in row))) for row in con.fetchall()]
    finally:
        con.close()
