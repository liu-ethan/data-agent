#!/usr/bin/env python3
"""Collect MySQL metadata and atomically publish a four-layer Milvus index."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import load_settings
from backend.app.errors import RuntimeAgentError
from backend.app.repositories.catalog import MySQLCatalogRepository
from backend.app.repositories.catalog_index import CatalogIndexBuilder, MilvusCatalogIndex
from backend.app.repositories.runtime import RuntimePersistence, mysql_url
from backend.app.services.embedding import build_embedder
from backend.app.services.schema_catalog import MySQLSchemaCollector

BUSINESS_TABLES = [
    "shops", "users", "categories", "products", "orders", "order_items",
    "refunds", "refund_items",
]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect information_schema and publish a versioned Milvus catalog index")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--collect-only", action="store_true",
                      help="update MySQL catalog and lexical BM25 documents only")
    mode.add_argument("--index-only", action="store_true",
                      help="index the current authoritative MySQL catalog without collecting")
    parser.add_argument("--config", default="config.yaml")
    return parser.parse_args()


def source_config(settings) -> dict:
    configured = settings.raw.get("catalog", {}).get("source", {})
    business_database = settings.mysql.get("business_database") or settings.mysql.get("database")
    return {
        "database": business_database,
        "source_id": "mysql_ecommerce_local",
        "name": "Ecommerce MySQL",
        "domain": "ECOMMERCE_TRADE",
        "include_tables": BUSINESS_TABLES,
        "grain_overrides": {"users": "buyer"},
        **configured,
    }


def require_schema_rag_migration(persistence: RuntimePersistence) -> None:
    required = {"catalog_snapshots", "catalog_search_documents",
                "catalog_search_terms", "catalog_index_manifests"}
    present = set(inspect(persistence.engine).get_table_names())
    missing = sorted(required - present)
    if missing:
        raise RuntimeAgentError(
            "CATALOG_MIGRATION_REQUIRED",
            "apply migrations/005_system_schema_rag.sql before collecting or indexing",
            details={"missing_tables": missing})


async def main() -> int:
    args = arguments()
    settings = load_settings(args.config)
    milvus_config = settings.raw.get("milvus", {})
    if not args.collect_only and not milvus_config.get("enabled"):
        raise RuntimeAgentError("RAG_NOT_CONFIGURED",
                                "milvus.enabled must be true before indexing")
    # Collection/index publication is an administrative operation. The live
    # API uses agent_control and cannot mutate catalog authority.
    persistence = RuntimePersistence(settings.mysql, account_name="migration")
    require_schema_rag_migration(persistence)
    repository = MySQLCatalogRepository(persistence)
    source = source_config(settings)

    if args.index_only:
        version = repository.version([source["source_id"]])
    else:
        # INFORMATION_SCHEMA introspection targets the business database,
        # while catalog authority (catalog_objects, catalog_snapshots, etc.)
        # is persisted in the system database.
        business_url = mysql_url(settings.mysql, account_name="migration",
                                 database=source["database"])
        business_engine = create_engine(business_url, future=True)
        try:
            snapshot = MySQLSchemaCollector(business_engine, source).collect()
            snapshot = repository.synchronize(snapshot)
            version = snapshot.catalog_version
        finally:
            business_engine.dispose()
        print(f"collected source={snapshot.source_id} objects={len(snapshot.objects)} "
              f"fields={len(snapshot.fields)} relations={len(snapshot.relations)} "
              f"catalog_version={version}")
    if args.collect_only:
        return 0

    embedder = build_embedder(settings.raw.get("llm", {}))
    index = MilvusCatalogIndex(milvus_config)
    try:
        manifest = await CatalogIndexBuilder(
            repository, index, embedder, milvus_config).build(version)
        print(f"activated manifest={manifest.manifest_id} "
              f"catalog_version={manifest.catalog_version} "
              f"index_version={manifest.index_version} "
              f"embedding={manifest.embedding_provider}/{manifest.embedding_model} "
              f"dimension={manifest.embedding_dimension} "
              f"counts={manifest.document_counts}")
    finally:
        await embedder.aclose()
        index.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except RuntimeAgentError as exc:
        print(f"ERROR {exc.error_code}: {exc.message}; details={exc.details}", file=sys.stderr)
        raise SystemExit(2) from exc
