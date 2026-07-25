from __future__ import annotations

from app.agent.memory.merge import merge_slots
from app.agent.state import AgentState

BUSINESS_SLOT_KEYS = frozenset(
    {
        "metrics",
        "time_range",
        "group_by",
        "top_n",
        "filters",
        "write_intent",
    }
)


def _business_slots_only(slots: dict | None) -> dict | None:
    if not slots:
        return slots
    return {k: v for k, v in slots.items() if k in BUSINESS_SLOT_KEYS}


def slot_merge(state: AgentState) -> dict:
    merged = merge_slots(
        _business_slots_only(state.get("session_slots")),
        _business_slots_only(state.get("slots")),
        state.get("user_preferences"),
    )
    return {"slots": merged}
