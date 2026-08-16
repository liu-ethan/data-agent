"""Grounded QuerySpec and candidate SQL generation entrypoint."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from ...errors import RuntimeAgentError
from ...models import (Action, CoverageStatus, QueryPlan, QuerySpec, SchemaGap)
from ...services.query_grounding import GroundingValidator
from ..state import QueryDraft


async def query_generation_node(runtime: Any, run: dict[str, Any]) -> dict[str, Any]:
    state = run["state"]
    started = time.perf_counter()
    await runtime._emit(run, "node.started", node="query_generation_node",
                        action=Action.GENERATE)
    context = state.grounded_context
    if not context or state.coverage != CoverageStatus.SUFFICIENT or not state.task_frame:
        raise RuntimeAgentError(
            "QUERY_SPEC_MISMATCH",
            "query generation requires sufficient grounded context",
        )
    if not state.task_frame.time_range:
        state.coverage = CoverageStatus.PARTIAL
        state.schema_gap = None
        await runtime._checkpoint(run, "query_generation_node")
        return {"state": state, "checkpoint_version": run.get("checkpoint_version", -1)}
    if not runtime.llm:
        category = bool(state.task_frame.dimension_ids)
        sql = (
            "SELECT c.category_name AS category_name, "
            "ROUND(SUM(oi.item_paid_amount), 2) AS gmv FROM orders o "
            "JOIN order_items oi ON oi.order_id=o.order_id "
            "JOIN products p ON p.product_id=oi.product_id "
            "JOIN categories c ON c.category_id=p.category_id "
            "WHERE o.status=:status AND o.paid_at>=:start AND o.paid_at<:end "
            "GROUP BY c.category_id,c.category_name "
            "ORDER BY gmv DESC,c.category_name ASC LIMIT :max_rows"
            if category else
            "SELECT ROUND(SUM(oi.item_paid_amount), 2) AS gmv FROM orders o "
            "JOIN order_items oi ON oi.order_id=o.order_id "
            "WHERE o.status=:status AND o.paid_at>=:start AND o.paid_at<:end "
            "LIMIT :max_rows"
        )
        draft = QueryDraft(
            status="QUERY_PLAN",
            candidate_sql=sql,
            parameters={
                "status": "PAID",
                "start": state.task_frame.time_range.start.strftime("%F %T"),
                "end": state.task_frame.time_range.end.strftime("%F %T"),
                "max_rows": 1000,
            },
            metric_refs=state.task_frame.metric_ids or ["gmv"],
            dimension_refs=state.task_frame.dimension_ids,
            expected_columns=["category_name", "gmv"] if category else ["gmv"],
            time_field="orders.paid_at",
            required_object_ids=(
                ["obj_orders", "obj_order_items", "obj_products", "obj_categories"]
                if category else ["obj_orders", "obj_order_items"]
            ),
        )
    else:
        safe_context = {
            "catalog_version": context.catalog_version,
            "objects": [item.model_dump() for item in context.objects],
            "fields": [item.model_dump() for item in context.fields],
            "joins": [item.model_dump() for item in context.join_paths],
            "task": state.task_frame.model_dump(mode="json"),
            "previous_gateway_error": state.previous_query_error,
        }
        draft, trace = await runtime.llm.structured(
            system="Generate a single MySQL SELECT QueryPlan only from the provided grounded context. Use named parameters, every mandatory metric filter, half-open time bounds and an explicit LIMIT. Result aliases and expected_columns must be stable lowercase English catalog/metric identifiers such as category_name and gmv, never localized display labels. If evidence is insufficient return SCHEMA_GAP. Never generate DDL/DML or query objects absent from context.",
            user=json.dumps(safe_context, ensure_ascii=False),
            schema=QueryDraft,
            purpose="query_generation",
            temperature=0.0,
            prompt_version="query_generation_v1",
        )
        state.model_traces.append(asdict(trace) | {"purpose": "query_generation"})
        bound_metrics = context.metrics or state.task_frame.metric_ids
        if bound_metrics:
            draft = draft.model_copy(update={"metric_refs": bound_metrics})
        if state.task_frame.dimension_ids:
            draft = draft.model_copy(
                update={"dimension_refs": state.task_frame.dimension_ids})
        draft = runtime._normalize_query_draft(draft)
    if draft.status == "SCHEMA_GAP":
        state.coverage = CoverageStatus.PARTIAL
        state.schema_gap = SchemaGap(
            gap_id=f"gap_{uuid4().hex[:16]}",
            missing_concepts=draft.missing_concepts or ["query evidence"],
            candidate_object_ids=draft.required_object_ids,
            narrow_query=state.task_frame.question,
            reason="LLM requested more schema evidence",
            retrieval_round=min(
                runtime.max_retrieval_rounds,
                int(state.budgets.get("retrieval_rounds_used", 0)) + 1,
            ),
        )
    else:
        GroundingValidator.validate(draft, context)
        spec = QuerySpec(
            query_id=f"query_{uuid4().hex[:16]}",
            metric_refs=draft.metric_refs,
            dimension_refs=draft.dimension_refs,
            filters=state.task_frame.filters,
            time_range=state.task_frame.time_range,
            time_field=draft.time_field,
            join_path_refs=[item.join_id for item in context.join_paths],
            allowed_object_ids=draft.required_object_ids,
            expected_columns=draft.expected_columns,
            max_rows=min(
                1000,
                int(runtime.settings.get("runtime_agent", {}).get(
                    "max_rows_per_query", 1000)),
            ),
        )
        plan = QueryPlan(
            query_plan_id=f"plan_{uuid4().hex[:16]}",
            query_spec=spec,
            candidate_sql=runtime._canonicalize_parameters(
                draft.candidate_sql, draft.parameters),
            parameters=draft.parameters,
            catalog_version=context.catalog_version,
            permission_policy_version=run["permission"].policy_version,
            generator="llm" if runtime.llm else "deterministic_test_double",
        )
        state.query_plan = plan
        state.query_plan_id = plan.query_plan_id
    await runtime._checkpoint(run, "query_generation_node")
    await runtime._emit(
        run, "node.completed", node="query_generation_node", action=Action.GENERATE,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return {"state": state, "checkpoint_version": run.get("checkpoint_version", -1)}
