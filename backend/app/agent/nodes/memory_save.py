from __future__ import annotations

from app.agent.memory import store
from app.agent.memory.summarize import build_result_summary
from app.agent.memory.title import generate_session_title
from app.agent.memory.turn_display import build_display_payload
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

    question = state.get("question") or ""
    has_sql = bool(state.get("generated_sql"))
    guardrail_passed = has_sql and not state.get("error")
    if state.get("rows") is not None and not state.get("is_write"):
        guardrail_passed = True
    display = build_display_payload(
        columns=state.get("columns"),
        rows=state.get("rows"),
        chart=state.get("chart"),
        repaired=bool(state.get("repaired")),
        guardrail_passed=guardrail_passed,
        trace=list(state.get("agent_trace") or []),
    )
    try:
        store.save_turn(
            session_id=session_id,
            user_id=user_id,
            question=question,
            intent=state.get("intent") or "",
            sql_text=state.get("generated_sql"),
            slots=slots,
            result_summary=result_summary,
            display=display,
        )
    except store.MemoryError:
        return {}

    out: dict = {}
    try:
        existing = store.get_session_title(session_id, user_id)
        if not existing:
            title = generate_session_title(question, result_summary)
            if store.set_session_title_if_empty(session_id, user_id, title):
                out["session_title"] = title[:10]
    except store.MemoryError:
        pass

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
    return out
