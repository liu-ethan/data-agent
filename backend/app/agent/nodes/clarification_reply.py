from __future__ import annotations

from app.agent.state import AgentState


def clarification_reply(state: AgentState) -> dict:
    answer = (
        state.get("clarification_question")
        or "请补充指标与时间范围后再问我。"
    )
    return {"answer": answer}
