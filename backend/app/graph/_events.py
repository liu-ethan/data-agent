"""SSE event emission and state checkpointing for the runtime graph.

Spec 03 §7 requires every node transition to publish an event and to be
checkpointed. Centralising the helpers here keeps individual nodes free
of bookkeeping details and makes it impossible for one node to use a
different event schema than the others.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..models import RunStatus
from ..services.trace import record

if TYPE_CHECKING:
    from .main_graph import RuntimeGraph, _Run


EVENT_SCHEMA_VERSION = "runtime_event_v1"


async def emit_event(runtime: "RuntimeGraph", run: "_Run", event: str,
                     *, node: str | None = None, action=None,
                     **extra: Any) -> None:
    """Append an SSE event to ``state.action_history``, persist it and
    forward it to the in-process sink."""
    state = run["state"]
    duration_ms = extra.pop("duration_ms", None)
    error_code = extra.pop("error_code", None)
    item = {
        "event": event,
        "request_id": state.request_id,
        "thread_id": state.thread_id,
        "node": node,
        "action": action.value if action else None,
        "status": state.status.value,
        "duration_ms": duration_ms,
        "error_code": error_code,
        "schema_version": EVENT_SCHEMA_VERSION,
        **extra,
    }
    state.action_history.append(item)
    if runtime.persistence:
        await asyncio.to_thread(
            runtime.persistence.append_event,
            state.request_id, state.user_id, item,
        )
    sink = run.get("event_sink")
    if sink:
        result = sink(item)
        if result is not None:
            await result


async def checkpoint_state(runtime: "RuntimeGraph", run: "_Run", node: str) -> None:
    """Persist an optimistic-locking snapshot of the current state."""
    if not runtime.persistence:
        return
    state = run["state"]
    checkpoint_id = (state.pending_interrupt.checkpoint_id
                     if state.status == RunStatus.WAITING_FOR_USER
                     and state.pending_interrupt else None)
    checkpoint = await asyncio.to_thread(
        runtime.persistence.save_checkpoint, state,
        expected_state_version=run.get("checkpoint_version", -1),
        idempotency_key=f"node:{state.request_id}:{node}:{len(state.action_history)}",
        checkpoint_id=checkpoint_id,
    )
    run["checkpoint_version"] = checkpoint.state_version


def record_terminal(event: str, **payload: Any) -> None:
    """Convenience wrapper around ``services.trace.record``."""
    record(event, **payload)
