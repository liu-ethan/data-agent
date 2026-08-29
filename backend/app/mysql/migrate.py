from __future__ import annotations

from backend.app.mysql.pool import get_engine
from backend.app.resources.domain import BUSINESS_TABLES, control_tables
from backend.app.resources.sql import mysql_text


def ensure_slice_tables() -> None:
    """No-op when the ecommerce slice already exists. Never DROP or recreate."""
    engine = get_engine("reader")
    with engine.connect() as conn:
        rows = conn.execute(mysql_text("catalog.mysql_list_tables_in_schema"))
        existing = {row[0] for row in rows}
    required = tuple(sorted(BUSINESS_TABLES)) + control_tables()
    missing = [name for name in required if name not in existing]
    if missing:
        raise RuntimeError(f"missing tables {missing}; run scripts/apply_mysql_slice.sh")
