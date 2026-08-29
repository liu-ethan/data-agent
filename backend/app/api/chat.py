from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from backend.app.api.auth import CurrentUser
from backend.app.coordinator.graph import upsert_thread
from backend.app.coordinator.progress import think_listener
from backend.app.resources.domain import empty_thread_title, tenant_id, think_steps, title_max_chars
from backend.app.resources.prompts import render_prompt
from backend.app.resources.sql import load_sql
from backend.app.runtime.context import build_runtime_context
from backend.app.types import RuntimeContext

router = APIRouter()
_LOG = logging.getLogger(__name__)
_PUNCT = set("「」『』，。！？、.,!?;:：·—()[]{}<>《》'\"")


class ThreadCreate(BaseModel):
    title: str | None = None


_EMPTY_THREAD = ThreadCreate()


class MessageCreate(BaseModel):
    message: str


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def public_error(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return "模型输出无法解析，请再试一次。"
    lowered = text.lower()
    if "validation error" in lowered or "expecting value" in lowered:
        return "模型输出无法解析，请再试一次。"
    return text or "无法完成该请求。"


def request_time(request: Request) -> str:
    stamped = getattr(request.app.state, "request_time_utc", None)
    if stamped:
        return str(stamped)
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def owned_thread(request: Request, thread_id: str, user_id: str) -> None:
    with sqlite3.connect(request.app.state.runtime_db) as conn:
        row = conn.execute(
            load_sql("threads.select_thread_owner"),
            (thread_id,),
        ).fetchone()
    if row is None or row[0] != user_id:
        raise HTTPException(status_code=404, detail="thread not found")


def build_ctx(request: Request, user_id: str, thread_id: str) -> RuntimeContext:
    return build_runtime_context(
        user_id,
        thread_id,
        request_time(request),
        timezone=request.app.state.timezone,
        users_db=request.app.state.users_db,
        catalog_version=getattr(request.app.state, "catalog_version", 1),
    )


def clip_title(text: str) -> str:
    from backend.app.llm.client import strip_reasoning

    cleaned = "".join(ch for ch in strip_reasoning(text) if ch not in _PUNCT)
    cleaned = "".join(cleaned.split())
    return cleaned[: title_max_chars()] or empty_thread_title()


def default_title_fn(message: str) -> str:
    try:
        from backend.app.config import load_settings
        from backend.app.llm.client import ChatLlm

        llm = ChatLlm(load_settings().llm)
        return clip_title(llm.summarize_title(message, render_prompt("coordinator.title"))) or clip_title(
            message
        )
    except Exception:  # noqa: BLE001
        return clip_title(message)


def summarize_title(app: Any, thread_id: str, user_id: str, message: str) -> None:
    with sqlite3.connect(app.state.runtime_db) as conn:
        row = conn.execute(
            load_sql("threads.select_thread_title"),
            (thread_id, user_id),
        ).fetchone()
    if row is None:
        return
    if row[0] not in (None, "", empty_thread_title()):
        return
    fn = getattr(app.state, "title_fn", None) or default_title_fn
    try:
        raw = fn(message)
    except Exception:  # noqa: BLE001
        raw = message
    title = clip_title(raw or message)
    with sqlite3.connect(app.state.runtime_db) as conn:
        conn.execute(
            load_sql("threads.update_thread_title"),
            (title, thread_id, user_id),
        )
        conn.commit()


def coordinator_sse(request: Request, ctx: RuntimeContext, message: str, *, resume: Any = None):
    yield format_sse("status", {"stage": "running"})
    start = think_steps().get("start")
    if start:
        yield format_sse("think", {"node": "start", **start})

    events: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()

    def on_think(payload: dict[str, str]) -> None:
        events.put(("think", payload))

    def worker() -> None:
        try:
            with think_listener(on_think):
                result = request.app.state.invoke_fn(
                    request.app.state.graph, message, ctx, resume=resume
                )
            events.put(("ok", result))
        except Exception as exc:  # noqa: BLE001
            events.put(("err", exc))
        finally:
            events.put(("end", None))

    threading.Thread(target=worker, daemon=True).start()
    result: dict[str, Any] | None = None
    error: Exception | None = None
    while True:
        kind, payload = events.get()
        if kind == "think":
            yield format_sse("think", payload)
        elif kind == "ok":
            result = payload
        elif kind == "err":
            error = payload
        else:
            break

    if error is not None:
        _LOG.exception("coordinator invoke failed", exc_info=error)
        yield format_sse("error", {"message": public_error(error)})
        yield format_sse("done", {})
        return
    assert result is not None
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        first = interrupts[0]
        payload = first.value if hasattr(first, "value") else first
        yield format_sse("interrupt", payload)
        yield format_sse("done", {"interrupted": True})
        return
    if result.get("result_id"):
        yield format_sse("result_ref", {"result_id": result["result_id"]})
    answer = result.get("answer") or ""
    if answer:
        yield format_sse("token", {"text": answer})
    done: dict[str, Any] = {"answer": answer}
    if result.get("operation_id"):
        done["operation_id"] = result["operation_id"]
    yield format_sse("done", done)


def sse_response(chunks):
    return StreamingResponse(chunks, media_type="text/event-stream")


@router.post("/api/threads")
def create_thread(request: Request, user: CurrentUser, body: ThreadCreate = _EMPTY_THREAD):
    del body
    thread_id = str(uuid.uuid4())
    title = empty_thread_title()
    upsert_thread(request.app.state.runtime_db, thread_id, user.user_id, title, request_time(request))
    graph = request.app.state.graph
    if graph is not None and hasattr(graph, "update_state"):
        graph.update_state(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "user_id": user.user_id,
                    "request_time_utc": request_time(request),
                    "timezone": request.app.state.timezone,
                    "role": user.role,
                    "tenant_id": tenant_id(),
                }
            },
            {"message": ""},
        )
    return {"thread_id": thread_id, "title": title}


@router.get("/api/threads")
def list_threads(request: Request, user: CurrentUser):
    with sqlite3.connect(request.app.state.runtime_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            load_sql("threads.list_threads"),
            (user.user_id,),
        ).fetchall()
    return {
        "threads": [
            {
                "thread_id": row["thread_id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    }


@router.delete("/api/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: str, request: Request, user: CurrentUser):
    with sqlite3.connect(request.app.state.runtime_db) as conn:
        cur = conn.execute(
            load_sql("threads.delete_thread"),
            (thread_id, user.user_id),
        )
        conn.commit()
        deleted = cur.rowcount
    if not deleted:
        raise HTTPException(status_code=404, detail="thread not found")
    return Response(status_code=204)


@router.post("/api/threads/{thread_id}/messages")
def post_message(
    thread_id: str,
    body: MessageCreate,
    request: Request,
    user: CurrentUser,
    background: BackgroundTasks,
):
    owned_thread(request, thread_id, user.user_id)
    ctx = build_ctx(request, user.user_id, thread_id)
    background.add_task(summarize_title, request.app, thread_id, user.user_id, body.message)
    return sse_response(coordinator_sse(request, ctx, body.message))
