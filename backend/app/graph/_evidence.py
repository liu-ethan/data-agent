"""Public evaluation evidence projected from AgentState.

Spec 07 scores TaskFrame accuracy, recall and GroundedContext tokens from
this payload. It is evidence-rail data, not hidden reasoning.
"""

from __future__ import annotations

from typing import Any

from ..models import AgentState, CoverageStatus, EvaluationEvidence


def build_evaluation_evidence(state: AgentState) -> EvaluationEvidence:
    task = state.task_frame
    context = state.grounded_context
    coverage = state.coverage
    rounds = int(state.budgets.get("retrieval_rounds_used") or 0)
    recovered: bool | None = None
    if rounds >= 2:
        recovered = bool(context is not None and state.schema_gap is None)
    return EvaluationEvidence(
        intent=task.intent.value if task else None,
        metric_ids=list(task.metric_ids) if task else [],
        object_names=[item.name for item in context.objects] if context else [],
        field_names=[item.name for item in context.fields] if context else [],
        coverage=coverage.value if isinstance(coverage, CoverageStatus) else str(coverage),
        retrieval_rounds=rounds,
        grounded_context_tokens=context.token_count if context else None,
        schema_gap_recovered=recovered,
    )


def evidence_payload(state: AgentState) -> dict[str, Any]:
    return build_evaluation_evidence(state).model_dump(mode="json")
