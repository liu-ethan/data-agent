#!/usr/bin/env python3
"""Ping split SQLite control-plane files. Do not open MySQL."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_config import ROOT, load_config

EXPECTED = {
    "users": {"app_user", "user_permission"},
    "catalog": {
        "catalog_meta",
        "schema_table",
        "schema_column",
        "schema_relation",
        "metric_spec",
        "write_op",
    },
    "embeddings": {"embedding_manifest", "table_embedding", "column_embedding"},
    "runtime": {"thread"},
    "results": {"query_result"},
}
FORBIDDEN_IN_SQLITE = {
    "fact_order",
    "fact_order_item",
    "dim_sku",
    "da_write_receipt",
    "da_write_audit",
}
SLICE_TABLES = 12
SLICE_RELATIONS = 15
SLICE_METRICS = 10


def user_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def main() -> int:
    cfg = load_config()
    sqlite_cfg = cfg["sqlite"]
    names = ["users", "catalog", "embeddings", "checkpoint", "runtime", "results"]
    paths = {}
    for name in names:
        raw = Path(sqlite_cfg[name])
        paths[name] = raw if raw.is_absolute() else ROOT / raw

    for name, path in paths.items():
        if not path.exists():
            raise SystemExit(f"FAIL missing {path}; run python scripts/init_sqlite.py")
        # checkpoint.sqlite may be empty until SqliteSaver runs
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.execute("SELECT 1")
            found = user_tables(conn)
            leaked = sorted(found & FORBIDDEN_IN_SQLITE)
            if leaked:
                raise SystemExit(f"FAIL business tables in {path.name}: {leaked}")
            expected = EXPECTED.get(name)
            if expected and not expected <= found:
                raise SystemExit(f"FAIL {path.name} missing {sorted(expected - found)}")
            print(f"OK  {path.name} tables={sorted(found) or ['<empty, writable later>']}")
            if name == "catalog":
                n_tables = conn.execute("SELECT COUNT(*) FROM schema_table").fetchone()[0]
                n_rel = conn.execute("SELECT COUNT(*) FROM schema_relation").fetchone()[0]
                n_metric = conn.execute("SELECT COUNT(*) FROM metric_spec").fetchone()[0]
                if (n_tables, n_rel, n_metric) != (SLICE_TABLES, SLICE_RELATIONS, SLICE_METRICS):
                    raise SystemExit(
                        f"FAIL catalog counts tables={n_tables} rel={n_rel} metrics={n_metric} "
                        f"expected {SLICE_TABLES}/{SLICE_RELATIONS}/{SLICE_METRICS}"
                    )
                print(f"OK  catalog {n_tables} tables / {n_rel} edges / {n_metric} metrics")
            if name == "users":
                n = conn.execute("SELECT COUNT(*) FROM app_user").fetchone()[0]
                if n < 1:
                    raise SystemExit("FAIL users.sqlite has no app_user")
                print(f"OK  users rows={n}")
        finally:
            conn.close()

    mysql_dir = sqlite_cfg.get("dir", "./data/sqlite")
    if "mysql" in str(mysql_dir).lower() and "sqlite" not in str(mysql_dir).lower():
        raise SystemExit(f"FAIL sqlite.dir looks like MySQL path: {mysql_dir}")

    print("OK  sqlite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
