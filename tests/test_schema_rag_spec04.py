"""Spec 04 acceptance tests for Schema RAG, Coverage and SchemaGap refill.

Covers the §6/§7/§8 invariants that existing retrieval tests do not isolate:
permission-first filtering, supplement source pinning, CoverageEvaluator,
budget trim order, and catalog/index/permission provenance.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.models import (
    CatalogField,
    CatalogObject,
    CoverageStatus,
    GroundedContext,
    Intent,
    JoinPath,
    PermissionContext,
    SchemaGap,
    TaskFrame,
)
from backend.app.services.catalog_retrieval import (
    ContextBudgeter,
    PassthroughReranker,
    ProductionCatalogRetrievalService,
)
from backend.app.services.coverage import CoverageEvaluator
from backend.app.services.schema_catalog import IndexManifest
from tests.test_production_retrieval import (
    FakeEmbedder,
    FakeIndex,
    FakeRepository,
    FakeReranker,
    MANIFEST,
    permission,
)


def _task(question: str = "昨天 GMV") -> TaskFrame:
    return TaskFrame(task_id="t", user_id="u", question=question,
                     intent=Intent.DATA_QUERY)


def _service(index=None, repository=None, reranker=None, **kwargs):
    return ProductionCatalogRetrievalService(
        repository or FakeRepository(), index or FakeIndex(),
        FakeEmbedder(), reranker or FakeReranker(),
        max_objects=5, max_fields=8, max_tokens=3000, min_score=.55,
        **kwargs)


def _run(service, task, perm, schema_gap=None, existing_context=None):
    return asyncio.run(service.retrieve(
        task, perm, schema_gap,
        existing_context.context_id if existing_context else None,
        existing_context=existing_context))


def _object(**updates) -> CatalogObject:
    values = dict(object_id="obj_orders", name="orders", grain="order",
                  source_id="source_allowed", domain="TRADE", score=0.9,
                  permission_policy_version="policy_v1")
    values.update(updates)
    return CatalogObject(**values)


def _field(**updates) -> CatalogField:
    values = dict(field_id="field_orders_paid", name="orders.paid_at",
                  data_type="DATETIME", classification="BUSINESS_TIME",
                  object_id="obj_orders", score=0.9,
                  permission_policy_version="policy_v1")
    values.update(updates)
    return CatalogField(**values)


# ---------------------------------------------------------------------------
# §6: permission filter happens before rerank; unauthorized sources stay out
# ---------------------------------------------------------------------------

def test_unauthorized_source_never_reaches_reranker_or_traces():
    index, reranker = FakeIndex(), FakeReranker()
    context, _ = _run(_service(index=index, reranker=reranker), _task(),
                      permission())
    assert all(item.source_id == "source_allowed" for item in reranker.candidates)
    assert all(item.source_id == "source_allowed" for item in context.objects)
    dumped = json.dumps(context.model_dump(mode="json"), ensure_ascii=False)
    assert "source_forbidden" not in dumped
    assert all("source_forbidden" not in json.dumps(trace, default=str)
               for trace in context.model_traces)


# ---------------------------------------------------------------------------
# §6: SchemaGap refill cannot expand to every authorized source
# ---------------------------------------------------------------------------

def test_schema_gap_refill_pins_sources_from_existing_context():
    class OpenIndex(FakeIndex):
        def search(self, manifest, **kwargs):
            self.calls.append(kwargs)
            hits = super().search(manifest, **{**kwargs, "source_ids": ["source_allowed"]})
            return hits

    class OpenRepository(FakeRepository):
        def version(self, sources):
            assert "source_allowed" in sources
            return "catalog_v2"

        def lexical_search(self, query, permission, limit, **kwargs):
            assert "source_allowed" in permission.allowed_source_ids
            return []

        def hydrate(self, ids, permission, field_ids=None):
            assert "source_allowed" in permission.allowed_source_ids
            return super().hydrate(ids, permission.model_copy(
                update={"allowed_source_ids": ["source_allowed"]}),
                field_ids=field_ids)

    index = OpenIndex()
    service = _service(index=index, repository=OpenRepository())
    perm = permission().model_copy(update={
        "allowed_source_ids": ["source_allowed", "source_other"],
    })
    first, coverage = _run(service, _task(), perm)
    assert coverage.status == CoverageStatus.SUFFICIENT

    index.calls.clear()
    gap = SchemaGap(
        gap_id="gap_1", missing_concepts=["field.orders.status"],
        candidate_object_ids=["obj_orders", "obj_items"],
        narrow_query="orders.status", reason="missing field", retrieval_round=1,
    )
    _run(service, _task(), perm, schema_gap=gap, existing_context=first)
    assert [call for call in index.calls if call["layer"] == "source_domain"] == []
    object_calls = [call for call in index.calls if call["layer"] == "object"]
    assert object_calls
    assert all(call["source_ids"] == ["source_allowed"] for call in object_calls)


def test_schema_gap_refill_reuses_context_id_and_keeps_prior_fields():
    first, _ = _run(_service(), _task(), permission())
    gap = SchemaGap(
        gap_id="gap_1", missing_concepts=["field.orders.status"],
        candidate_object_ids=[item.object_id for item in first.objects],
        narrow_query="orders.status", reason="missing field", retrieval_round=1,
    )
    second, _ = _run(_service(), _task(), permission(), schema_gap=gap,
                     existing_context=first)
    assert second.context_id == first.context_id
    assert {item.field_id for item in first.fields} <= {
        item.field_id for item in second.fields}


# ---------------------------------------------------------------------------
# §6: CoverageEvaluator is the named coverage component
# ---------------------------------------------------------------------------

def test_coverage_evaluator_is_sufficient_when_metrics_time_and_fields_exist():
    result = CoverageEvaluator(ambiguity_gap=0.08).evaluate(
        task=_task(),
        objects=[_object(), _object(object_id="obj_items", name="order_items",
                                    score=0.7)],
        fields=[_field(), _field(field_id="field_status", name="orders.status",
                                 classification="STATUS"),
                _field(field_id="field_amount",
                       name="order_items.item_paid_amount",
                       object_id="obj_items", classification="AMOUNT")],
        metric_ids=["gmv"], dimension_ids=[],
        required_fields={"orders.paid_at", "orders.status",
                         "order_items.item_paid_amount"},
    )
    assert result.status == CoverageStatus.SUFFICIENT
    assert result.schema_gap is None
    assert "metric.gmv" in result.covered


def test_coverage_evaluator_is_ambiguous_when_top_object_gap_is_small():
    result = CoverageEvaluator(ambiguity_gap=0.08).evaluate(
        task=_task(),
        objects=[_object(score=0.81),
                 _object(object_id="obj_refunds", name="refunds", score=0.80)],
        fields=[_field()],
        metric_ids=[], dimension_ids=[], required_fields=set(),
    )
    assert result.status == CoverageStatus.AMBIGUOUS
    assert "business object" in result.ambiguous


# ---------------------------------------------------------------------------
# §7: budget trim order — aliases, then extra joins; never required fields
# ---------------------------------------------------------------------------

def test_context_budget_trims_aliases_then_joins_not_required_fields():
    obj = _object()
    fields = [
        _field(aliases=["optional alias " * 20]),
        _field(field_id="field_required", name="orders.status",
               classification="STATUS", aliases=["status alias " * 20]),
    ]
    joins = [
        JoinPath(join_id="keep", left="orders.order_id",
                 right="order_items.order_id", cardinality="one_to_many"),
        JoinPath(join_id="extra", left="orders.shop_id", right="shops.shop_id",
                 cardinality="many_to_one"),
    ]
    _, selected, kept_joins, count = ContextBudgeter(180, 8).apply(
        objects=[obj], fields=fields, joins=joins, metrics=["gmv"],
        required_fields={"orders.status"})
    assert "orders.status" in {item.name for item in selected}
    assert all(not item.aliases for item in selected)
    assert {item.join_id for item in kept_joins} == {"keep"}
    assert kept_joins[0].cardinality == "one_to_many"
    assert count <= 180


def test_context_budget_raises_when_required_evidence_cannot_fit():
    obj = _object()
    required = [_field(aliases=["x" * 80])]
    with pytest.raises(RuntimeAgentError) as exc:
        ContextBudgeter(20, 8).apply(
            objects=[obj], fields=required, joins=[], metrics=["gmv"],
            required_fields={"orders.paid_at"})
    assert exc.value.error_code == "RAG_CONTEXT_BUDGET_EXCEEDED"


# ---------------------------------------------------------------------------
# §8: provenance and reranker ablation
# ---------------------------------------------------------------------------

def test_candidates_carry_catalog_index_and_permission_versions():
    context, _ = _run(_service(), _task(), permission())
    assert context.catalog_version == "catalog_v2"
    assert context.permission_policy_version == "policy_v1"
    assert context.objects
    for item in [*context.objects, *context.fields]:
        assert item.index_version == "index_v2"
        assert item.permission_policy_version == "policy_v1"
        assert 0 <= item.score <= 1


def test_passthrough_reranker_still_returns_grounded_context():
    context, coverage = _run(
        _service(reranker=PassthroughReranker()), _task(), permission())
    assert coverage.status == CoverageStatus.SUFFICIENT
    assert context.metrics == ["gmv"]
    assert context.model_traces[-1].get("disabled_for_ablation") is True
