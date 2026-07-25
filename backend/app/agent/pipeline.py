from __future__ import annotations

import time
from collections.abc import Iterator

from app.agent.graph import build_graph
from app.agent.state import AgentState


def _summarize(node: str, state: dict) -> str:
    if state.get("error"):
        return "rejected" if node == "SQLGuardrail" else "failed"
    if node == "IntentAnalyzer":
        return str(state.get("intent") or "unknown")
    if node == "ClarificationChecker":
        return "clarification_needed" if state.get("need_clarification") else "clear"
    if node == "RouteEmit":
        return str(state.get("route_mode") or "react")
    if node == "SQLGuardrail":
        return "passed"
    return {
        "ClarificationReply": "composed",
        "SchemaRetriever": "retrieved",
        "SQLGenerator": "sql_generated",
        "SQLExecutor": "executed",
        "AnswerComposer": "composed",
    }.get(node, "completed")


def iter_pipeline_events(state: AgentState) -> Iterator[tuple[str, dict]]:
    started = time.monotonic()
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
                yield ("node_start", {"node": node})
                if isinstance(delta, dict):
                    merged.update(delta)
                yield (
                    "node_end",
                    {"node": node, "summary": _summarize(node, merged)},
                )
                if node == "RouteEmit":
                    yield (
                        "route_decision",
                        {
                            "route_mode": merged.get("route_mode"),
                            "route_source": merged.get("route_source"),
                        },
                    )
                if node == "SQLGenerator" and merged.get("generated_sql"):
                    yield (
                        "sql",
                        {"sql": merged["generated_sql"], "repaired": False},
                    )
                if isinstance(delta, dict) and delta.get("error") is not None:
                    yield ("error", {"message": merged["error"]})
                if node == "SQLExecutor" and merged.get("rows") is not None:
                    yield (
                        "rows",
                        {
                            "columns": merged.get("columns") or [],
                            "rows": merged.get("rows") or [],
                        },
                    )
                if (
                    node in ("AnswerComposer", "ClarificationReply")
                    and merged.get("answer")
                ):
                    yield ("answer", {"text": merged["answer"]})
    except Exception as exc:
        yield ("error", {"message": str(exc)})

    latency = int((time.monotonic() - started) * 1000)
    merged["latency_ms"] = latency
    yield (
        "done",
        {
            "latency_ms": latency,
            "need_clarification": bool(merged.get("need_clarification")),
            "clarification_question": merged.get("clarification_question"),
        },
    )
