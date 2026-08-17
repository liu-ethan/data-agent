from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.repositories.catalog_index import CatalogIndexBuilder, MilvusCatalogIndex
from backend.app.services.schema_catalog import IndexManifest, SearchDocument


class FakeRepository:
    def __init__(self, documents):
        self._documents = documents
        self.manifest = None

    def documents(self, version):
        assert version == "catalog_test"
        return self._documents

    def active_manifest(self, catalog_version=None):
        if self.manifest is None:
            raise RuntimeAgentError("RAG_INDEX_MISSING", "missing")
        return self.manifest

    def activate_manifest(self, **values):
        self.manifest = IndexManifest(
            manifest_id="manifest_test", **values)
        return self.manifest


class FakeEmbedder:
    provider = "test-embedding"
    model_name = "semantic-test-v1"

    async def embed_documents(self, values):
        vectors = {
            "source_domain": [1.0, 0.0, 0.0],
            "object": [0.9, 0.1, 0.0],
            "field_entity": [0.8, 0.2, 0.0],
            "relation": [0.7, 0.3, 0.0],
        }
        return [vectors[value] for value in values]


def document(layer):
    return SearchDocument(
        document_id=f"doc_{layer}", layer=layer, kind=(
            "source" if layer == "source_domain" else
            "field" if layer == "field_entity" else layer),
        target_id=f"target_{layer}", source_id="source_allowed",
        object_id="obj_orders" if layer in {"object", "field_entity"} else "",
        catalog_version="catalog_test", text=layer, token_count=1)


def test_real_milvus_lite_build_validates_four_layers_and_searches(tmp_path: Path):
    pytest.importorskip("pymilvus")
    config = {
        "enabled": True, "uri": str(tmp_path / "catalog.db"),
        "collections": {layer: f"test_{layer}" for layer in
                        ("source_domain", "object", "field_entity", "relation")},
    }
    documents = [document(layer) for layer in
                 ("source_domain", "object", "field_entity", "relation")]
    documents.append(replace(
        document("field_entity"), document_id="doc_sensitive",
        target_id="field_phone", classification="PHONE"))
    repository = FakeRepository(documents)
    index = MilvusCatalogIndex(config)
    try:
        manifest = asyncio.run(CatalogIndexBuilder(
            repository, index, FakeEmbedder(), config).build("catalog_test"))
        assert manifest.embedding_dimension == 3
        assert manifest.document_counts == {
            "source_domain": 1, "object": 1, "field_entity": 2, "relation": 1}
        assert all(index.client.has_collection(name)
                   for name in manifest.collections.values())
        hits = index.search(
            manifest, layer="object", embedding=[0.9, 0.1, 0.0],
            source_ids=["source_allowed"], limit=3, kinds=["object"])
        assert hits[0]["target_id"] == "target_object"
        assert hits[0]["source_id"] == "source_allowed"
        assert index.search(
            manifest, layer="object", embedding=[0.9, 0.1, 0.0],
            source_ids=["source_forbidden"], limit=3) == []
        field_hits = index.search(
            manifest, layer="field_entity", embedding=[0.8, 0.2, 0.0],
            source_ids=["source_allowed"], limit=3,
            denied_classifications=["PHONE"])
        assert [item["target_id"] for item in field_hits] == ["target_field_entity"]
        assert asyncio.run(CatalogIndexBuilder(
            repository, index, FakeEmbedder(), config).build("catalog_test")) == manifest
        with pytest.raises(RuntimeAgentError) as error:
            index.search(manifest, layer="object", embedding=[1.0, 0.0],
                         source_ids=["source_allowed"], limit=3)
        assert error.value.error_code == "RAG_EMBEDDING_DIMENSION_MISMATCH"
    finally:
        index.close()


def test_rebuilds_when_mysql_manifest_is_active_but_milvus_file_is_gone(tmp_path: Path):
    pytest.importorskip("pymilvus")
    first_uri = tmp_path / "catalog.db"
    config = {
        "enabled": True, "uri": str(first_uri),
        "collections": {layer: f"test_{layer}" for layer in
                        ("source_domain", "object", "field_entity", "relation")},
    }
    documents = [document(layer) for layer in
                 ("source_domain", "object", "field_entity", "relation")]
    repository = FakeRepository(documents)
    index = MilvusCatalogIndex(config)
    try:
        first = asyncio.run(CatalogIndexBuilder(
            repository, index, FakeEmbedder(), config).build("catalog_test"))
    finally:
        index.close()

    replacement = tmp_path / "catalog-rebuilt.db"
    rebuilt_config = {**config, "uri": str(replacement)}
    rebuilt = MilvusCatalogIndex(rebuilt_config)
    try:
        second = asyncio.run(CatalogIndexBuilder(
            repository, rebuilt, FakeEmbedder(), rebuilt_config).build("catalog_test"))
        assert second.catalog_version == first.catalog_version
        assert second.index_version == first.index_version
        assert all(rebuilt.client.has_collection(name)
                   for name in second.collections.values())
        hits = rebuilt.search(
            second, layer="object", embedding=[0.9, 0.1, 0.0],
            source_ids=["source_allowed"], limit=3, kinds=["object"])
        assert hits[0]["target_id"] == "target_object"
    finally:
        rebuilt.close()
