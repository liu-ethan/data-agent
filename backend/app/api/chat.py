from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.pipeline import iter_pipeline_events
from app.agent.state import AgentState
from app.auth.deps import get_current_user
from app.log.logging import get_request_id

router = APIRouter(tags=["chat"])


class ChatBody(BaseModel):
    question: str
    session_id: str = "default"


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _iter_sse(state: AgentState) -> Iterator[str]:
    for event, data in iter_pipeline_events(state):
        yield _format_sse(event, data)


@router.post("/chat")
def chat(
    body: ChatBody,
    user: Annotated[dict, Depends(get_current_user)],
) -> StreamingResponse:
    request_id = get_request_id() or f"req_{uuid.uuid4().hex[:12]}"
    trace_id = request_id
    state = AgentState(
        question=body.question,
        session_id=body.session_id,
        user_id=user["id"],
        user_role=user["role"],
        request_id=request_id,
        trace_id=trace_id,
    )
    return StreamingResponse(
        _iter_sse(state),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
