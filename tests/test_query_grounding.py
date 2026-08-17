from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.errors import RuntimeAgentError
from backend.app.graph.state import AnswerDraft, QueryDraft, TaskUnderstanding
from backend.app.models import (
    CatalogField,
    CatalogObject,
    CoverageStatus,
    GroundedContext,
)
from backend.app.services.query_grounding import GroundingValidator


def grounded_context() -> GroundedContext:
    return GroundedContext(
        context_id="ctx_1",
        catalog_version="catalog_v1",
        objects=[
            CatalogObject(object_id="obj_orders", name="orders", grain="order",
                          source_id="mysql", domain="commerce", score=1,
                          permission_policy_version="policy_v1"),
            CatalogObject(object_id="obj_items", name="order_items", grain="item",
                          source_id="mysql", domain="commerce", score=1,
                          permission_policy_version="policy_v1"),
        ],
        fields=[
            CatalogField(field_id="field_orders_id", name="orders.order_id",
                         data_type="bigint", object_id="obj_orders", score=1,
                         permission_policy_version="policy_v1"),
            CatalogField(field_id="field_orders_paid", name="orders.paid_at",
                         data_type="datetime", object_id="obj_orders", score=1,
                         permission_policy_version="policy_v1"),
            CatalogField(field_id="field_items_order", name="order_items.order_id",
                         data_type="bigint", object_id="obj_items", score=1,
                         permission_policy_version="policy_v1"),
            CatalogField(field_id="field_items_amount", name="order_items.item_paid_amount",
                         data_type="decimal", object_id="obj_items", score=1,
                         permission_policy_version="policy_v1"),
        ],
        metrics=["gmv"], coverage=CoverageStatus.SUFFICIENT, token_count=50,
        permission_policy_version="policy_v1",
    )


def valid_draft(**updates) -> QueryDraft:
    data = {
        "status": "QUERY_PLAN",
        "candidate_sql": (
            "SELECT SUM(oi.item_paid_amount) AS gmv FROM orders o "
            "JOIN order_items oi ON oi.order_id=o.order_id "
            "WHERE o.paid_at>=:start AND o.paid_at<:end LIMIT :max_rows"
        ),
        "parameters": {"start": "2026-01-01", "end": "2026-01-02", "max_rows": 100},
        "metric_refs": ["gmv"],
        "dimension_refs": [],
        "expected_columns": ["gmv"],
        "time_field": "orders.paid_at",
        "required_object_ids": ["obj_orders", "obj_items"],
    }
    data.update(updates)
    return QueryDraft(**data)


def test_grounded_query_draft_accepts_only_catalog_references():
    GroundingValidator.validate(valid_draft(), grounded_context())


@pytest.mark.parametrize("updates,reference_type", [
    ({"required_object_ids": ["obj_orders", "obj_secret"]}, "object"),
    ({"metric_refs": ["profit"]}, "metric"),
    ({"time_field": "orders.deleted_at"}, "field"),
    ({"candidate_sql": (
        "SELECT SUM(oi.cost_price) AS gmv FROM orders o JOIN order_items oi "
        "ON oi.order_id=o.order_id WHERE o.paid_at>=:start LIMIT :max_rows"
      )}, "SQL column"),
])
def test_ungrounded_references_are_rejected_before_gateway(updates, reference_type):
    with pytest.raises(RuntimeAgentError) as error:
        GroundingValidator.validate(valid_draft(**updates), grounded_context())
    assert error.value.error_code == "QUERY_SPEC_MISMATCH"
    assert error.value.details["reference_type"] == reference_type


def test_query_draft_status_contract_prevents_inconsistent_payloads():
    with pytest.raises(ValidationError):
        QueryDraft(status="SCHEMA_GAP", candidate_sql="SELECT 1",
                   missing_concepts=["orders"])
    with pytest.raises(ValidationError):
        QueryDraft(status="QUERY_PLAN", candidate_sql="SELECT 1")


def test_task_understanding_requires_clarification_for_unresolved_concepts():
    with pytest.raises(ValidationError):
        TaskUnderstanding(task_type="DATA_QUERY", unresolved=["which revenue"],
                          next_action="RETRIEVE")


def test_answer_contract_requires_result_evidence_and_forbids_extra_fields():
    with pytest.raises(ValidationError):
        AnswerDraft(answer="42")
    with pytest.raises(ValidationError):
        AnswerDraft(answer="42", evidence_result_ids=["result_1"], chain_of_thought="x")
