from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.api.auth import CurrentUser
from backend.app.api.chat import build_ctx, coordinator_sse, owned_thread, sse_response

router = APIRouter()


class ResumeRequest(BaseModel):
    approved: bool | None = None
    user_id: str | None = None
    selected_id: str | None = None

    model_config = {"extra": "allow"}


@router.post("/api/threads/{thread_id}/resume")
def resume_interrupt(thread_id: str, body: ResumeRequest, request: Request, user: CurrentUser):
    owned_thread(request, thread_id, user.user_id)
    ctx = build_ctx(request, user.user_id, thread_id)
    payload: dict[str, Any] = body.model_dump(exclude_none=True)
    if "user_id" not in payload:
        payload["user_id"] = user.user_id
    return sse_response(coordinator_sse(request, ctx, "", resume=payload))
