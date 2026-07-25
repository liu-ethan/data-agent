from __future__ import annotations

from app.agent.memory import store
from app.agent.memory.summarize import build_result_summary
from app.agent.state import AgentState


def memory_save(state: AgentState) -> dict:
    session_id = state["session_id"]
    user_id = str(state["user_id"])
    slots = state.get("slots") or {}
    clarification = (
        state.get("clarification_question")
        if state.get("need_clarification")
        else None
    )
    result_summary = build_result_summary(
        answer=state.get("answer"),
        error=state.get("error"),
        clarification=clarification,
    )

    try:
        store.save_turn(
            session_id=session_id,
            user_id=user_id,
            question=state.get("question") or "",
            intent=state.get("intent") or "",
            sql_text=state.get("generated_sql"),
            slots=slots,
            result_summary=result_summary,
        )
    except store.MemoryError:
        return {}

    if (
        state.get("answer")
        and not state.get("error")
        and not state.get("need_clarification")
    ):
        store.update_preferences_from_slots(user_id, slots)
        store.append_summary(
            user_id=user_id,
            session_id=session_id,
            question_summary=state.get("question") or "",
            answer_summary=result_summary,
            metrics=list(slots.get("metrics") or []),
            filters=dict(slots.get("filters") or {}),
        )
    return {}
