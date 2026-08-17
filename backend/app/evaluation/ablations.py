"""Local-only ablation evidence. None of these numbers are production LLM quality."""

from __future__ import annotations

import json
from typing import Any

from ..memory.prompt_context import PromptContextBuilder
from ..models import (
    AgentState,
    Intent,
    PermissionContext,
    ResultObservation,
    ResultStatus,
    ResultSummary,
    SchemaGap,
    TaskFrame,
)
from ..services.catalog_baseline import (
    CatalogRetrievalService,
    HybridCatalogRetrievalService,
    SyntheticCatalogRetrievalService,
    generate_synthetic_metadata,
)
from .metrics import summarize_metrics
from .scoring import CaseOutcome, score_case


def _permission() -> PermissionContext:
    return PermissionContext(
        user_id="u", scope_mode="ALLOWLIST", allowed_shop_ids=["shop_001"], policy_version="p"
    )


def _tokens(payload: Any) -> int:
    return max(1, len(json.dumps(payload, ensure_ascii=False, default=str)) // 4)


def _recall(context, name: str) -> float:
    return 1.0 if any(item.name == name for item in context.objects) else 0.0


def run_ablations() -> dict[str, Any]:
    records = generate_synthetic_metadata()
    full_schema = [
        {"name": record.name, "fields": [field[0] for field in record.fields]} for record in records
    ]
    permission = _permission()
    lookup = TaskFrame(
        task_id="ablation_schema",
        user_id="u",
        question="synthetic_table_0042",
        intent=Intent.SCHEMA_LOOKUP,
    )
    bounded, _ = SyntheticCatalogRetrievalService(
        max_objects=5, max_fields=8, max_tokens=3000
    ).retrieve(lookup, permission)

    bm25_context, _ = CatalogRetrievalService(
        records=records,
        max_objects=5,
        max_fields=8,
        max_tokens=3000,
    ).retrieve(lookup, permission)
    hybrid_context, _ = HybridCatalogRetrievalService(
        records=records,
        max_objects=5,
        max_fields=8,
        max_tokens=3000,
    ).retrieve(lookup, permission)

    first, _ = SyntheticCatalogRetrievalService(
        max_objects=1,
        max_fields=4,
        max_tokens=800,
    ).retrieve(
        TaskFrame(
            task_id="ablation_gap",
            user_id="u",
            question="synthetic_table_0001",
            intent=Intent.SCHEMA_LOOKUP,
        ),
        permission,
    )
    gap = SchemaGap(
        gap_id="gap_ablation",
        missing_concepts=["synthetic_table_0042"],
        narrow_query="synthetic_table_0042",
        reason="targeted refill for ablation",
        retrieval_round=1,
    )
    filled, _ = SyntheticCatalogRetrievalService(
        max_objects=1,
        max_fields=4,
        max_tokens=800,
    ).retrieve(lookup, permission, schema_gap=gap, existing_context=first)

    state = AgentState(thread_id="t", request_id="r", user_id="u")
    state.messages = [
        {"role": "user", "content": f"turn {index} " + ("订单明细 " * 20)} for index in range(16)
    ]
    secret_rows = [
        {"gmv": float(index), "secret": "should-not-be-in-prompt"} for index in range(80)
    ]
    state.latest_observation = ResultObservation(
        status=ResultStatus.SUCCESS,
        result_id="result_ablation",
        summary=ResultSummary(row_count=80, columns=["gmv"], empty=False, preview=secret_rows[:3]),
        query_plan_id="qp_ablation",
        catalog_version="catalog_v1",
        permission_policy_version="p",
    )
    projected = PromptContextBuilder().build(node="agent_node", state=state)
    projected_text = json.dumps(projected, ensure_ascii=False, default=str)

    sql_vs_tcr = summarize_metrics(
        [
            score_case(
                CaseOutcome(
                    case_id="ablation_sql_only",
                    category="single_turn_data_query",
                    status="SUCCEEDED",
                    expected_status="SUCCEEDED",
                    action_sequence=["RETRIEVE", "EXECUTE", "RESPOND"],
                    expected_action_sequence=["RETRIEVE", "EXECUTE", "RESPOND"],
                    required_objects=["orders"],
                    observed_objects=["orders"],
                    required_fields=["orders.paid_at"],
                    observed_fields=["orders.paid_at"],
                    result_ok=False,
                    sql_execution_accurate=True,
                )
            ),
            score_case(
                CaseOutcome(
                    case_id="ablation_task_complete",
                    category="single_turn_data_query",
                    status="SUCCEEDED",
                    expected_status="SUCCEEDED",
                    action_sequence=["RETRIEVE", "EXECUTE", "RESPOND"],
                    expected_action_sequence=["RETRIEVE", "EXECUTE", "RESPOND"],
                    required_objects=["orders"],
                    observed_objects=["orders"],
                    required_fields=["orders.paid_at"],
                    observed_fields=["orders.paid_at"],
                    result_ok=True,
                    sql_execution_accurate=True,
                )
            ),
        ]
    )
    by_name = {item["name"]: item for item in sql_vs_tcr}

    return {
        "full_schema_injection_vs_bounded_context": {
            "full_schema_estimated_tokens": _tokens(full_schema),
            "bounded_context_tokens": bounded.token_count,
            "bounded_context_limit": 3000,
        },
        "bm25_vs_hybrid_retrieval": {
            "target": "synthetic_table_0042",
            "bm25_only": {
                "retrieval_method": "memory",
                "object_recall_at_k": _recall(bm25_context, "synthetic_table_0042"),
                "context_tokens": bm25_context.token_count,
            },
            "hybrid": {
                "retrieval_method": hybrid_context.objects[0].retrieval_method
                if hybrid_context.objects
                else "bm25+embedding+reranker",
                "object_recall_at_k": _recall(hybrid_context, "synthetic_table_0042"),
                "context_tokens": hybrid_context.token_count,
            },
        },
        "schema_gap_disabled_vs_enabled": {
            "disabled": {
                "recovered": _recall(first, "synthetic_table_0042") == 1.0,
                "objects": [item.name for item in first.objects],
            },
            "enabled": {
                "recovered": _recall(filled, "synthetic_table_0042") == 1.0,
                "objects": [item.name for item in filled.objects],
            },
        },
        "full_history_vs_summary_projection": {
            "full_history_tokens": _tokens({"messages": state.messages, "rows": secret_rows}),
            "projected_tokens": _tokens(projected),
            "projected_contains_raw_rows": "should-not-be-in-prompt" in projected_text,
        },
        "sql_execution_accuracy_vs_task_completion_rate": {
            "sql_execution_accuracy": by_name["sql_execution_accuracy"],
            "task_completion_rate": by_name["task_completion_rate"],
        },
    }


def production_ablations(
    outcomes: list[CaseOutcome],
    *,
    full_schema_tokens: int = 101_500,
) -> dict[str, Any]:
    """Five Spec 07 comparisons scored from production HTTP outcomes.

    Retrieval-only contrasts still use the local synthetic catalog because they
    do not require an LLM. SQL vs TCR and GroundedContext tokens come from the
    authenticated run being reported.
    """
    synthetic = run_ablations()
    metrics = {
        item["name"]: item
        for item in summarize_metrics(outcomes, filter_note="production HTTP runnable cases")
    }
    grounded = sorted(
        float(item.grounded_context_tokens)
        for item in outcomes
        if item.grounded_context_tokens is not None
    )
    bounded = grounded[min(len(grounded) - 1, int((len(grounded) - 1) * 0.95))] if grounded else 0
    return {
        "non_production": False,
        "full_schema_injection_vs_bounded_context": {
            "full_schema_estimated_tokens": full_schema_tokens,
            "bounded_context_tokens": bounded,
            "bounded_context_limit": 3000,
            "source": "production_http_grounded_context",
        },
        "bm25_vs_hybrid_retrieval": {
            **synthetic["bm25_vs_hybrid_retrieval"],
            "source": "synthetic_catalog",
        },
        "schema_gap_disabled_vs_enabled": {
            **synthetic["schema_gap_disabled_vs_enabled"],
            "source": "synthetic_catalog",
        },
        "full_history_vs_summary_projection": {
            **synthetic["full_history_vs_summary_projection"],
            "source": "prompt_context_builder",
        },
        "sql_execution_accuracy_vs_task_completion_rate": {
            "sql_execution_accuracy": metrics["sql_execution_accuracy"],
            "task_completion_rate": metrics["task_completion_rate"],
            "source": "production_http_outcomes",
        },
    }
