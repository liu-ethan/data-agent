"""Initialize split SQLite control-plane databases. Never writes to MySQL."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.resources.domain import (  # noqa: E402
    ALL_METRICS,
    ALL_TABLES,
    METRICS,
    RELATIONS,
    SLICE_TABLES,
    TENANT_ID,
    load_write_ops_raw,
    mysql_database,
    pbkdf2_iterations,
)
from backend.app.resources.sql import SQLITE_DDL_DIR, apply_sql, load_sql  # noqa: E402

SQL_DIR = SQLITE_DDL_DIR
DATA_DIR = ROOT / "data" / "sqlite"
TENANT = TENANT_ID
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

FILES = {
    "users": "users.sql",
    "catalog": "catalog.sql",
    "embeddings": "embeddings.sql",
    "checkpoint": "checkpoint.sql",
    "runtime": "runtime.sql",
    "results": "results.sql",
}


def password_hash(password: str, salt: str = "local-dev-salt") -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), pbkdf2_iterations()
    )
    return f"{salt}${digest.hex()}"


def seed_users(db_path: Path) -> None:
    users = [
        ("u-admin", "admin", "admin", "本地管理员", "operator"),
        ("u-analyst", "analyst", "analyst", "分析师", "analyst"),
    ]
    db = mysql_database()
    ops = list(item["operation_type"] for item in load_write_ops_raw())
    with sqlite3.connect(db_path) as conn:
        conn.execute(load_sql("seed.seed_delete_user_permission"))
        conn.execute(load_sql("seed.seed_delete_app_user"))
        for user_id, username, password, display_name, role in users:
            conn.execute(
                load_sql("seed.seed_insert_app_user"),
                (user_id, username, password_hash(password), display_name, role, TENANT, NOW),
            )
            write_ops = ops if role == "operator" else []
            conn.execute(
                load_sql("seed.seed_insert_user_permission"),
                (
                    user_id,
                    json.dumps(ALL_TABLES),
                    json.dumps([f"{db}.{t}.*" for t in ALL_TABLES]),
                    json.dumps(ALL_METRICS),
                    json.dumps(write_ops),
                    NOW,
                ),
            )
        conn.commit()


def seed_catalog(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        for name in (
            "seed_delete_write_op",
            "seed_delete_metric_spec",
            "seed_delete_schema_relation",
            "seed_delete_schema_column",
            "seed_delete_schema_table",
            "seed_delete_catalog_meta",
        ):
            conn.execute(load_sql(f"seed.{name}"))
        conn.execute(
            load_sql("seed.seed_insert_catalog_meta"),
            (mysql_database(), NOW, "catalog seed"),
        )
        for table_name, business_name, domain, grain in SLICE_TABLES:
            conn.execute(
                load_sql("seed.seed_insert_schema_table"),
                (table_name, business_name, domain, grain, grain),
            )
        for i, rel in enumerate(RELATIONS, start=1):
            conn.execute(load_sql("seed.seed_insert_schema_relation"), (i, *rel))
        for metric in METRICS:
            conn.execute(
                load_sql("seed.seed_insert_metric_spec"),
                (
                    metric["metric_id"],
                    metric["name"],
                    metric["version"],
                    metric["grain_table"],
                    metric["formula"],
                    metric["time_field"],
                    metric["unit"],
                    json.dumps(metric["filters"], ensure_ascii=False),
                    json.dumps(metric["deps"]),
                    json.dumps(metric["needs_tables"]),
                ),
            )
        for item in load_write_ops_raw():
            conn.execute(
                load_sql("seed.seed_insert_write_op"),
                (
                    item["operation_type"],
                    item["target_table"],
                    json.dumps(item["allowed_columns"]),
                    item["sql_template"],
                    int(item.get("max_affected_rows") or 100),
                    1 if item.get("must_hitl", True) else 0,
                    item.get("version_predicate") or "row_version",
                ),
            )
        conn.commit()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "results").mkdir(parents=True, exist_ok=True)
    for name, sql_file in FILES.items():
        db_path = DATA_DIR / f"{name}.sqlite"
        apply_sql(db_path, SQL_DIR / sql_file)
        print(f"init {db_path}")
    seed_users(DATA_DIR / "users.sqlite")
    seed_catalog(DATA_DIR / "catalog.sqlite")
    print("seeded users.sqlite and catalog.sqlite")


if __name__ == "__main__":
    main()
