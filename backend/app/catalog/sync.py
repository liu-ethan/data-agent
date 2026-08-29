from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import Engine

from backend.app.config import load_settings
from backend.app.mysql.pool import get_engine
from backend.app.resources.domain import BUSINESS_TABLES
from backend.app.resources.sql import load_sql, mysql_text


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
        max_version = conn.execute(load_sql("catalog.select_max_catalog_version")).fetchone()[0]
        new_version = int(max_version or 0) + 1
        for table in tables:
            name = str(table["table_name"])
            comment = table.get("comment") or None
            exists = conn.execute(
                load_sql("catalog.schema_table_exists"), (name,)
            ).fetchone()
            if exists:
                conn.execute(
                    load_sql("catalog.update_schema_table_comment"),
                    (comment, name),
                )
            else:
                conn.execute(
                    load_sql("catalog.insert_schema_table"),
                    (name, name, "", comment or "", comment),
                )
        conn.execute(load_sql("catalog.delete_schema_columns"))
        for column in columns:
            conn.execute(
                load_sql("catalog.insert_schema_column"),
                (
                    column["table_name"],
                    column["column_name"],
                    column["data_type"],
                    column.get("comment") or None,
                ),
            )
        conn.execute(load_sql("catalog.delete_schema_relations"))
        for relation_id, fk in enumerate(foreign_keys, start=1):
            conn.execute(
                load_sql("catalog.insert_schema_relation"),
                (
                    relation_id,
                    fk["left_table"],
                    fk["left_col"],
                    fk["right_table"],
                    fk["right_col"],
                ),
            )
        conn.execute(
            load_sql("catalog.insert_catalog_meta"),
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
        tables = _as_dicts(
            conn.execute(mysql_text("catalog.mysql_information_schema_tables", expanding=("tables",)), params)
        )
        columns = _as_dicts(
            conn.execute(mysql_text("catalog.mysql_information_schema_columns", expanding=("tables",)), params)
        )
        foreign_keys = _as_dicts(
            conn.execute(mysql_text("catalog.mysql_information_schema_fks", expanding=("tables",)), params)
        )
    return apply_information_schema(
        path,
        tables=tables,
        columns=columns,
        foreign_keys=foreign_keys,
        mysql_database=mysql_database,
    )


def ensure_physical_schema(
    *,
    catalog_db: str | Path | None = None,
    engine: Engine | None = None,
) -> int:
    """Sync table/column/FK rows from MySQL when Catalog has no columns. No-op otherwise."""
    from backend.app.catalog.store import CatalogStore

    store = CatalogStore(catalog_db)
    snap = store.load()
    if snap.columns:
        return snap.catalog_version
    return sync_from_mysql(catalog_db=store.path, engine=engine)
