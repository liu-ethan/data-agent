import sqlite3

from app.db.schema import APP_TABLES, BUSINESS_TABLES


def test_business_and_app_table_sets():
    assert BUSINESS_TABLES == frozenset(
        {
            "users",
            "products",
            "orders",
            "order_items",
            "payments",
            "refunds",
            "campaigns",
            "traffic_logs",
        }
    )
    assert APP_TABLES == frozenset(
        {
            "app_users",
            "chat_sessions",
            "session_turns",
            "user_preferences",
            "user_analysis_summaries",
        }
    )
    assert BUSINESS_TABLES.isdisjoint(APP_TABLES)


def test_ddl_creates_all_tables(tmp_db_path):
    from app.db.database import get_connection
    from app.db.schema import apply_schema

    conn = get_connection()
    try:
        apply_schema(conn)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()

    assert BUSINESS_TABLES | APP_TABLES <= names


def test_users_has_sensitive_columns(tmp_db_path):
    from app.db.database import get_connection
    from app.db.schema import apply_schema

    conn = get_connection()
    try:
        apply_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    finally:
        conn.close()

    assert {"name", "phone", "email", "id_card"} <= cols


def test_seed_row_count_and_coverage(tmp_db_path):
    from app.db.init_db import init_database

    init_database(reset=True)
    conn = sqlite3.connect(tmp_db_path)
    try:
        total = 0
        for table in BUSINESS_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            total += n
        assert total >= 1000

        min_d, max_d = conn.execute(
            "SELECT MIN(order_date), MAX(order_date) FROM orders"
        ).fetchone()
        assert min_d is not None and max_d is not None

        channels = {r[0] for r in conn.execute("SELECT DISTINCT channel FROM orders")}
        assert len(channels) >= 3

        n_app = conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]
        assert n_app >= 1
    finally:
        conn.close()
