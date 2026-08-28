from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.app.catalog.models import (
    CatalogSnapshot,
    MetricSpec,
    SchemaColumn,
    SchemaTable,
    TableRelation,
    WriteOpSpec,
)
from backend.app.config import load_settings
from backend.app.types import FilterCond

_REVIEWED_SOURCES = frozenset({"fk", "human"})


def _catalog_path(catalog_db: str | Path | None) -> Path:
    if catalog_db is not None:
        return Path(catalog_db)
    return Path(load_settings().sqlite.catalog)


def _metric_from_row(row: sqlite3.Row) -> MetricSpec:
    filters = [FilterCond.model_validate(item) for item in json.loads(row["filters_json"])]
    return MetricSpec(
        metric_id=row["metric_id"],
        name=row["name"],
        version=int(row["version"]),
        grain_table=row["grain_table"],
        formula=row["formula"],
        time_field=row["time_field"],
        unit=row["unit"],
        filters=filters,
        deps=json.loads(row["deps_json"]),
        needs_tables=json.loads(row["needs_tables_json"]),
    )


def _relation_from_row(row: sqlite3.Row) -> TableRelation:
    source = row["source"]
    if source not in _REVIEWED_SOURCES:
        raise ValueError(f"unsupported relation source: {source!r}")
    return TableRelation(
        left_table=row["left_table"],
        right_table=row["right_table"],
        left_col=row["left_col"],
        right_col=row["right_col"],
        cardinality=row["cardinality"],
        source=source,
        version=int(row["version"]),
    )


class CatalogStore:
    def __init__(self, catalog_db: str | Path | None = None) -> None:
        self.path = _catalog_path(catalog_db)

    def load(self) -> CatalogSnapshot:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            version_row = conn.execute(
                "SELECT MAX(catalog_version) AS catalog_version FROM catalog_meta"
            ).fetchone()
            catalog_version = int(version_row["catalog_version"] or 0)
            tables = [
                SchemaTable(
                    table_name=row["table_name"],
                    business_name=row["business_name"],
                    domain=row["domain"],
                    grain_description=row["grain_description"],
                    comment=row["comment"],
                    aliases=json.loads(row["aliases_json"]),
                )
                for row in conn.execute("SELECT * FROM schema_table ORDER BY table_name")
            ]
            columns = [
                SchemaColumn(
                    table_name=row["table_name"],
                    column_name=row["column_name"],
                    data_type=row["data_type"],
                    comment=row["comment"],
                    aliases=json.loads(row["aliases_json"]),
                    is_sensitive=bool(row["is_sensitive"]),
                )
                for row in conn.execute(
                    "SELECT * FROM schema_column ORDER BY table_name, column_name"
                )
            ]
            relations = [
                _relation_from_row(row)
                for row in conn.execute(
                    """
                    SELECT left_table, right_table, left_col, right_col,
                           cardinality, source, version
                    FROM schema_relation
                    WHERE reviewed = 1 AND source IN ('fk', 'human')
                    ORDER BY relation_id
                    """
                )
            ]
            metrics = [
                _metric_from_row(row)
                for row in conn.execute("SELECT * FROM metric_spec ORDER BY metric_id")
            ]
            write_ops = [
                WriteOpSpec(
                    operation_type=row["operation_type"],
                    target_table=row["target_table"],
                    allowed_columns=json.loads(row["allowed_columns_json"]),
                    sql_template=row["sql_template"],
                    max_affected_rows=int(row["max_affected_rows"]),
                    requires_hitl=bool(row["requires_hitl"]),
                    version_predicate=row["version_predicate"],
                )
                for row in conn.execute("SELECT * FROM write_op ORDER BY operation_type")
            ]
        return CatalogSnapshot(
            catalog_version=catalog_version,
            tables=tables,
            columns=columns,
            relations=relations,
            metrics=metrics,
            write_ops=write_ops,
        )

    def get_metric(self, metric_id: str) -> MetricSpec:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM metric_spec WHERE metric_id = ?",
                (metric_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"unknown metric_id: {metric_id}")
        return _metric_from_row(row)

    def list_reviewed_edges(self) -> list[TableRelation]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT left_table, right_table, left_col, right_col,
                       cardinality, source, version
                FROM schema_relation
                WHERE reviewed = 1 AND source IN ('fk', 'human')
                ORDER BY relation_id
                """
            ).fetchall()
        return [_relation_from_row(row) for row in rows]


def list_reviewed_edges(*, catalog_db: str | Path | None = None) -> list[TableRelation]:
    return CatalogStore(catalog_db).list_reviewed_edges()
