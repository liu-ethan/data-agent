"""SQLite DDL and table-name constants for business vs app tables."""

from __future__ import annotations

import sqlite3

BUSINESS_TABLES: frozenset[str] = frozenset(
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

APP_TABLES: frozenset[str] = frozenset(
    {
        "app_users",
        "chat_sessions",
        "session_turns",
        "user_preferences",
        "user_analysis_summaries",
    }
)

SENSITIVE_USER_COLUMNS: frozenset[str] = frozenset(
    {"name", "phone", "email", "id_card"}
)

ALL_TABLES: frozenset[str] = BUSINESS_TABLES | APP_TABLES

DDL_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT,
        phone TEXT,
        email TEXT,
        id_card TEXT,
        city TEXT,
        province TEXT,
        gender TEXT,
        age_group TEXT,
        register_date TEXT,
        channel TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY,
        name TEXT,
        category TEXT,
        brand TEXT,
        price REAL,
        cost REAL,
        status TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        order_date TEXT,
        status TEXT,
        total_amount REAL,
        pay_amount REAL,
        channel TEXT,
        province TEXT,
        city TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY,
        order_id INTEGER,
        product_id INTEGER,
        quantity INTEGER,
        unit_price REAL,
        discount_amount REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY,
        order_id INTEGER,
        payment_method TEXT,
        paid_at TEXT,
        amount REAL,
        status TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS refunds (
        id INTEGER PRIMARY KEY,
        order_id INTEGER,
        refund_date TEXT,
        refund_amount REAL,
        reason TEXT,
        status TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY,
        name TEXT,
        channel TEXT,
        start_date TEXT,
        end_date TEXT,
        budget REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS traffic_logs (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        visit_date TEXT,
        channel TEXT,
        page_type TEXT,
        session_id TEXT,
        is_conversion INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_turns (
        id INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        question TEXT,
        intent TEXT,
        sql_text TEXT,
        metrics_json TEXT,
        time_range_json TEXT,
        filters_json TEXT,
        group_by_json TEXT,
        result_summary TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_preferences (
        user_id INTEGER PRIMARY KEY,
        preferences_json TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_analysis_summaries (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        session_id TEXT,
        question_summary TEXT,
        answer_summary TEXT,
        metrics_json TEXT,
        filters_json TEXT,
        created_at TEXT NOT NULL
    )
    """,
]


def apply_schema(conn: sqlite3.Connection) -> None:
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
