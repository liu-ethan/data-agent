from __future__ import annotations

from app.agent import sql_generator
from app.agent.state import AgentState


def sql_generator_node(state: AgentState) -> dict:
    relevant_columns = state.get("relevant_columns") or {}
    schema = [
        {"name": table, "columns": relevant_columns.get(table, [])}
        for table in state.get("relevant_tables") or []
    ]
    sql = sql_generator.generate_sql(
        state.get("question") or "",
        schema,
        state.get("metric_specs") or [],
        state.get("slots") or {},
        state["user_role"],
    )
    return {"generated_sql": sql}
