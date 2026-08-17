from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.graph.nodes.retrieval import retrieval_node
from backend.app.models import (
    AgentState,
    CatalogField,
    CatalogObject,
    JoinPath,
    PermissionContext,
    TaskFrame,
)
from backend.app.services.catalog_retrieval import (
    ContextBudgeter,
    LLMReranker,
    ProductionCatalogRetrievalService,
)
from backend.app.services.schema_catalog import IndexManifest

MANIFEST = IndexManifest(
    manifest_id="manifest_1", catalog_version="catalog_v2",
    index_version="index_v2", embedding_provider="fake",
    embedding_model="fake-v1", embedding_dimension=2,
    collections={layer: layer for layer in
                 ("source_domain", "object", "field_entity", "relation")},
    document_counts={layer: 1 for layer in
                     ("source_domain", "object", "field_entity", "relation")})


class FakeEmbedder:
    provider = "fake"
    model_name = "fake-v1"

    async def embed_query(self, value):
        return [1.0, 0.0]


class FakeIndex:
    def __init__(self):
        self.calls = []

    def validate_manifest(self, manifest):
        assert manifest == MANIFEST

    def search(self, manifest, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["source_ids"] == ["source_allowed"]
        if kwargs["layer"] == "source_domain":
            return [{"target_id": "source_allowed", "source_id": "source_allowed",
                     "object_id": "", "kind": "source", "score": 1.0}]
        if kwargs["layer"] == "object":
            return [
                {"target_id": "obj_orders", "source_id": "source_allowed",
                 "object_id": "obj_orders", "kind": "object", "score": .95},
                {"target_id": "gmv", "source_id": "source_allowed",
                 "object_id": "", "kind": "metric", "score": .9},
            ]
        if kwargs["layer"] == "field_entity":
            return [
                {"target_id": "field_orders_paid", "source_id": "source_allowed",
                 "object_id": "obj_orders", "kind": "field", "score": .95},
                {"target_id": "field_items_amount", "source_id": "source_allowed",
                 "object_id": "obj_items", "kind": "field", "score": .9},
            ]
        return []


class FakeRepository:
    def version(self, sources):
        assert sources == ["source_allowed"]
        return "catalog_v2"

    def active_manifest(self, version):
        assert version == "catalog_v2"
        return MANIFEST

    def lexical_search(self, query, permission, limit, **kwargs):
        assert permission.allowed_source_ids == ["source_allowed"]
        return []

    def semantic_bindings(self, query, permission, dense_metric_ids=None):
        assert dense_metric_ids == ["gmv"]
        return (["gmv"], [], {"orders", "order_items"},
                {"orders.paid_at", "orders.status", "order_items.item_paid_amount"})

    def expand_object_ids(self, seed_ids, permission, max_hops, max_objects,
                          required_names=None):
        assert required_names == {"orders", "order_items"}
        return ["obj_orders", "obj_items"]

    def hydrate(self, ids, permission, field_ids=None):
        objects = [
            CatalogObject(object_id="obj_orders", name="orders", grain="order",
                          source_id="source_allowed", domain="TRADE", score=0,
                          permission_policy_version="policy_v1"),
            CatalogObject(object_id="obj_items", name="order_items", grain="item",
                          source_id="source_allowed", domain="TRADE", score=0,
                          permission_policy_version="policy_v1"),
        ]
        fields = [
            CatalogField(field_id="field_orders_paid", name="orders.paid_at",
                         data_type="DATETIME", classification="BUSINESS_TIME",
                         object_id="obj_orders", score=.5,
                         permission_policy_version="policy_v1"),
            CatalogField(field_id="field_orders_status", name="orders.status",
                         data_type="VARCHAR", classification="STATUS",
                         object_id="obj_orders", score=.5,
                         permission_policy_version="policy_v1"),
            CatalogField(field_id="field_items_amount",
                         name="order_items.item_paid_amount", data_type="DECIMAL",
                         classification="AMOUNT", object_id="obj_items", score=.5,
                         permission_policy_version="policy_v1"),
        ]
        joins = [JoinPath(join_id="orders_items", left="orders.order_id",
                          right="order_items.order_id", cardinality="one_to_many")]
        return objects, fields, joins, "catalog_v2"


class FakeReranker:
    def __init__(self):
        self.candidates = []

    async def rerank(self, query, objects):
        self.candidates = objects
        return [item.object_id for item in objects], {
            "purpose": "reranker", "model": "fake-reranker"}


def permission():
    return PermissionContext(
        user_id="u", scope_mode="ALLOWLIST", allowed_shop_ids=["shop_1"],
        allowed_source_ids=["source_allowed"], policy_version="policy_v1")


def test_production_retrieval_is_permission_first_and_returns_bounded_evidence():
    index, reranker = FakeIndex(), FakeReranker()
    service = ProductionCatalogRetrievalService(
        FakeRepository(), index, FakeEmbedder(), reranker,
        max_objects=5, max_fields=8, max_tokens=3000, min_score=.55)
    task = TaskFrame(task_id="t", user_id="u", question="昨天 GMV",
                     intent="DATA_QUERY")
    context, coverage = asyncio.run(service.retrieve(task, permission()))
    assert coverage.status.value == "SUFFICIENT"
    assert context.metrics == ["gmv"]
    assert {item.name for item in context.objects} == {"orders", "order_items"}
    assert {item.name for item in context.fields} >= {
        "orders.paid_at", "orders.status", "order_items.item_paid_amount"}
    assert context.token_count <= 3000
    assert all(item.source_id == "source_allowed" for item in reranker.candidates)
    assert context.model_traces[0]["index_version"] == "index_v2"


def test_embedding_model_mismatch_fails_before_search_or_rerank():
    class WrongEmbedder(FakeEmbedder):
        model_name = "wrong-model"

    service = ProductionCatalogRetrievalService(
        FakeRepository(), FakeIndex(), WrongEmbedder(), FakeReranker())
    task = TaskFrame(task_id="t", user_id="u", question="GMV", intent="DATA_QUERY")
    with pytest.raises(RuntimeAgentError) as error:
        asyncio.run(service.retrieve(task, permission()))
    assert error.value.error_code == "RAG_EMBEDDING_MODEL_MISMATCH"


def test_context_budget_never_drops_required_fields():
    obj = CatalogObject(object_id="obj", name="orders", grain="order",
                        source_id="source", domain="TRADE", score=1,
                        permission_policy_version="p")
    fields = [CatalogField(
        field_id=f"field_{index}", name=f"orders.field_{index}",
        data_type="VARCHAR", object_id="obj", score=1 - index / 20,
        aliases=["very long optional alias" * 10],
        permission_policy_version="p") for index in range(20)]
    _, selected, _, count = ContextBudgeter(700, 4).apply(
        objects=[obj], fields=fields, joins=[], metrics=["gmv"],
        required_fields={"orders.field_19"})
    assert "orders.field_19" in {item.name for item in selected}
    assert len(selected) <= 4
    assert count <= 700


def test_retrieval_replaces_model_proposals_with_authoritative_bindings():
    service = ProductionCatalogRetrievalService(
        FakeRepository(), FakeIndex(), FakeEmbedder(), FakeReranker(),
        max_tokens=3000)

    class Runtime:
        retrieval = service
        persistence = None
        llm = None

    state = AgentState(thread_id="thread", request_id="request", user_id="u")
    state.task_frame = TaskFrame(
        task_id="t", user_id="u", question="昨天 GMV", intent="DATA_QUERY",
        metric_ids=["GMV"], dimension_ids=["category"])
    asyncio.run(retrieval_node(Runtime(), {
        "state": state, "permission": permission(), "checkpoint_version": -1}))
    assert state.task_frame.metric_ids == ["gmv"]
    assert state.task_frame.dimension_ids == []


def test_reranker_deterministically_completes_missing_authorized_ids():
    @dataclass(frozen=True)
    class Trace:
        model: str = "reranker"

    class PartialLLM:
        async def structured(self, schema, **kwargs):
            return schema(ranking=["obj_items"]), Trace()

    objects, _, _, _ = FakeRepository().hydrate(
        ["obj_orders", "obj_items"], permission())
    ranking, trace = asyncio.run(LLMReranker(PartialLLM()).rerank("GMV", objects))
    assert ranking == ["obj_items", "obj_orders"]
    assert trace["completed_missing_ids"] == 1


def test_reranker_rejects_unknown_candidate_ids():
    @dataclass(frozen=True)
    class Trace:
        model: str = "reranker"

    class InvalidLLM:
        async def structured(self, schema, **kwargs):
            return schema(ranking=["obj_forbidden"]), Trace()

    objects, _, _, _ = FakeRepository().hydrate(["obj_orders"], permission())
    with pytest.raises(RuntimeAgentError) as error:
        asyncio.run(LLMReranker(InvalidLLM()).rerank("GMV", objects))
    assert error.value.error_code == "RAG_RERANK_FAILED"
