from __future__ import annotations

from sqlalchemy import text

from backend.app.mysql.pool import get_engine

SLICE_TABLES = (
    "dim_store",
    "dim_user",
    "dim_category",
    "dim_sku",
    "dim_channel",
    "dim_campaign",
    "fact_order",
    "fact_order_item",
    "fact_payment",
    "fact_refund",
    "fact_traffic",
    "fact_ad_spend",
    "da_write_receipt",
    "da_write_audit",
)


def ensure_slice_tables() -> None:
    """No-op when the ecommerce slice already exists. Never DROP or recreate."""
    engine = get_engine("reader")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE()"
            )
        )
        existing = {row[0] for row in rows}
    missing = [name for name in SLICE_TABLES if name not in existing]
    if missing:
        raise RuntimeError(f"missing tables {missing}; run scripts/apply_mysql_slice.sh")
