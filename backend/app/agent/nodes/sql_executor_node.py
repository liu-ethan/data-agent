from __future__ import annotations

from app.agent.sql_executor import execute_sql
from app.agent.state import AgentState


def sql_executor_node(state: AgentState) -> dict:
    try:
        columns, rows = execute_sql(
            state.get("generated_sql") or "",
            user_role=state["user_role"],
        )
        return {"columns": columns, "rows": rows, "error": None}
    except Exception as exc:
        return {"error": str(exc)}
