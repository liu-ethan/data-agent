from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.sql.elements import TextClause

from backend.app.resources.paths import sqlite_ddl_dir, sql_root


@lru_cache(maxsize=32)
def _named_queries(stem: str) -> dict[str, str]:
    path = sql_root() / "queries" / f"{stem}.sql"
    body = path.read_text(encoding="utf-8")
    queries: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("-- name:"):
            if current is not None:
                queries[current] = "\n".join(buf).strip()
            current = line.split(":", 1)[1].strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        queries[current] = "\n".join(buf).strip()
    return queries


def load_sql(name: str, **fmt: str) -> str:
    if "." not in name:
        raise KeyError(f"sql name must be file.query, got {name!r}")
    stem, key = name.split(".", 1)
    sql = _named_queries(stem).get(key)
    if sql is None:
        raise KeyError(f"unknown sql: {name}")
    return sql.format(**fmt) if fmt else sql


def apply_sql(db_path: Path, sql_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = sql_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
        conn.commit()


def apply_sqlite_ddl(db_path: Path, name: str) -> None:
    apply_sql(db_path, sqlite_ddl_dir() / f"{name}.sql")


def mysql_text(name: str, *, expanding: Sequence[str] = (), **fmt: str) -> TextClause:
    stmt = text(load_sql(name, **fmt))
    if expanding:
        stmt = stmt.bindparams(*(bindparam(item, expanding=True) for item in expanding))
    return stmt


SQLITE_DDL_DIR = sqlite_ddl_dir()
