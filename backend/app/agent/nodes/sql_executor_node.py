from __future__ import annotations

from app.agent.state import AgentState


def sql_executor_node(state: AgentState) -> dict:
    from app.tools.builtins import ensure_builtins_registered
    from app.tools.schemas import ToolContext

    reg = ensure_builtins_registered()
    ctx = ToolContext(
        request_id=state["request_id"],
        trace_id=state["trace_id"],
        session_id=state["session_id"],
        user_id=state["user_id"],
        user_role=state["user_role"],
        node="SQLExecutor",
    )
    result = reg.invoke(
        "execute_sql",
        {"sql": state.get("generated_sql") or ""},
        context=ctx,
    )
    out = {"tool_events": result.events}
    if not result.ok:
        out["error"] = result.error or "SQL execution failed"
        return out
    data = result.data or {}
    if data.get("is_write"):
        out.update(
            {
                "columns": [],
                "rows": [],
                "is_write": True,
                "affected_rows": data.get("affected_rows"),
                "error": None,
            }
        )
    else:
        out.update(
            {
                "columns": data.get("columns") or [],
                "rows": data.get("rows") or [],
                "is_write": False,
                "affected_rows": None,
                "error": None,
            }
        )
    return out
