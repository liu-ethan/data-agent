from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.app.types import FilterCond

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one"]
RelationSource = Literal["fk", "human"]


class SchemaTable(BaseModel):
    table_name: str
    business_name: str
    domain: str
    grain_description: str
    comment: str | None = None
    aliases: list[str] = []


class SchemaColumn(BaseModel):
    table_name: str
    column_name: str
    data_type: str
    comment: str | None = None
    aliases: list[str] = []
    is_sensitive: bool = False


class TableRelation(BaseModel):
    left_table: str
    right_table: str
    left_col: str
    right_col: str
    cardinality: Cardinality
    source: RelationSource
    version: int


class MetricSpec(BaseModel):
    metric_id: str
    name: str
    version: int
    grain_table: str
    formula: str
    time_field: str
    unit: str
    filters: list[FilterCond]
    deps: list[str]
    needs_tables: list[str] = []


class WriteOpSpec(BaseModel):
    operation_type: str
    target_table: str
    allowed_columns: list[str]
    sql_template: str
    max_affected_rows: int
    requires_hitl: bool
    version_predicate: str


class CatalogSnapshot(BaseModel):
    catalog_version: int
    tables: list[SchemaTable]
    columns: list[SchemaColumn]
    relations: list[TableRelation]
    metrics: list[MetricSpec]
    write_ops: list[WriteOpSpec]
