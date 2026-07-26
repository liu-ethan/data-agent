from __future__ import annotations

import time
from collections.abc import Iterator

from app.agent.graph import build_graph
from app.agent.state import AgentState
from app.log.logging import log_event


def _summarize(node: str, state: dict) -> str:
    if state.get("error"):
        return "rejected" if node == "SQLGuardrail" else "failed"
    if node == "IntentAnalyzer":
        return str(state.get("intent") or "unknown")
    if node == "ClarificationChecker":
        return "clarification_needed" if state.get("need_clarification") else "clear"
    if node == "ComplexityRouter":
        return str(state.get("route_mode") or "react")
    if node == "ReActAgent":
        if state.get("pending_tool_calls"):
            return "tool_requested"
        return "sql_generated" if state.get("generated_sql") else "completed"
    if node == "ReActTools":
        return "sql_proposed" if state.get("generated_sql") else "tools_executed"
    if node == "SQLGuardrail":
        return "passed"
    if node == "ChartPlanner":
        ch = state.get("chart")
        return "skipped" if not ch else str(ch.get("type") or "table")
    return {
        "MemoryLoad": "loaded",
        "SlotMerge": "merged",
        "ClarificationReply": "composed",
        "SchemaRetriever": "retrieved",
        "SQLGenerator": "sql_generated",
        "SQLExecutor": "executed",
        "SQLRepairer": "sql_repaired",
        "AnswerComposer": "composed",
        "MemorySave": "saved",
    }.get(node, "completed")


def iter_pipeline_events(state: AgentState) -> Iterator[tuple[str, dict]]:
    started = time.monotonic()
    run_ids = {
        "request_id": state["request_id"],
        "trace_id": state["trace_id"],
        "session_id": state["session_id"],
        "user_id": state.get("user_id"),
        "user_role": state.get("user_role"),
    }
    log_event("INFO", "run_start", **run_ids)
    yield (
        "run_start",
        {
            "request_id": state["request_id"],
            "trace_id": state["trace_id"],
            "session_id": state["session_id"],
        },
    )

    graph = build_graph()
    merged: dict = dict(state)
    try:
        for update in graph.stream(merged, stream_mode="updates"):
            for node, delta in update.items():
                log_event("INFO", "node_start", node=node, **run_ids)
                yield ("node_start", {"node": node})
                if isinstance(delta, dict):
                    merged.update(delta)
                    for item in delta.get("tool_events") or []:
                        yield (item["event"], item.get("data") or {})
                summary = _summarize(node, merged)
                log_event(
                    "INFO",
                    "node_end",
                    node=node,
                    summary=summary,
                    **run_ids,
                )
                yield (
                    "node_end",
                    {"node": node, "summary": summary},
                )
                if node == "ComplexityRouter":
                    log_event(
                        "INFO",
                        "route_decision",
                        route_mode=merged.get("route_mode"),
                        route_source=merged.get("route_source"),
                        **run_ids,
                    )
                    yield (
                        "route_decision",
                        {
                            "route_mode": merged.get("route_mode"),
                            "route_source": merged.get("route_source"),
                        },
                    )
                if (
                    node in ("SQLGenerator", "ReActTools")
                    and isinstance(delta, dict)
                    and delta.get("generated_sql")
                ):
                    yield (
                        "sql",
                        {"sql": delta["generated_sql"], "repaired": False},
                    )
                if node == "SQLRepairer" and merged.get("generated_sql"):
                    yield (
                        "sql",
                        {"sql": merged["generated_sql"], "repaired": True},
                    )
                if (
                    isinstance(delta, dict)
                    and delta.get("error") is not None
                    and not (
                        node == "SQLExecutor" and not merged.get("repaired")
                    )
                ):
                    yield (
                        "error",
                        {
                            "message": merged["error"],
                            "request_id": run_ids["request_id"],
                            "trace_id": run_ids["trace_id"],
                        },
                    )
                if node == "SQLExecutor" and merged.get("is_write"):
                    yield (
                        "write_result",
                        {
                            "affected_rows": merged.get("affected_rows"),
                            "sql": merged.get("generated_sql") or "",
                        },
                    )
                if (
                    node == "SQLExecutor"
                    and not merged.get("is_write")
                    and merged.get("rows") is not None
                ):
                    yield (
                        "rows",
                        {
                            "columns": merged.get("columns") or [],
                            "rows": merged.get("rows") or [],
                        },
                    )
                if node == "ChartPlanner" and merged.get("chart"):
                    yield ("chart", dict(merged["chart"]))
                if (
                    node in ("AnswerComposer", "ClarificationReply")
                    and merged.get("answer")
                ):
                    yield ("answer", {"text": merged["answer"]})
    except Exception as exc:
        log_event(
            "ERROR",
            "run_error",
            detail={"message": str(exc)[:500]},
            **run_ids,
        )
        yield (
            "error",
            {
                "message": str(exc),
                "request_id": run_ids["request_id"],
                "trace_id": run_ids["trace_id"],
            },
        )

    latency = int((time.monotonic() - started) * 1000)
    merged["latency_ms"] = latency
    log_event(
        "INFO",
        "run_end",
        latency_ms=latency,
        need_clarification=bool(merged.get("need_clarification")),
        repaired=bool(merged.get("repaired")),
        **run_ids,
    )
    yield (
        "done",
        {
            "latency_ms": latency,
            "need_clarification": bool(merged.get("need_clarification")),
            "clarification_question": merged.get("clarification_question"),
            "repaired": bool(merged.get("repaired")),
        },
    )
