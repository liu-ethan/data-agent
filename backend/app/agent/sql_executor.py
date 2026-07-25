from __future__ import annotations

import re

from app.db.database import get_connection
from app.security.sql_guardrail import check_sql


class GuardrailError(Exception):
    """Raised when SQL fails guardrail checks before execution."""


def _apply_row_limit(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    if re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
        return stripped
    return f"SELECT * FROM ({stripped}) LIMIT 100"


def execute_sql(sql: str, *, user_role: str) -> tuple[list[str], list[dict]]:
    result = check_sql(sql, user_role=user_role)
    if not result.ok:
        raise GuardrailError(result.reason or "SQL blocked by guardrail")
    limited = _apply_row_limit(sql)
    conn = get_connection()
    try:
        cursor = conn.execute(limited)
        rows_raw = cursor.fetchall()
        if cursor.description:
            columns = [col[0] for col in cursor.description]
        else:
            columns = []
        rows = [dict(row) for row in rows_raw]
        return columns, rows
    finally:
        conn.close()
