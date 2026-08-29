#!/usr/bin/env python3
"""Ping MySQL business DB. Do not write business rows."""

from __future__ import annotations

import sys
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from load_config import load_config
from backend.app.resources.domain import ALL_TABLES, control_tables

BUSINESS_TABLES = list(ALL_TABLES)
CONTROL_TABLES = list(control_tables())
FORBIDDEN_TABLES = [
    "schema_table",
    "schema_column",
    "metric_spec",
    "app_user",
    "embedding_manifest",
    "query_result",
    "thread",
    "catalog_meta",
]


def connect(cfg: dict, role: str) -> pymysql.Connection:
    mysql = cfg["mysql"]
    acc = mysql[role]
    return pymysql.connect(
        host=mysql["host"],
        port=int(mysql["port"]),
        user=acc["user"],
        password=str(acc["password"]),
        database=mysql["database"],
        charset=mysql.get("charset") or "utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def must_fail(conn: pymysql.Connection, sql: str, label: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.rollback()
        raise SystemExit(f"FAIL {label}: statement succeeded, should be denied")
    except pymysql.Error:
        conn.rollback()
        print(f"OK  {label} denied")


def main() -> int:
    cfg = load_config()
    mysql = cfg["mysql"]
    db = mysql["database"]
    if db != "data-agent-ecommerce":
        raise SystemExit(f"FAIL unexpected mysql.database={db!r}")

    with connect(cfg, "reader") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT CURRENT_USER() AS u, DATABASE() AS d")
            row = cur.fetchone()
            print(f"OK  reader {row['u']} db={row['d']}")
            cur.execute(
                "SELECT table_name AS tbl FROM information_schema.tables "
                "WHERE table_schema=%s",
                (db,),
            )
            tables = {r["tbl"] for r in cur.fetchall()}
            missing = [t for t in BUSINESS_TABLES + CONTROL_TABLES if t not in tables]
            if missing:
                raise SystemExit(
                    f"FAIL missing tables {missing}; run scripts/apply_mysql_slice.sh"
                )
            leaked = sorted(tables & set(FORBIDDEN_TABLES))
            if leaked:
                raise SystemExit(f"FAIL control-plane tables in MySQL: {leaked}")
            extra_ok = tables - set(BUSINESS_TABLES) - set(CONTROL_TABLES)
            if extra_ok:
                print(f"WARN extra MySQL tables (not control-plane): {sorted(extra_ok)}")
            cur.execute("SELECT COUNT(*) AS n FROM fact_order")
            n = cur.fetchone()["n"]
            if n < 1:
                raise SystemExit("FAIL fact_order is empty; re-run seed")
            print(f"OK  12 business + 2 receipt tables; fact_order rows={n}")
        must_fail(
            conn,
            "INSERT INTO da_write_receipt (operation_id, request_hash, operation_type, status, payload_json) "
            "VALUES ('probe', REPEAT('0',64), 'probe', 'pending', '{}')",
            "reader INSERT",
        )

    with connect(cfg, "writer") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT CURRENT_USER() AS u")
            print(f"OK  writer {cur.fetchone()['u']}")
        must_fail(conn, "DROP TABLE dim_sku", "writer DROP")
        must_fail(conn, "UPDATE fact_order SET status='probe' WHERE id=1", "writer UPDATE fact_order")

    print("OK  mysql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
