"""HTTP middleware that sets up a TraceContext for every incoming request.

Spec 00 §7 requires a trace_id to be visible across API logs and downstream
calls.  The middleware stamps a TraceContext on the per-task ContextVar
before the route runs; endpoints then call ``bind_trace`` once the
authenticated identity and thread id are known.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ..models import TraceContext
from ..services.trace import _current_trace


class TraceMiddleware(BaseHTTPMiddleware):
    """Stamp a TraceContext on every request and echo X-Request-ID on response."""

    HEADER = "X-Request-ID"

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(self.HEADER) or f"req_{uuid4().hex[:16]}"
        context = TraceContext(
            trace_id=f"trace_{uuid4().hex}",
            request_id=request_id,
            thread_id="pending",
            user_id="anonymous",
            route=f"{request.method} {request.url.path}",
            started_at=datetime.now(UTC),
        )
        token = _current_trace.set(context)
        try:
            response = await call_next(request)
        finally:
            _current_trace.reset(token)
        response.headers[self.HEADER] = request_id
        return response
