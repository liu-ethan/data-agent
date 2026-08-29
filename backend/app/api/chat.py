from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.api.auth import CurrentUser
from backend.app.coordinator.graph import upsert_thread
from backend.app.runtime.context import build_runtime_context
from backend.app.types import RuntimeContext

router = APIRouter()


class ThreadCreate(BaseModel):
    title: str | None = None


_EMPTY_THREAD = ThreadCreate()


class MessageCreate(BaseModel):
    message: str


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


def coordinator_sse(request: Request, ctx: RuntimeContext, message: str, *, resume: Any = None):
    yield format_sse("status", {"stage": "running"})
    invoke_fn = request.app.state.invoke_fn
    try:
        result = invoke_fn(request.app.state.graph, message, ctx, resume=resume)
    except Exception as exc:  # noqa: BLE001
        yield format_sse("error", {"message": str(exc)})
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
    thread_id = str(uuid.uuid4())
    title = body.title or "新会话"
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


@router.post("/api/threads/{thread_id}/messages")
def post_message(thread_id: str, body: MessageCreate, request: Request, user: CurrentUser):
    owned_thread(request, thread_id, user.user_id)
    ctx = build_ctx(request, user.user_id, thread_id)
    return sse_response(coordinator_sse(request, ctx, body.message))
