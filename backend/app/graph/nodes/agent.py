"""Agent decision node entrypoint."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ...models import Action, CoverageStatus, Intent, Interrupt, ResultStatus, RunStatus
from .._events import checkpoint_state, emit_event
from .._task_understanding import understand_task

_NON_RETRYABLE_GATEWAY_ERRORS = {
    "PERMISSION_DENIED",
    "SQL_FORBIDDEN_OPERATION",
    "SQL_OBJECT_NOT_ALLOWED",
    "READER_ACCOUNT_INVALID",
    "READER_ACCOUNT_NOT_READ_ONLY",
    "READER_ACCOUNT_OVERPRIVILEGED",
}


def _action_fingerprint(action: Action, state) -> str:
    gap = tuple(state.schema_gap.missing_concepts) if state.schema_gap else ()
    observation = state.latest_observation
    return "|".join((
        action.value,
        str(state.coverage),
        state.query_plan_id or "",
        ",".join(gap),
        observation.status.value if observation else "",
        state.previous_query_error or "",
    ))


async def agent_node(runtime: Any, run: dict[str, Any]) -> dict[str, Any]:
    state, message, tz = run["state"], run["message"], run["timezone_name"]
    started = time.perf_counter()
    await emit_event(runtime, run, "node.started",
                     node="agent_node", action=state.next_action)
    iterations = int(state.budgets.get("iterations_used", 0)) + 1
    state.budgets["iterations_used"] = iterations
    if iterations > runtime.max_iterations:
        state.status = RunStatus.TIMEOUT
        state.next_action = Action.FAIL
        state.previous_query_error = "BUDGET_EXCEEDED"
    elif state.pending_interrupt:
        state.status = RunStatus.WAITING_FOR_USER
    elif state.task_frame is None:
        preferences = {}
        if runtime.persistence and hasattr(runtime.persistence, "user_preferences"):
            preferences = await asyncio.to_thread(
                runtime.persistence.user_preferences, state.user_id)
        state.task_frame = await understand_task(
            runtime, state, message, tz,
            permission=run.get("permission"),
            preferences=preferences,
        )
        state.goal_checklist = {
            "task_understood": True,
            "evidence_retrieved": False,
            "query_executed": False,
            "response_delivered": False,
        }
        state.next_action = (
            Action.RESPOND
            if state.task_frame.intent == Intent.CHAT_OR_OUT_OF_SCOPE
            else Action.RETRIEVE
        )
    elif state.task_frame.intent == Intent.CHAT_OR_OUT_OF_SCOPE:
        state.next_action = Action.RESPOND
    elif state.coverage != CoverageStatus.SUFFICIENT:
        used = int(state.budgets.get("retrieval_rounds_used", 0))
        state.next_action = (Action.RETRIEVE if used < runtime.max_retrieval_rounds
                             else Action.ASK_USER)
    elif state.task_frame.intent in {
        Intent.SCHEMA_QUERY, Intent.SCHEMA_LOOKUP, Intent.METRIC_EXPLANATION,
    }:
        state.next_action = Action.RESPOND
    elif state.query_plan is None:
        state.next_action = Action.GENERATE
    elif state.latest_observation is None:
        state.next_action = Action.EXECUTE
    elif state.latest_observation.status in {ResultStatus.SUCCESS, ResultStatus.EMPTY}:
        state.next_action = Action.RESPOND
    elif (state.latest_observation.status in {ResultStatus.REJECTED, ResultStatus.FAILED}
          and int(state.budgets.get("query_retries_used", 0)) < runtime.max_query_retries
          and (state.latest_observation.error_code or "") not in _NON_RETRYABLE_GATEWAY_ERRORS):
        state.budgets["query_retries_used"] = int(
            state.budgets.get("query_retries_used", 0)) + 1
        state.previous_query_error = state.latest_observation.error_code or "QUERY_REJECTED"
        state.query_plan = None
        state.query_plan_id = None
        state.latest_observation = None
        state.next_action = Action.GENERATE
    else:
        state.next_action = Action.FAIL
    if state.next_action in {Action.RETRIEVE, Action.GENERATE, Action.EXECUTE}:
        fingerprint = _action_fingerprint(state.next_action, state)
        if state.last_action_fingerprint == fingerprint:
            state.next_action = Action.FAIL
            state.previous_query_error = "GRAPH_TERMINATED"
        else:
            state.last_action_fingerprint = fingerprint
    if state.next_action == Action.ASK_USER:
        state.status = RunStatus.WAITING_FOR_USER
        state.pending_interrupt = Interrupt(
            reason="SCHEMA_GAP",
            question="目前无法唯一确定指标或业务对象，请补充具体口径、表或字段。",
            candidates=(state.schema_gap.missing_concepts if state.schema_gap
                        else state.task_frame.unresolved),
            checkpoint_id=f"ckpt_{uuid4().hex[:16]}",
            interrupt_id=f"interrupt_{uuid4().hex[:16]}",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
    elif state.next_action == Action.FAIL and state.status != RunStatus.TIMEOUT:
        error = (state.latest_observation.error_code if state.latest_observation
                 else state.previous_query_error or "GRAPH_TERMINATED")
        state.previous_query_error = error
        state.status = (RunStatus.REJECTED if error == "PERMISSION_DENIED"
                        else RunStatus.FAILED)
    await checkpoint_state(runtime, run, "agent_node")
    if state.next_action == Action.ASK_USER:
        await emit_event(
            runtime, run, "interrupt.created", node="agent_node",
            action=Action.ASK_USER,
            interrupt=state.pending_interrupt.model_dump(mode="json"),
            state_version=run.get("checkpoint_version"),
        )
    await emit_event(
        runtime, run, "node.completed", node="agent_node", action=state.next_action,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        error_code=(state.previous_query_error
                    if state.next_action == Action.FAIL else None),
    )
    return {"state": state, "checkpoint_version": run.get("checkpoint_version", -1)}
