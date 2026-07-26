from __future__ import annotations

from app.agent.llm import chat_completion
from app.agent.memory.summarize import strip_sensitive
from app.config import get_settings
from app.prompts import render


def generate_session_title(question: str, result_summary: str | None) -> str:
    max_chars = get_settings().memory_session_title_max_chars
    q = strip_sensitive(question or "").strip()
    summary = strip_sensitive(result_summary or "").strip()
    fallback = (q[:max_chars] if q else "新会话")
    parts = render(
        "session_title",
        max_chars=max_chars,
        question=q,
        summary=summary or "（无）",
    )
    messages = [
        {"role": "system", "content": parts["system"]},
        {"role": "user", "content": parts["user"]},
    ]
    try:
        raw = chat_completion(messages)
    except Exception:
        return fallback
    title = strip_sensitive(raw or "").strip().strip("\"'「」").replace("\n", "")
    title = title[:max_chars]
    return title or fallback
