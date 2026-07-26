from app.agent.memory.merge import merge_slots
from app.agent.memory.store import (
    MemoryError,
    append_summary,
    assert_session_owner,
    create_session,
    delete_session,
    ensure_session,
    get_session_title,
    list_sessions,
    list_turns,
    load_last_turn_slots,
    load_preferences,
    load_recent_summaries,
    save_turn,
    set_session_title_if_empty,
    update_preferences_from_slots,
)
from app.agent.memory.summarize import (
    build_result_summary,
    merge_preferences,
    strip_sensitive,
)
from app.config import get_settings


def __getattr__(name: str):
    if name == "MAX_TURNS_PER_SESSION":
        return get_settings().memory_max_turns_per_session
    if name == "MAX_SUMMARIES_PER_USER":
        return get_settings().memory_max_summaries_per_user
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MAX_SUMMARIES_PER_USER",
    "MAX_TURNS_PER_SESSION",
    "MemoryError",
    "append_summary",
    "assert_session_owner",
    "build_result_summary",
    "create_session",
    "delete_session",
    "ensure_session",
    "get_session_title",
    "list_sessions",
    "list_turns",
    "load_last_turn_slots",
    "load_preferences",
    "load_recent_summaries",
    "merge_preferences",
    "merge_slots",
    "save_turn",
    "set_session_title_if_empty",
    "strip_sensitive",
    "update_preferences_from_slots",
]
