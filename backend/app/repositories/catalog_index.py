"""Versioned Milvus dense index and validated staging builder."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from ..errors import RuntimeAgentError
from ..services.schema_catalog import IndexManifest, SearchDocument
from .catalog import MySQLCatalogRepository

LAYERS = ("source_domain", "object", "field_entity", "relation")


def _safe_collection_name(base: str, suffix: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", f"{base}__{suffix}")
    if not value or not value[0].isalpha() and value[0] != "_":
        value = "catalog_" + value
    return value[:255]


class MilvusCatalogIndex:
    """Milvus Lite/standalone adapter; active names come from MySQL manifest."""

    def __init__(self, config: dict[str, Any], *, client: Any | None = None) -> None:
        if not config.get("enabled"):
            raise RuntimeAgentError("RAG_NOT_CONFIGURED", "Milvus index is disabled")
        self.config = config
        if client is not None:
            self.client = client
            return
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise RuntimeAgentError("RAG_NOT_CONFIGURED", "pymilvus is unavailable") from exc
        try:
            self.client = MilvusClient(
                uri=config["uri"], token=config.get("token") or None,
                db_name=config.get("database", "default"))
        except Exception as exc:
            raise RuntimeAgentError(
                "RAG_CONNECTION_FAILED", "Milvus connection could not be established",
                details={"error_type": type(exc).__name__}) from exc

    def validate_manifest(self, manifest: IndexManifest) -> None:
        if set(manifest.collections) != set(LAYERS):
            raise RuntimeAgentError("RAG_INDEX_INVALID",
                                    "index manifest does not contain all catalog layers")
        for layer, collection in manifest.collections.items():
            try:
                if not self.client.has_collection(collection):
                    raise RuntimeAgentError(
                        "RAG_INDEX_MISSING", f"Milvus {layer} collection is missing")
                description = self.client.describe_collection(collection)
                required_fields = {
                    "document_id", "target_id", "source_id", "object_id",
                    "catalog_version", "document_kind", "classification", "text", "vector"}
                actual_fields = {str(field.get("name"))
                                 for field in description.get("fields", [])}
                if not required_fields.issubset(actual_fields):
                    raise RuntimeAgentError(
                        "RAG_INDEX_INVALID", "Milvus collection schema is incomplete",
                        details={"layer": layer,
                                 "missing_fields": sorted(required_fields - actual_fields)})
                vector = next((field for field in description.get("fields", [])
                               if field.get("name") == "vector"), None)
                dimension = int((vector or {}).get("params", {}).get("dim", 0))
                if dimension != manifest.embedding_dimension:
                    raise RuntimeAgentError(
                        "RAG_INDEX_INVALID", "Milvus embedding dimension does not match manifest")
                count = int(self.client.get_collection_stats(collection).get("row_count", 0))
                if count != int(manifest.document_counts.get(layer, -1)):
                    raise RuntimeAgentError(
                        "RAG_INDEX_INVALID", "Milvus document count does not match manifest",
                        details={"layer": layer, "expected": manifest.document_counts.get(layer),
                                 "actual": count})
                self.client.load_collection(collection_name=collection)
            except RuntimeAgentError:
                raise
            except Exception as exc:
                raise RuntimeAgentError(
                    "RAG_INDEX_INVALID", "Milvus manifest validation failed",
                    details={"layer": layer, "error_type": type(exc).__name__}) from exc

    def search(self, manifest: IndexManifest, *, layer: str, embedding: list[float],
               source_ids: list[str], limit: int, object_ids: list[str] | None = None,
               kinds: list[str] | None = None,
               denied_classifications: list[str] | None = None) -> list[dict[str, Any]]:
        if layer not in manifest.collections:
            raise RuntimeAgentError("RAG_INDEX_INVALID", "unknown catalog index layer")
        if len(embedding) != manifest.embedding_dimension:
            raise RuntimeAgentError(
                "RAG_EMBEDDING_DIMENSION_MISMATCH",
                "query embedding dimension does not match active index",
                details={"expected": manifest.embedding_dimension, "actual": len(embedding)})
        if not source_ids:
            return []
        filters = ["source_id in [" + ",".join(json.dumps(item) for item in source_ids) + "]",
                   f"catalog_version == {json.dumps(manifest.catalog_version)}"]
        if object_ids:
            filters.append("object_id in [" + ",".join(
                json.dumps(item) for item in object_ids) + "]")
        if kinds:
            filters.append("document_kind in [" + ",".join(
                json.dumps(item) for item in kinds) + "]")
        if denied_classifications:
            filters.append("classification not in [" + ",".join(
                json.dumps(item) for item in denied_classifications) + "]")
        try:
            result = self.client.search(
                collection_name=manifest.collections[layer], data=[embedding],
                filter=" and ".join(filters), limit=limit, anns_field="vector",
                search_params={"metric_type": "COSINE", "params": {}},
                output_fields=["target_id", "source_id", "object_id",
                               "catalog_version", "document_kind"])
        except Exception as exc:
            raise RuntimeAgentError(
                "RAG_SEARCH_FAILED", "Milvus catalog search failed",
                details={"layer": layer, "error_type": type(exc).__name__}) from exc
        hits: list[dict[str, Any]] = []
        for hit in result[0] if result else []:
            entity = hit.get("entity") or {}
            if str(entity.get("catalog_version")) != manifest.catalog_version:
                raise RuntimeAgentError(
                    "CATALOG_VERSION_MISMATCH", "Milvus returned a stale catalog document")
            similarity = float(hit.get("distance", hit.get("score", 0)))
            hits.append({
                "target_id": str(entity.get("target_id")),
                "source_id": str(entity.get("source_id")),
                "object_id": str(entity.get("object_id") or ""),
                "kind": str(entity.get("document_kind")),
                "score": round(max(0.0, min(1.0, similarity)), 6),
            })
        return hits

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            return


class CatalogIndexBuilder:
    """Build four physical staging collections, verify, then switch manifest."""

    def __init__(self, repository: MySQLCatalogRepository, index: MilvusCatalogIndex,
                 embedder: Any, config: dict[str, Any]) -> None:
        self.repository, self.index, self.embedder = repository, index, embedder
        self.base_names = config.get("collections", {})
        self.batch_size = max(1, int(config.get("insert_batch_size", 256)))

    async def build(self, catalog_version: str) -> IndexManifest:
        documents = self.repository.documents(catalog_version)
        grouped: dict[str, list[SearchDocument]] = defaultdict(list)
        for document in documents:
            grouped[document.layer].append(document)
        missing = [layer for layer in LAYERS if not grouped[layer]]
        if missing:
            raise RuntimeAgentError(
                "RAG_INDEX_INVALID", "catalog has no documents for required layers",
                details={"missing_layers": missing})

        provider = str(getattr(self.embedder, "provider", "unknown"))
        model = str(getattr(self.embedder, "model_name", "unknown"))
        digest = hashlib.sha256(json.dumps({
            "catalog_version": catalog_version, "provider": provider, "model": model,
            "documents": [(item.document_id, item.text, item.classification)
                          for item in documents],
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        index_version = f"index_{digest}"
        suffix = f"{catalog_version.removeprefix('catalog_')[:12]}_{digest[:8]}"
        collections = {layer: _safe_collection_name(
            str(self.base_names.get(layer, f"catalog_{layer}")), suffix)
            for layer in LAYERS}
        active_collections: set[str] = set()
        active_manifest: IndexManifest | None = None
        try:
            active_manifest = self.repository.active_manifest()
            active_collections = set(active_manifest.collections.values())
        except RuntimeAgentError as exc:
            if exc.error_code != "RAG_INDEX_MISSING":
                raise
        counts = {layer: len(grouped[layer]) for layer in LAYERS}
        if (active_manifest
                and active_manifest.catalog_version == catalog_version
                and active_manifest.index_version == index_version
                and active_manifest.embedding_provider == provider
                and active_manifest.embedding_model == model
                and active_manifest.collections == collections
                and active_manifest.document_counts == counts):
            try:
                self.index.validate_manifest(active_manifest)
                return active_manifest
            except RuntimeAgentError as exc:
                if exc.error_code not in {"RAG_INDEX_MISSING", "RAG_INDEX_INVALID"}:
                    raise
                # MySQL still points at a digest-identical index, but the
                # vector store was deleted or drifted. Rebuild in place.
                active_collections = {
                    name for name in active_collections
                    if self.index.client.has_collection(name)
                }

        created: list[str] = []
        dimension = 0
        try:
            for layer in LAYERS:
                docs = grouped[layer]
                vectors = await self.embedder.embed_documents([item.text for item in docs])
                if not vectors or len(vectors) != len(docs):
                    raise RuntimeAgentError(
                        "RAG_EMBEDDING_FAILED", "embedding count does not match documents")
                current_dimension = len(vectors[0])
                if dimension and current_dimension != dimension:
                    raise RuntimeAgentError(
                        "RAG_EMBEDDING_DIMENSION_MISMATCH",
                        "embedding model returned inconsistent dimensions")
                dimension = current_dimension
                collection = collections[layer]
                if self.index.client.has_collection(collection):
                    if collection in active_collections:
                        raise RuntimeAgentError(
                            "RAG_INDEX_INVALID", "staging name collides with active collection")
                    self.index.client.drop_collection(collection)
                self._create_collection(collection, dimension)
                created.append(collection)
                records = [{
                    "document_id": document.document_id,
                    "target_id": document.target_id,
                    "source_id": document.source_id,
                    "object_id": document.object_id,
                    "catalog_version": document.catalog_version,
                    "document_kind": document.kind,
                    "classification": document.classification,
                    "text": document.text,
                    "vector": vector,
                } for document, vector in zip(docs, vectors, strict=True)]
                for offset in range(0, len(records), self.batch_size):
                    self.index.client.insert(
                        collection_name=collection,
                        data=records[offset:offset + self.batch_size])
                self.index.client.flush(collection_name=collection)
                self.index.client.load_collection(collection_name=collection)
                actual = int(self.index.client.get_collection_stats(collection).get(
                    "row_count", 0))
                if actual != len(records):
                    raise RuntimeAgentError(
                        "RAG_INDEX_INVALID", "staging collection row count mismatch",
                        details={"layer": layer, "expected": len(records), "actual": actual})
            staging_manifest = IndexManifest(
                manifest_id="staging", catalog_version=catalog_version,
                index_version=index_version, embedding_provider=provider,
                embedding_model=model, embedding_dimension=dimension,
                collections=collections, document_counts=counts)
            self.index.validate_manifest(staging_manifest)
            return self.repository.activate_manifest(
                catalog_version=catalog_version, index_version=index_version,
                embedding_provider=provider, embedding_model=model,
                embedding_dimension=dimension, collections=collections,
                document_counts=counts)
        except Exception:
            for collection in created:
                if collection not in active_collections:
                    try:
                        self.index.client.drop_collection(collection)
                    except Exception:
                        pass
            raise

    def _create_collection(self, collection: str, dimension: int) -> None:
        try:
            from pymilvus import DataType, MilvusClient
        except ImportError as exc:
            raise RuntimeAgentError("RAG_NOT_CONFIGURED", "pymilvus is unavailable") from exc
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("document_id", DataType.VARCHAR, is_primary=True, max_length=160)
        schema.add_field("target_id", DataType.VARCHAR, max_length=160)
        schema.add_field("source_id", DataType.VARCHAR, max_length=128)
        schema.add_field("object_id", DataType.VARCHAR, max_length=128)
        schema.add_field("catalog_version", DataType.VARCHAR, max_length=64)
        schema.add_field("document_kind", DataType.VARCHAR, max_length=32)
        schema.add_field("classification", DataType.VARCHAR, max_length=64)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dimension)
        indexes = self.index.client.prepare_index_params()
        indexes.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
        self.index.client.create_collection(
            collection_name=collection, schema=schema, index_params=indexes)
