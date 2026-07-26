from __future__ import annotations

import json
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_write_lock = threading.Lock()


def __getattr__(name: str):
    if name == "MAX_LOG_BYTES":
        return get_settings().logging_max_bytes
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def app_log_dir() -> Path:
    return get_settings().logging_dir_path


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def resolve_app_log_path(
    *,
    day: str | None = None,
    log_dir: Path | None = None,
    max_bytes: int | None = None,
) -> Path:
    """Return active app log path: YYYY-MM-DD.log, then YYYY-MM-DD_N.log when full."""
    day = day or _today_str()
    directory = log_dir if log_dir is not None else app_log_dir()
    if max_bytes is None:
        max_bytes = get_settings().logging_max_bytes
    directory.mkdir(parents=True, exist_ok=True)

    candidates = [directory / f"{day}.log"]
    index = 1
    while True:
        path = candidates[-1]
        if not path.exists() or path.stat().st_size < max_bytes:
            return path
        next_path = directory / f"{day}_{index}.log"
        candidates.append(next_path)
        index += 1


def _append_app_log(line: str) -> None:
    try:
        text = line if line.endswith("\n") else line + "\n"
        with _write_lock:
            path = resolve_app_log_path()
            with path.open("a", encoding="utf-8") as f:
                f.write(text)
    except Exception:
        # File logging must never break request handling.
        pass


def format_log_line(level: str, message: str, *, ts: str | None = None) -> str:
    """Format: `INFO 2026-07-26 11:01:00 :     Waiting for application startup.`"""
    return f"{level} {ts or _now_str()} :     {message}"


def _contains_newline(value: Any) -> bool:
    if isinstance(value, str):
        return "\n" in value
    if isinstance(value, dict):
        return any(_contains_newline(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_newline(v) for v in value)
    return False


def _fmt_value(value: Any) -> str:
    if isinstance(value, str):
        if "\n" in value:
            # Keep real newlines for readability (SQL / prompts).
            return value
        if any(ch in value for ch in " \t=\"'"):
            return json.dumps(value, ensure_ascii=False)
        return value
    try:
        if _contains_newline(value):
            dumped = json.dumps(
                value, ensure_ascii=False, indent=2, default=str
            )
            # Expand escaped newlines inside JSON string values.
            return dumped.replace("\\n", "\n")
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return repr(value)


def _build_message(event: str, fields: dict[str, Any]) -> str:
    parts = [event]
    request_id = fields.pop("request_id", None)
    if request_id is None:
        request_id = get_request_id()
    if request_id is not None:
        parts.append(f"request_id={request_id}")

    detail = fields.pop("detail", None)
    for key, value in fields.items():
        if value is None:
            continue
        formatted = _fmt_value(value)
        if "\n" in formatted:
            parts.append(f"{key}=\n{formatted}")
        else:
            parts.append(f"{key}={formatted}")
    if detail is not None:
        formatted = _fmt_value(detail)
        if "\n" in formatted:
            parts.append(f"detail=\n{formatted}")
        else:
            parts.append(f"detail={formatted}")
    return " ".join(parts)


def log_event(level: str, event: str, **fields: Any) -> None:
    message = _build_message(event, dict(fields))
    line = format_log_line(level, message)
    print(line, flush=True)
    _append_app_log(line + "\n")


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
