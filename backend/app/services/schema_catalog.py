"""Physical MySQL schema collection and versioned search documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..errors import RuntimeAgentError


@dataclass(frozen=True)
class SchemaField:
    table_name: str
    field_name: str
    ordinal: int
    data_type: str
    column_type: str
    nullable: bool
    column_key: str
    comment: str
    classification: str
    is_time_field: bool


@dataclass(frozen=True)
class SchemaObject:
    name: str
    object_type: str
    grain: str
    comment: str


@dataclass(frozen=True)
class SchemaRelation:
    name: str
    left_table: str
    left_field: str
    right_table: str
    right_field: str
    cardinality: str


@dataclass(frozen=True)
class CatalogSnapshot:
    source_id: str
    source_name: str
    domain: str
    database: str
    objects: tuple[SchemaObject, ...]
    fields: tuple[SchemaField, ...]
    relations: tuple[SchemaRelation, ...]
    catalog_version: str

    def with_version(self, version: str) -> "CatalogSnapshot":
        return replace(self, catalog_version=version)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "domain": self.domain,
            "database": self.database,
            "objects": [asdict(item) for item in self.objects],
            "fields": [asdict(item) for item in self.fields],
            "relations": [asdict(item) for item in self.relations],
        }


@dataclass(frozen=True)
class SearchDocument:
    document_id: str
    layer: str
    kind: str
    target_id: str
    source_id: str
    object_id: str
    catalog_version: str
    text: str
    token_count: int
    classification: str = "BUSINESS"


@dataclass(frozen=True)
class IndexManifest:
    manifest_id: str
    catalog_version: str
    index_version: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    collections: dict[str, str]
    document_counts: dict[str, int]


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def content_version(payload: object) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "catalog_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*|\d+|[\u4e00-\u9fff]+")


def lexical_tokens(value: str) -> list[str]:
    """Stable English/identifier and Chinese unigram+bigram tokenization."""
    output: list[str] = []
    for match in _WORD.findall(value.lower().replace("_", " ")):
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            output.extend(match)
            output.extend(match[index:index + 2] for index in range(len(match) - 1))
        else:
            output.append(match)
    return [item for item in output if item]


def classify_field(field_name: str, data_type: str,
                   overrides: dict[str, str] | None = None) -> tuple[str, bool]:
    override = (overrides or {}).get(field_name) or (overrides or {}).get(field_name.lower())
    lowered, dtype = field_name.lower(), data_type.lower()
    is_time = dtype in {"date", "datetime", "timestamp", "time"}
    if override:
        return override, is_time
    if lowered in {"phone", "mobile", "mobile_phone"}:
        return "PHONE", is_time
    if lowered in {"id_number", "identity_number", "national_id"}:
        return "ID_CARD", is_time
    if is_time:
        return "BUSINESS_TIME", True
    if lowered.endswith("_id") or lowered == "id":
        return "IDENTIFIER", False
    if "status" in lowered or lowered in {"state", "type"}:
        return "STATUS", False
    if any(term in lowered for term in ("amount", "price", "revenue", "cost")):
        return "AMOUNT", False
    if dtype in {"int", "integer", "bigint", "smallint", "decimal", "numeric", "float", "double"}:
        return "MEASURE", False
    return "BUSINESS", False


class MySQLSchemaCollector:
    """Collect an allowlisted physical schema from MySQL information_schema."""

    def __init__(self, engine: Engine, config: dict[str, Any]) -> None:
        self.engine = engine
        self.database = str(config.get("database") or engine.url.database or "")
        self.source_id = str(config.get("source_id") or f"mysql_{self.database}")
        self.source_name = str(config.get("name") or self.database)
        self.domain = str(config.get("domain") or "UNCLASSIFIED")
        self.include_tables = tuple(str(item) for item in config.get("include_tables", []))
        self.exclude_tables = tuple(str(item) for item in config.get("exclude_tables", []))
        self.grain_overrides = {str(k): str(v) for k, v in
                                config.get("grain_overrides", {}).items()}
        self.classification_overrides = {
            str(k).lower(): str(v) for k, v in
            config.get("classification_overrides", {}).items()}
        if not self.database or not self.source_id:
            raise RuntimeAgentError("CATALOG_COLLECTION_FAILED",
                                    "catalog source database and source_id are required")

    def collect(self) -> CatalogSnapshot:
        params = {"database": self.database}
        with self.engine.connect() as connection:
            object_rows = connection.execute(text("""
                SELECT TABLE_NAME,TABLE_TYPE,COALESCE(TABLE_COMMENT,'') AS TABLE_COMMENT
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA=:database
                ORDER BY TABLE_NAME
            """), params).mappings().all()
            field_rows = connection.execute(text("""
                SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,DATA_TYPE,COLUMN_TYPE,
                       IS_NULLABLE,COLUMN_KEY,COALESCE(COLUMN_COMMENT,'') AS COLUMN_COMMENT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=:database
                ORDER BY TABLE_NAME,ORDINAL_POSITION
            """), params).mappings().all()
            relation_rows = connection.execute(text("""
                SELECT CONSTRAINT_NAME,TABLE_NAME,COLUMN_NAME,
                       REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA=:database AND REFERENCED_TABLE_NAME IS NOT NULL
                ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION
            """), params).mappings().all()

        names = {str(row["TABLE_NAME"]) for row in object_rows}
        if self.include_tables:
            missing = set(self.include_tables) - names
            if missing:
                raise RuntimeAgentError(
                    "CATALOG_COLLECTION_FAILED", "configured catalog tables do not exist",
                    details={"missing_tables": sorted(missing)})
            selected = set(self.include_tables)
        else:
            selected = names - set(self.exclude_tables)
        if not selected:
            raise RuntimeAgentError("CATALOG_COLLECTION_FAILED",
                                    "catalog source contains no selected objects")

        objects = tuple(
            SchemaObject(
                name=str(row["TABLE_NAME"]),
                object_type="VIEW" if "VIEW" in str(row["TABLE_TYPE"]).upper() else "TABLE",
                grain=self.grain_overrides.get(
                    str(row["TABLE_NAME"]), self._default_grain(str(row["TABLE_NAME"]))),
                comment=str(row["TABLE_COMMENT"] or ""),
            )
            for row in object_rows if str(row["TABLE_NAME"]) in selected
        )
        fields: list[SchemaField] = []
        for row in field_rows:
            table_name, field_name = str(row["TABLE_NAME"]), str(row["COLUMN_NAME"])
            if table_name not in selected:
                continue
            override = (self.classification_overrides.get(f"{table_name}.{field_name}".lower())
                        or self.classification_overrides.get(field_name.lower()))
            classification, is_time = classify_field(
                field_name, str(row["DATA_TYPE"]), {field_name: override} if override else None)
            fields.append(SchemaField(
                table_name=table_name, field_name=field_name,
                ordinal=int(row["ORDINAL_POSITION"]), data_type=str(row["DATA_TYPE"]).upper(),
                column_type=str(row["COLUMN_TYPE"]),
                nullable=str(row["IS_NULLABLE"]).upper() == "YES",
                column_key=str(row["COLUMN_KEY"] or ""),
                comment=str(row["COLUMN_COMMENT"] or ""),
                classification=classification, is_time_field=is_time,
            ))
        relations = tuple(
            SchemaRelation(
                name=str(row["CONSTRAINT_NAME"]),
                left_table=str(row["TABLE_NAME"]), left_field=str(row["COLUMN_NAME"]),
                right_table=str(row["REFERENCED_TABLE_NAME"]),
                right_field=str(row["REFERENCED_COLUMN_NAME"]),
                cardinality="many_to_one",
            )
            for row in relation_rows
            if str(row["TABLE_NAME"]) in selected
            and str(row["REFERENCED_TABLE_NAME"]) in selected
        )
        provisional = CatalogSnapshot(
            source_id=self.source_id, source_name=self.source_name, domain=self.domain,
            database=self.database, objects=objects, fields=tuple(fields), relations=relations,
            catalog_version="catalog_pending")
        return provisional.with_version(content_version(provisional.canonical_payload()))

    @staticmethod
    def _default_grain(table_name: str) -> str:
        return table_name[:-3] + "y" if table_name.endswith("ies") else (
            table_name[:-1] if table_name.endswith("s") else table_name)


def token_frequencies(tokens: Iterable[str]) -> dict[str, int]:
    output: dict[str, int] = {}
    for item in tokens:
        output[item] = output.get(item, 0) + 1
    return output
