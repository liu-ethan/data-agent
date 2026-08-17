"""Fire-and-forget thread title generation.

A successful first run triggers a cheap LLM call that summarises the
exchange into a ≤10-character Chinese title. The result is persisted as
``thread_titles.title`` and emitted as a ``thread.title_updated`` SSE
event so the sidebar can pick it up next time.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from ..services.trace import record
from ._events import EVENT_SCHEMA_VERSION
from .state import ThreadTitleDraft

if TYPE_CHECKING:
    from .main_graph import RuntimeGraph, _Run


_TITLE_PROMPT = (
    "You are a concise thread-title writer for an ecommerce data analyst. "
    "Summarize the user's first question and the assistant's answer into a "
    "short Chinese title (no more than 10 Chinese characters, no punctuation, "
    "no quotes)."
)


async def generate_thread_title(runtime: "RuntimeGraph", thread_id: str,
                                user_id: str, question: str,
                                answer: str) -> None:
    if not runtime.llm or not runtime.persistence:
        return
    try:
        draft, _ = await runtime.llm.structured(
            system=_TITLE_PROMPT,
            user=json.dumps({"question": question, "answer": answer[:400]},
                            ensure_ascii=False),
            schema=ThreadTitleDraft,
            purpose="thread_title",
            temperature=0.2,
            prompt_version="thread_title_v1",
        )
        title = draft.title.strip()
        if not title:
            return
        runtime.persistence.save_thread_title(thread_id, title)
        await asyncio.to_thread(
            runtime.persistence.append_event,
            f"title:{thread_id}", user_id,
            {
                "event": "thread.title_updated",
                "request_id": thread_id,
                "thread_id": thread_id,
                "node": None,
                "action": None,
                "status": "SUCCEEDED",
                "duration_ms": None,
                "error_code": None,
                "thread_title": title,
                "schema_version": EVENT_SCHEMA_VERSION,
            })
    except Exception as exc:  # noqa: BLE001 - title generation is best-effort
        record("thread_title.failed", thread_id=thread_id,
               error_type=type(exc).__name__, error_code=str(exc))


def maybe_generate_thread_title(runtime: "RuntimeGraph", run: "_Run",
                                final, answer: str) -> None:
    """Schedule the title generator if this is the first successful run."""
    if not runtime.llm or not runtime.persistence:
        return
    thread_id = final.thread_id
    user_id = final.user_id
    if runtime.persistence.load_thread_title(thread_id):
        return
    question = final.task_frame.question if final.task_frame else ""
    if not question:
        return
    asyncio.create_task(generate_thread_title(
        runtime, thread_id, user_id, question, answer))
