"""Spec 03 acceptance tests for the five-node runtime graph.

Covers the §6 routing invariants that the module split in §11 does not
by itself prove: Coverage gating, GoalChecklist, loop termination,
empty-result stability, and immediate permission failure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.graph.nodes import agent_node, query_generation_node, response_node
from backend.app.models import (
    Action,
    AgentState,
    CoverageStatus,
    FilterSpec,
    GroundedContext,
    Intent,
    QueryPlan,
    QuerySpec,
    ResultObservation,
    ResultStatus,
    ResultSummary,
    RunStatus,
    SchemaGap,
    TaskFrame,
    TimeRange,
)
from backend.app.testing import build_test_permission, build_test_runtime


def _time_range() -> TimeRange:
    return TimeRange(
        start=datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc),
        timezone="Asia/Shanghai",
    )


def _task(*, intent=Intent.DATA_QUERY, filters=None) -> TaskFrame:
    return TaskFrame(
        task_id="task_spec03",
        user_id="u_demo_user",
        question="昨天各品类 GMV 是多少？",
        intent=intent,
        metric_ids=["gmv"],
        filters=filters or [],
        time_range=_time_range(),
    )


def _observation(*, status=ResultStatus.SUCCESS, error_code=None) -> ResultObservation:
    summary = None
    if status in {ResultStatus.SUCCESS, ResultStatus.EMPTY}:
        summary = ResultSummary(
            row_count=0 if status == ResultStatus.EMPTY else 1,
            columns=["gmv"],
            empty=status == ResultStatus.EMPTY,
        )
    return ResultObservation(
        status=status,
        result_id="res_1" if status != ResultStatus.REJECTED else None,
        summary=summary,
        error_code=error_code,
        query_plan_id="plan_1",
        catalog_version="catalog_v1",
        permission_policy_version="policy_test_v1",
    )


def _plan() -> QueryPlan:
    return QueryPlan(
        query_plan_id="plan_1",
        query_spec=QuerySpec(query_id="query_1", metric_refs=["gmv"],
                             expected_columns=["gmv"]),
        candidate_sql="SELECT 1 AS gmv",
        catalog_version="catalog_v1",
        permission_policy_version="policy_test_v1",
    )


def _run(state: AgentState) -> dict:
    return {
        "state": state,
        "message": state.task_frame.question if state.task_frame else "昨天 GMV",
        "timezone_name": "Asia/Shanghai",
        "permission": build_test_permission("u_demo_user"),
    }


def _state(**updates) -> AgentState:
    values = {
        "thread_id": "thread_spec03",
        "request_id": "req_spec03",
        "user_id": "u_demo_user",
        "task_frame": _task(),
        "coverage": CoverageStatus.SUFFICIENT,
        "budgets": {
            "iterations_used": 1,
            "retrieval_rounds_used": 1,
            "query_retries_used": 0,
            "max_iterations": 6,
            "max_retrieval_rounds": 2,
        },
        "goal_checklist": {
            "task_understood": True,
            "evidence_retrieved": True,
            "query_executed": False,
            "response_delivered": False,
        },
    }
    values.update(updates)
    return AgentState(**values)


def _decide(state: AgentState, runtime=None) -> AgentState:
    graph = runtime or build_test_runtime()
    asyncio.run(agent_node(graph, _run(state)))
    return state


# ---------------------------------------------------------------------------
# §6: Coverage that is not SUFFICIENT cannot enter GENERATE
# ---------------------------------------------------------------------------

def test_partial_coverage_routes_to_retrieve_not_generate():
    state = _state(
        coverage=CoverageStatus.PARTIAL,
        query_plan=None,
        schema_gap=SchemaGap(
            gap_id="gap_1", missing_concepts=["gmv"],
            narrow_query="昨天 GMV", reason="partial", retrieval_round=1,
        ),
        budgets={"iterations_used": 1, "retrieval_rounds_used": 0,
                 "query_retries_used": 0, "max_iterations": 6,
                 "max_retrieval_rounds": 2},
    )
    _decide(state)
    assert state.next_action == Action.RETRIEVE
    assert state.status == RunStatus.RUNNING


def test_query_generation_rejects_insufficient_coverage():
    state = _state(coverage=CoverageStatus.PARTIAL, query_plan=None)
    state.grounded_context = GroundedContext(
        context_id="ctx_1", catalog_version="catalog_v1",
        coverage=CoverageStatus.PARTIAL, token_count=10,
        permission_policy_version="policy_test_v1",
    )
    with pytest.raises(RuntimeAgentError) as exc:
        asyncio.run(query_generation_node(build_test_runtime(), _run(state)))
    assert exc.value.error_code == "QUERY_SPEC_MISMATCH"


# ---------------------------------------------------------------------------
# §6: GoalChecklist incomplete cannot END
# ---------------------------------------------------------------------------

def test_data_query_without_executed_query_cannot_end_successfully():
    state = _state(
        query_plan=None,
        latest_observation=None,
        goal_checklist={
            "task_understood": True,
            "evidence_retrieved": True,
            "query_executed": False,
            "response_delivered": False,
        },
    )
    asyncio.run(response_node(build_test_runtime(), _run(state)))
    assert state.status != RunStatus.SUCCEEDED
    assert state.next_action != Action.END
    assert state.goal_checklist.get("response_delivered") is not True


# ---------------------------------------------------------------------------
# §6: Consecutive identical Action + parameters terminate the loop
# ---------------------------------------------------------------------------

def test_schema_gap_interrupt_omits_internal_coverage_labels():
    state = _state(
        coverage=CoverageStatus.PARTIAL,
        query_plan=None,
        schema_gap=SchemaGap(
            gap_id="gap_1",
            missing_concepts=["catalog metric binding", "time field"],
            narrow_query="上周退款总金额",
            reason="partial",
            retrieval_round=2,
        ),
        grounded_context=GroundedContext(
            context_id="ctx_1",
            catalog_version="catalog_v1",
            coverage=CoverageStatus.PARTIAL,
            token_count=10,
            permission_policy_version="policy_test_v1",
            metrics=["refund_amount"],
        ),
        budgets={
            "iterations_used": 2,
            "retrieval_rounds_used": 2,
            "query_retries_used": 0,
            "max_iterations": 6,
            "max_retrieval_rounds": 2,
        },
    )
    _decide(state)
    assert state.next_action == Action.ASK_USER
    assert state.pending_interrupt is not None
    assert "catalog metric binding" not in state.pending_interrupt.candidates
    assert "refund_amount" in state.pending_interrupt.candidates


def test_repeated_retrieve_with_unchanged_gap_terminates():
    gap = SchemaGap(
        gap_id="gap_1", missing_concepts=["unknown_metric"],
        narrow_query="随便看看", reason="ambiguous", retrieval_round=1,
    )
    state = _state(
        coverage=CoverageStatus.PARTIAL,
        schema_gap=gap,
        query_plan=None,
        budgets={"iterations_used": 2, "retrieval_rounds_used": 1,
                 "query_retries_used": 0, "max_iterations": 6,
                 "max_retrieval_rounds": 4},
    )
    _decide(state)
    assert state.next_action == Action.RETRIEVE
    _decide(state)
    assert state.next_action == Action.FAIL
    assert state.status == RunStatus.FAILED


# ---------------------------------------------------------------------------
# §6: Empty results cannot auto-expand time or drop user filters
# ---------------------------------------------------------------------------

def test_empty_observation_responds_without_widening_time_or_filters():
    filters = [FilterSpec(field="orders.status", operator="=", value="PAID")]
    original_range = _time_range()
    state = _state(
        task_frame=_task(filters=filters),
        query_plan=_plan(),
        latest_observation=_observation(status=ResultStatus.EMPTY),
        goal_checklist={
            "task_understood": True,
            "evidence_retrieved": True,
            "query_executed": True,
            "response_delivered": False,
        },
    )
    _decide(state)
    assert state.next_action == Action.RESPOND
    assert state.task_frame.time_range == original_range
    assert state.task_frame.filters == filters
    assert state.query_plan is not None


# ---------------------------------------------------------------------------
# §6: Permission failure terminates immediately (no GENERATE retry)
# ---------------------------------------------------------------------------

def test_permission_denied_does_not_retry_generate():
    state = _state(
        query_plan=_plan(),
        latest_observation=_observation(
            status=ResultStatus.REJECTED, error_code="PERMISSION_DENIED"),
        budgets={"iterations_used": 3, "retrieval_rounds_used": 1,
                 "query_retries_used": 0, "max_iterations": 6,
                 "max_retrieval_rounds": 2},
    )
    _decide(state)
    assert state.next_action == Action.FAIL
    assert state.status == RunStatus.REJECTED
    assert state.query_plan is not None
    assert state.budgets["query_retries_used"] == 0


def test_retryable_gateway_error_retries_generate_once():
    state = _state(
        query_plan=_plan(),
        latest_observation=_observation(
            status=ResultStatus.FAILED, error_code="QUERY_EXECUTION_FAILED"),
        budgets={"iterations_used": 3, "retrieval_rounds_used": 1,
                 "query_retries_used": 0, "max_iterations": 6,
                 "max_retrieval_rounds": 2},
    )
    _decide(state)
    assert state.next_action == Action.GENERATE
    assert state.query_plan is None
    assert state.budgets["query_retries_used"] == 1
