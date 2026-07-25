from __future__ import annotations

from app.agent.chart_planner import plan_chart
from app.agent.state import AgentState


def chart_planner_node(state: AgentState) -> dict:
    if state.get("error") or state.get("is_write"):
        return {"chart": None}
    columns = state.get("columns") or []
    rows = state.get("rows") or []
    if not columns or not rows:
        return {"chart": None}
    chart = plan_chart(
        state.get("question") or "",
        columns,
        rows,
        slots=state.get("slots"),
    )
    return {"chart": chart}
