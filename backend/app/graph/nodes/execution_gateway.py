"""Non-bypassable gateway execution node entrypoint."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ...errors import RuntimeAgentError
from ...models import Action, ResultStatus


async def execution_gateway_node(runtime: Any, run: dict[str, Any]) -> dict[str, Any]:
    state = run["state"]
    started = time.perf_counter()
    await runtime._emit(run, "node.started", node="execution_gateway_node",
                        action=Action.EXECUTE)
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
    await runtime._checkpoint(run, "execution_gateway_node")
    await runtime._emit(
        run, "node.completed", node="execution_gateway_node", action=Action.EXECUTE,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        error_code=observation.error_code,
    )
    return {"state": state, "checkpoint_version": run.get("checkpoint_version", -1)}
