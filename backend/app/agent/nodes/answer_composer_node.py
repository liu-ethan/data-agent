from __future__ import annotations

from app.agent import answer_composer
from app.agent.state import AgentState


def answer_composer_node(state: AgentState) -> dict:
    if state.get("error"):
        return {}
    answer = answer_composer.compose_answer(
        state.get("question") or "",
        state.get("columns") or [],
        state.get("rows") or [],
        is_write=bool(state.get("is_write")),
        affected_rows=state.get("affected_rows"),
    )
    return {"answer": answer}
