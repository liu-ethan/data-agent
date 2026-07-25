from __future__ import annotations

from app.agent.state import AgentState


def sql_guardrail_node(state: AgentState) -> dict:
    from app.tools.builtins import ensure_builtins_registered
    from app.tools.schemas import ToolContext

    reg = ensure_builtins_registered()
    ctx = ToolContext(
        request_id=state["request_id"],
        trace_id=state["trace_id"],
        session_id=state["session_id"],
        user_id=state["user_id"],
        user_role=state["user_role"],
        node="SQLGuardrail",
    )
    result = reg.invoke(
        "validate_sql",
        {"sql": state.get("generated_sql") or ""},
        context=ctx,
    )
    out = {"tool_events": result.events}
    if not result.ok:
        out["error"] = result.error or "SQL blocked by guardrail"
    else:
        out["error"] = None
    return out
