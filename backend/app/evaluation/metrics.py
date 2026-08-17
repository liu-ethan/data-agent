"""Named metrics with explicit numerator, denominator and filter text."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .scoring import CaseOutcome

Metric = dict[str, Any]


def metric_definitions() -> list[Metric]:
    return [
        {
            "name": "task_completion_rate",
            "description": "status, clarify/reject expectation, permission and result_compare all pass",
        },
        {
            "name": "task_frame_accuracy",
            "description": "intent and required metric_ids match the golden TaskFrame",
        },
        {
            "name": "object_recall_at_k",
            "description": "required objects present in GroundedContext",
        },
        {"name": "field_recall_at_k", "description": "required fields present in GroundedContext"},
        {
            "name": "context_precision",
            "description": "required objects+fields over all retrieved objects+fields",
        },
        {
            "name": "schema_gap_recovery",
            "description": "second retrieval round recovered the missing object/field",
        },
        {
            "name": "result_accuracy",
            "description": "result_compare on columns, rows and numeric tolerance",
        },
        {
            "name": "action_routing_accuracy",
            "description": "expected Action sequence is a subsequence of condensed node actions",
        },
        {
            "name": "average_graph_steps",
            "description": "mean top-level node steps per runnable case",
        },
        {
            "name": "security_pass_rate",
            "description": "security cases rejected or isolated as expected",
        },
        {
            "name": "hitl_resume_success",
            "description": "write/HITL resume cases; empty while spec 06 is deferred",
        },
        {
            "name": "follow_up_resolution_accuracy",
            "description": "multi-turn follow-up cases complete with the expected artifact/result",
        },
        {
            "name": "checkpoint_recovery_success",
            "description": "checkpoint cases resume from the same thread without repeating side effects",
        },
        {
            "name": "long_term_memory_precision",
            "description": "long-term preference cases bind only the confirmed, in-scope key",
        },
        {
            "name": "p95_latency_ms_success",
            "description": "P95 end-to-end latency for completed cases",
        },
        {
            "name": "p95_latency_ms_failure",
            "description": "P95 end-to-end latency for failed runnable cases",
        },
        {"name": "average_token_cost", "description": "mean input+output tokens per runnable case"},
        {"name": "p95_grounded_context_tokens", "description": "P95 GroundedContext token_count"},
        {
            "name": "sql_execution_accuracy",
            "description": "column/SQL execution match only; not a substitute for task completion",
        },
    ]


def summarize_metrics(
    outcomes: Sequence[CaseOutcome],
    *,
    filter_note: str = "runnable cases; deferred spec 06 HITL excluded",
) -> list[Metric]:
    runnable = [
        item for item in outcomes if not item.deferred and not item.status.startswith("NOT_RUN")
    ]
    success = [item for item in runnable if item.completed]
    failure = [item for item in runnable if not item.completed]

    def rate(
        name: str, items: Sequence[CaseOutcome], passed: Callable[[CaseOutcome], bool], filt: str
    ) -> Metric:
        numerator = sum(1 for item in items if passed(item))
        denominator = len(items)
        return {
            "name": name,
            "value": (numerator / denominator) if denominator else None,
            "numerator": numerator,
            "denominator": denominator,
            "filter": filt,
        }

    def mean_metric(
        name: str, items: Sequence[CaseOutcome], read: Callable[[CaseOutcome], float], filt: str
    ) -> Metric:
        values = [read(item) for item in items]
        denominator = len(values)
        total = sum(values)
        return {
            "name": name,
            "value": (total / denominator) if denominator else None,
            "numerator": total,
            "denominator": denominator,
            "filter": filt,
        }

    def p95(
        name: str, items: Sequence[CaseOutcome], read: Callable[[CaseOutcome], float], filt: str
    ) -> Metric:
        values = [read(item) for item in items if read(item) is not None]
        return {
            "name": name,
            "value": _percentile(values, 0.95) if values else None,
            "numerator": len(values),
            "denominator": len(values),
            "filter": filt,
        }

    object_needed = sum(len(item.required_objects) for item in runnable)
    object_hit = sum(
        len(set(item.required_objects) & set(item.observed_objects)) for item in runnable
    )
    field_needed = sum(len(item.required_fields) for item in runnable)
    field_hit = sum(len(set(item.required_fields) & set(item.observed_fields)) for item in runnable)
    relevant = object_hit + field_hit
    observed_total = sum(
        len(item.observed_objects) + len(item.observed_fields) for item in runnable
    )
    gap_cases = [item for item in runnable if item.schema_gap_recovered is not None]
    follow = [item for item in runnable if item.category in {"follow_up", "multi_turn"}]
    checkpoint = [item for item in runnable if item.category == "checkpoint"]
    memory = [item for item in runnable if item.category == "long_term_memory"]
    hitl = [item for item in runnable if item.category == "hitl"]
    security = [item for item in runnable if item.category == "security"]
    token_items = runnable
    grounded = [item for item in runnable if item.grounded_context_tokens is not None]

    return [
        rate("task_completion_rate", runnable, lambda item: item.completed, filter_note),
        rate("task_frame_accuracy", runnable, lambda item: item.task_frame_ok, filter_note),
        {
            "name": "object_recall_at_k",
            "value": (object_hit / object_needed) if object_needed else None,
            "numerator": object_hit,
            "denominator": object_needed,
            "filter": filter_note,
        },
        {
            "name": "field_recall_at_k",
            "value": (field_hit / field_needed) if field_needed else None,
            "numerator": field_hit,
            "denominator": field_needed,
            "filter": filter_note,
        },
        {
            "name": "context_precision",
            "value": (relevant / observed_total) if observed_total else None,
            "numerator": relevant,
            "denominator": observed_total,
            "filter": filter_note,
        },
        rate(
            "schema_gap_recovery",
            gap_cases,
            lambda item: bool(item.schema_gap_recovered),
            "runnable cases that recorded a SchemaGap recovery flag",
        ),
        rate("result_accuracy", runnable, lambda item: item.result_ok, filter_note),
        rate("action_routing_accuracy", runnable, lambda item: item.action_ok, filter_note),
        mean_metric(
            "average_graph_steps", runnable, lambda item: float(item.graph_steps), filter_note
        ),
        rate(
            "security_pass_rate",
            security,
            lambda item: item.completed,
            "category=security; expected reject/isolate",
        ),
        rate(
            "hitl_resume_success",
            hitl,
            lambda item: item.completed,
            "category=hitl and not deferred; spec 06 write gateway",
        ),
        rate(
            "follow_up_resolution_accuracy",
            follow,
            lambda item: item.completed,
            "category in {follow_up, multi_turn}",
        ),
        rate(
            "checkpoint_recovery_success",
            checkpoint,
            lambda item: item.completed,
            "category=checkpoint",
        ),
        rate(
            "long_term_memory_precision",
            memory,
            lambda item: item.completed,
            "category=long_term_memory",
        ),
        p95(
            "p95_latency_ms_success",
            success,
            lambda item: item.latency_ms,
            "completed runnable cases",
        ),
        p95(
            "p95_latency_ms_failure", failure, lambda item: item.latency_ms, "failed runnable cases"
        ),
        mean_metric(
            "average_token_cost",
            token_items,
            lambda item: float(item.input_tokens + item.output_tokens),
            filter_note,
        ),
        p95(
            "p95_grounded_context_tokens",
            grounded,
            lambda item: float(item.grounded_context_tokens or 0),
            "runnable cases with GroundedContext.token_count",
        ),
        rate(
            "sql_execution_accuracy",
            runnable,
            lambda item: item.sql_execution_accurate,
            "column/SQL match only; do not report as task completion",
        ),
    ]


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))])
