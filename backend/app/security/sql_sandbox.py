from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from app.config import get_settings
from app.security.sql_guardrail import check_sql

MAX_WRITE_ROWS = 100
SANDBOX_TIMEOUT_S = 5.0


class SandboxError(Exception):
    pass


class GuardrailSandboxError(SandboxError):
    """SQL rejected by guardrail before execution."""


@dataclass
class SandboxResult:
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    affected_rows: int | None = None
    is_write: bool = False


def _apply_row_limit(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    if re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
        return stripped
    return f"SELECT * FROM ({stripped}) LIMIT 100"


def _is_write(sql: str) -> bool:
    head = re.sub(r"\A(?:\s*--[^\n]*(?:\n|\Z))*\s*", "", sql.strip())
    if re.match(r"(?:INSERT|UPDATE|DELETE)\b", head, re.IGNORECASE):
        return True
    if re.match(r"WITH\b", head, re.IGNORECASE):
        return bool(re.search(r"\b(INSERT|UPDATE|DELETE)\b", head, re.IGNORECASE))
    return False


def _write_affected_rows(conn: sqlite3.Connection, cur: sqlite3.Cursor) -> int:
    if cur.rowcount is not None and cur.rowcount >= 0:
        return cur.rowcount
    row = conn.execute("SELECT changes()").fetchone()
    return int(row[0]) if row else 0


def sandbox_execute(sql: str, *, user_role: str) -> SandboxResult:
    gr = check_sql(sql, user_role=user_role)
    if not gr.ok:
        raise GuardrailSandboxError(gr.reason or "SQL blocked by guardrail")

    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=SANDBOX_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    try:
        if user_role == "analyst":
            conn.execute("PRAGMA query_only=ON")
        if _is_write(sql):
            try:
                conn.execute("BEGIN")
                cur = conn.execute(sql.strip().rstrip(";"))
                n = _write_affected_rows(conn, cur)
                if n > MAX_WRITE_ROWS:
                    conn.rollback()
                    raise SandboxError(f"Write affects more than {MAX_WRITE_ROWS} rows")
                conn.commit()
                return SandboxResult(affected_rows=n, is_write=True)
            except SandboxError:
                raise
            except Exception as exc:
                conn.rollback()
                raise SandboxError(str(exc).splitlines()[0][:200]) from None
        limited = _apply_row_limit(sql)
        cur = conn.execute(limited)
        rows_raw = cur.fetchall()
        columns = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(r) for r in rows_raw]
        return SandboxResult(columns=columns, rows=rows, is_write=False)
    except SandboxError:
        raise
    except Exception as exc:
        raise SandboxError(str(exc).splitlines()[0][:200]) from None
    finally:
        conn.close()
