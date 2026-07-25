from __future__ import annotations

from app.agent.state import AgentState


def route_emit(state: AgentState) -> dict:
    mode = state.get("route_mode") or "react"
    if mode not in ("react", "coordinator"):
        mode = "react"
    return {"route_mode": mode, "route_source": "model"}
