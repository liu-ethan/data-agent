"""Non-bypassable gateway execution node entrypoint."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ...errors import RuntimeAgentError
from ...models import Action, Interrupt, ResultStatus, RunStatus
from .._events import checkpoint_state, emit_event


async def execution_gateway_node(runtime: Any, run: dict[str, Any]) -> dict[str, Any]:
    state = run["state"]
    started = time.perf_counter()
    await emit_event(runtime, run, "node.started",
                     node="execution_gateway_node", action=Action.EXECUTE)
    if state.pending_preview is not None and run.get("mutation_decision"):
        await _commit_mutation(runtime, run)
    elif state.pending_mutation is not None:
        await _preview_mutation(runtime, run)
    else:
        if not state.query_plan:
            raise RuntimeAgentError("QUERY_SPEC_MISMATCH", "query plan is missing")
        observation = await asyncio.to_thread(
            runtime.gateway.execute, state.query_plan, run["permission"])
        state.latest_observation = observation
        if observation.result_id:
            state.result_ids.append(observation.result_id)
        state.goal_checklist["query_executed"] = observation.status in {
            ResultStatus.SUCCESS, ResultStatus.EMPTY,
        }
    if state.status != RunStatus.WAITING_FOR_USER:
        await checkpoint_state(runtime, run, "execution_gateway_node")
    await emit_event(
        runtime, run, "node.completed",
        node="execution_gateway_node", action=Action.EXECUTE,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        error_code=_execution_error(state),
    )
    return {"state": state, "checkpoint_version": run.get("checkpoint_version", -1)}


async def _preview_mutation(runtime: Any, run: dict[str, Any]) -> None:
    state = run["state"]
    if runtime.write_gateway is None:
        raise RuntimeAgentError("WRITE_FORBIDDEN", "write gateway is not configured")
    if runtime.persistence is not None and getattr(runtime.write_gateway, "auditor", None) is None:
        runtime.write_gateway.auditor = runtime.persistence
    preview = await asyncio.to_thread(
        runtime.write_gateway.preview, state.pending_mutation, run["permission"]
    )
    state.pending_preview = preview
    state.status = RunStatus.WAITING_FOR_USER
    state.next_action = Action.ASK_USER
    change = next(iter(preview.diff.values()), None)
    before = change.get("before") if isinstance(change, dict) else None
    after = change.get("after") if isinstance(change, dict) else None
    state.pending_interrupt = Interrupt(
        reason="WRITE_APPROVAL",
        question=(
            f"确认将 {preview.target} 从 {before} 改为 {after}？"
            f"预计影响 {preview.estimated_affected_rows} 行。"
        ),
        candidates=["确认执行", "取消"],
        resume_node="execution_gateway_node",
        checkpoint_id=f"ckpt_{uuid4().hex[:16]}",
        interrupt_id=f"interrupt_{uuid4().hex[:16]}",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        preview=preview,
    )
    await checkpoint_state(runtime, run, "execution_gateway_node")
    await emit_event(
        runtime,
        run,
        "interrupt.created",
        node="execution_gateway_node",
        action=Action.ASK_USER,
        interrupt=state.pending_interrupt.model_dump(mode="json"),
        state_version=run.get("checkpoint_version"),
    )


async def _commit_mutation(runtime: Any, run: dict[str, Any]) -> None:
    state = run["state"]
    if runtime.write_gateway is None or state.pending_preview is None:
        raise RuntimeAgentError("WRITE_FORBIDDEN", "write gateway is not configured")
    if runtime.persistence is not None and getattr(runtime.write_gateway, "auditor", None) is None:
        runtime.write_gateway.auditor = runtime.persistence
    observation = await asyncio.to_thread(
        runtime.write_gateway.commit, state.pending_preview, run["permission"]
    )
    state.latest_mutation = observation
    state.goal_checklist["query_executed"] = observation.status == ResultStatus.SUCCESS
    if observation.status == ResultStatus.SUCCESS:
        state.pending_interrupt = None


def _execution_error(state) -> str | None:
    if state.latest_mutation and state.latest_mutation.error_code:
        return state.latest_mutation.error_code
    if state.latest_observation:
        return state.latest_observation.error_code
    return None
