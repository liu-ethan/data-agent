"""Single entrypoint for initial and SchemaGap catalog retrieval."""

from __future__ import annotations

import time
from typing import Any

from ...models import Action, CoverageStatus
from .._events import checkpoint_state, emit_event


async def retrieval_node(runtime: Any, run: dict[str, Any]) -> dict[str, Any]:
    state = run["state"]
    started = time.perf_counter()
    await emit_event(runtime, run, "node.started",
                     node="retrieval_node", action=Action.RETRIEVE)
    result = runtime.retrieval.retrieve(
        state.task_frame, run["permission"], state.schema_gap, state.grounded_context_id,
        existing_context=state.grounded_context,
    )
    context, coverage = await result if hasattr(result, "__await__") else result
    state.grounded_context = context
    state.grounded_context_id = context.context_id
    state.catalog_version = context.catalog_version
    state.coverage = coverage.status
    state.schema_gap = coverage.schema_gap
    state.model_traces.extend(context.model_traces)
    # TaskUnderstanding IDs are semantic proposals. Only catalog-proven IDs
    # may flow into QuerySpec generation; preserve raw mentions separately.
    bound_dimensions = [
        item.removeprefix("dimension.") for item in coverage.covered
        if item.startswith("dimension.")
    ]
    grounded_field_refs = {
        reference
        for field in context.fields
        for reference in (field.field_id, field.name)
    }
    bound_dimensions.extend(
        item for item in state.task_frame.dimension_ids
        if item in grounded_field_refs)
    state.task_frame = state.task_frame.model_copy(update={
        "metric_ids": list(context.metrics),
        "dimension_ids": list(dict.fromkeys(bound_dimensions)),
    })
    state.context_frame = None
    state.budgets["retrieval_rounds_used"] = int(
        state.budgets.get("retrieval_rounds_used", 0)) + 1
    state.goal_checklist["evidence_retrieved"] = (
        coverage.status == CoverageStatus.SUFFICIENT)
    await checkpoint_state(runtime, run, "retrieval_node")
    await emit_event(
        runtime, run, "node.completed",
        node="retrieval_node", action=Action.RETRIEVE,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return {
        "state": state,
        "context": context,
        "checkpoint_version": run.get("checkpoint_version", -1),
    }
