from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def estimate_explain_rows(
    sql: str,
    params: dict[str, Any],
    *,
    engine: Engine | None = None,
) -> int:
    """MySQL EXPLAIN row estimate. Never runs the SELECT itself."""
    if engine is None:
        from backend.app.mysql.pool import get_engine

        engine = get_engine("reader")
    prefix = sql.lstrip().lower()
    explain_sql = sql if prefix.startswith("explain") else f"EXPLAIN {sql}"
    with engine.connect() as conn:
        result = conn.execute(text(explain_sql), params)
        total = 0
        for row in result.mappings():
            estimated = row.get("rows")
            if estimated is not None:
                total += int(estimated)
        return total
