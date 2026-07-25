from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def log_event(level: str, event: str, **fields: Any) -> None:
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": level,
        "request_id": get_request_id(),
        "event": event,
    }
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:12]}"
        set_request_id(rid)
        started = time.perf_counter()
        log_event(
            "INFO",
            "request_start",
            path=str(request.url.path),
            method=request.method,
        )
        response = await call_next(request)
        ms = int((time.perf_counter() - started) * 1000)
        log_event(
            "INFO",
            "request_end",
            path=str(request.url.path),
            status=response.status_code,
            latency_ms=ms,
        )
        response.headers["X-Request-Id"] = rid
        return response
