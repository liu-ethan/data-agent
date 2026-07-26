from __future__ import annotations

from app.agent.memory import store
from app.agent.state import AgentState


def memory_load(state: AgentState) -> dict:
    session_id = state["session_id"]
    user_id = str(state["user_id"])
    try:
        store.ensure_session(session_id, user_id)
    except store.MemoryError as exc:
        return {"error": str(exc)}
    return {
        "session_slots": store.load_last_turn_slots(session_id, user_id),
        "user_preferences": store.load_preferences(user_id),
        "recent_summaries": store.load_recent_summaries(user_id),
        "react_step": 0,
        "repaired": bool(state.get("repaired", False)),
    }
