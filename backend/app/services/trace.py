"""Trace context and a redacting in-memory trace sink used by API and tests."""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from ..models import TraceContext

_current_trace: ContextVar[TraceContext | None] = ContextVar("current_trace", default=None)
trace_records: list[dict[str, Any]] = []
logger = logging.getLogger("data_runtime_agent")


def hash_sql(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def new_trace(request_id: str, thread_id: str, user_id: str, route: str) -> TraceContext:
    context = TraceContext(request_id=request_id, thread_id=thread_id, user_id=user_id,
                           route=route, started_at=datetime.now(timezone.utc))
    _current_trace.set(context)
    return context


def current_trace() -> TraceContext | None:
    return _current_trace.get()


def record(event: str, **fields: Any) -> None:
    context = current_trace()
    safe = {"event": event, "trace_id": context.trace_id if context else None}
    for key, value in fields.items():
        sensitive_key = key.lower()
        if (any(secret in sensitive_key for secret in
                ("password", "secret", "phone", "id_number", "api_key"))
                or sensitive_key in {"token", "access_token", "refresh_token", "authorization"}):
            safe[key] = "<redacted>"
        elif key in {"rows", "result", "result_set", "prompt"}:
            safe[key] = "<omitted>"
        else:
            safe[key] = value
    trace_records.append(safe)
    logger.info("trace_event=%s trace_id=%s", event, safe["trace_id"])


@contextmanager
def timed(event: str, **fields: Any) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
    result: dict[str, Any] = {}
    try:
        yield result
    finally:
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        record(event, **fields, duration_ms=result["duration_ms"])
