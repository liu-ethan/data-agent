from __future__ import annotations

from app.agent.state import AgentState


def sql_guardrail_node(state: AgentState) -> dict:
    from app.security.sql_guardrail import check_sql

    sql = state.get("generated_sql") or ""
    result = check_sql(sql, user_role=state["user_role"])
    if not result.ok:
        return {"error": result.reason or "SQL blocked by guardrail"}
    return {"error": None}
