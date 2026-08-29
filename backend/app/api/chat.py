from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from backend.app.api.auth import CurrentUser
from backend.app.coordinator.graph import upsert_thread
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
            "SELECT user_id FROM thread WHERE thread_id = ?",
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
    return cleaned[:10] or "新会话"


def default_title_fn(message: str) -> str:
    try:
        from backend.app.config import load_settings

        settings = load_settings()
        url = settings.llm.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.llm.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.llm.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "用不超过10个字概括下面这个问题，不要标点、不要引号，只输出标题：\n"
                        + message
                    ),
                }
            ],
            "max_tokens": 32,
            "temperature": 0,
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        from backend.app.llm.client import llm_message_text

        return clip_title(llm_message_text(resp.json())) or clip_title(message)
    except Exception:  # noqa: BLE001
        return clip_title(message)


def summarize_title(app: Any, thread_id: str, user_id: str, message: str) -> None:
    with sqlite3.connect(app.state.runtime_db) as conn:
        row = conn.execute(
            "SELECT title FROM thread WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        ).fetchone()
    if row is None:
        return
    if row[0] not in (None, "", "新会话"):
        return
    fn = getattr(app.state, "title_fn", None) or default_title_fn
    try:
        raw = fn(message)
    except Exception:  # noqa: BLE001
        raw = message
    title = clip_title(raw or message)
    with sqlite3.connect(app.state.runtime_db) as conn:
        conn.execute(
            "UPDATE thread SET title = ? WHERE thread_id = ? AND user_id = ?",
            (title, thread_id, user_id),
        )
        conn.commit()


def coordinator_sse(request: Request, ctx: RuntimeContext, message: str, *, resume: Any = None):
    yield format_sse("status", {"stage": "running"})
    invoke_fn = request.app.state.invoke_fn
    try:
        result = invoke_fn(request.app.state.graph, message, ctx, resume=resume)
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("coordinator invoke failed")
        yield format_sse("error", {"message": public_error(exc)})
        yield format_sse("done", {})
        return
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
    title = "新会话"
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
                    "tenant_id": "default",
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
            """SELECT thread_id, user_id, title, created_at, updated_at
               FROM thread WHERE user_id = ? ORDER BY updated_at DESC""",
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
            "DELETE FROM thread WHERE thread_id = ? AND user_id = ?",
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
