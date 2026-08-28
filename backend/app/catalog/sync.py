from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from backend.app.config import load_settings
from backend.app.mysql.pool import get_engine

BUSINESS_TABLES = frozenset(
    {
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
    }
)

_TABLES_SQL = text(
    """
    SELECT TABLE_NAME AS table_name, TABLE_COMMENT AS comment
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = :db AND TABLE_TYPE = 'BASE TABLE'
      AND TABLE_NAME IN :tables
    """
).bindparams(bindparam("tables", expanding=True))

_COLUMNS_SQL = text(
    """
    SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name,
           DATA_TYPE AS data_type, COLUMN_COMMENT AS comment
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = :db AND TABLE_NAME IN :tables
    ORDER BY TABLE_NAME, ORDINAL_POSITION
    """
).bindparams(bindparam("tables", expanding=True))

_FK_SQL = text(
    """
    SELECT TABLE_NAME AS left_table, COLUMN_NAME AS left_col,
           REFERENCED_TABLE_NAME AS right_table,
           REFERENCED_COLUMN_NAME AS right_col
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = :db
      AND REFERENCED_TABLE_NAME IS NOT NULL
      AND TABLE_NAME IN :tables
      AND REFERENCED_TABLE_NAME IN :tables
    """
).bindparams(bindparam("tables", expanding=True))


def _as_dicts(result) -> list[dict[str, object]]:
    return [dict(row._mapping) for row in result]


def apply_information_schema(
    catalog_db: str | Path,
    *,
    tables: list[dict[str, object]],
    columns: list[dict[str, object]],
    foreign_keys: list[dict[str, object]],
    mysql_database: str,
) -> int:
    """Write physical table/column/FK rows into SQLite Catalog. Never touch metrics or write_ops."""
    path = Path(catalog_db)
    tables = [t for t in tables if t["table_name"] in BUSINESS_TABLES]
    columns = [c for c in columns if c["table_name"] in BUSINESS_TABLES]
    foreign_keys = [
        fk
        for fk in foreign_keys
        if fk["left_table"] in BUSINESS_TABLES and fk["right_table"] in BUSINESS_TABLES
    ]
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    with sqlite3.connect(path) as conn:
        max_version = conn.execute("SELECT MAX(catalog_version) FROM catalog_meta").fetchone()[0]
        new_version = int(max_version or 0) + 1
        for table in tables:
            name = str(table["table_name"])
            comment = table.get("comment") or None
            exists = conn.execute(
                "SELECT 1 FROM schema_table WHERE table_name = ?", (name,)
            ).fetchone()
            if exists:
                conn.execute(
                    "UPDATE schema_table SET comment = ? WHERE table_name = ?",
                    (comment, name),
                )
            else:
                conn.execute(
                    "INSERT INTO schema_table VALUES (?, ?, ?, ?, ?, '[]')",
                    (name, name, "", comment or "", comment),
                )
        conn.execute("DELETE FROM schema_column")
        for column in columns:
            conn.execute(
                "INSERT INTO schema_column VALUES (?, ?, ?, ?, '[]', 0)",
                (
                    column["table_name"],
                    column["column_name"],
                    column["data_type"],
                    column.get("comment") or None,
                ),
            )
        conn.execute("DELETE FROM schema_relation")
        for relation_id, fk in enumerate(foreign_keys, start=1):
            conn.execute(
                "INSERT INTO schema_relation VALUES (?, ?, ?, ?, ?, 'many_to_one', 'fk', 1, 1)",
                (
                    relation_id,
                    fk["left_table"],
                    fk["left_col"],
                    fk["right_table"],
                    fk["right_col"],
                ),
            )
        conn.execute(
            "INSERT INTO catalog_meta VALUES (?, ?, ?, ?)",
            (new_version, mysql_database, now, "sync_from_mysql"),
        )
        conn.commit()
    return new_version


def sync_from_mysql(
    *,
    catalog_db: str | Path | None = None,
    engine: Engine | None = None,
) -> int:
    """Read INFORMATION_SCHEMA + comments into SQLite Catalog. Does not write MySQL."""
    settings = load_settings()
    path = Path(catalog_db) if catalog_db is not None else Path(settings.sqlite.catalog)
    mysql_database = settings.mysql.database
    eng = engine or get_engine("reader")
    table_names = sorted(BUSINESS_TABLES)
    params = {"db": mysql_database, "tables": table_names}
    with eng.connect() as conn:
        tables = _as_dicts(conn.execute(_TABLES_SQL, params))
        columns = _as_dicts(conn.execute(_COLUMNS_SQL, params))
        foreign_keys = _as_dicts(conn.execute(_FK_SQL, params))
    return apply_information_schema(
        path,
        tables=tables,
        columns=columns,
        foreign_keys=foreign_keys,
        mysql_database=mysql_database,
    )
