from __future__ import annotations

import time
from collections.abc import Callable
from app.tools.audit import append_audit
from app.tools.schemas import ToolContext, ToolResult, ToolSpec

Handler = Callable[[dict, ToolContext], ToolResult]

_registry_singleton: ToolRegistry | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Handler] = {}

    def register(self, spec: ToolSpec, handler: Handler) -> None:
        self._tools[spec.name] = spec
        self._handlers[spec.name] = handler

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def invoke(self, name: str, args: dict, *, context: ToolContext) -> ToolResult:
        events: list[dict] = []
        t0 = time.monotonic()
        spec = self._tools.get(name)

        if spec is None or not spec.enabled:
            latency = int((time.monotonic() - t0) * 1000)
            events.append(
                {
                    "event": "permission_deny",
                    "data": {"tool": name, "reason": "not_found_or_disabled", "node": context.node},
                }
            )
            append_audit(
                {
                    "event": "permission_deny",
                    "tool": name,
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "session_id": context.session_id,
                    "user_id": context.user_id,
                    "user_role": context.user_role,
                    "node": context.node,
                    "latency_ms": latency,
                }
            )
            return ToolResult(ok=False, error="tool not found or disabled", events=events)

        events.append(
            {
                "event": "tool_start",
                "data": {"tool": name, "risk_level": spec.risk_level, "node": context.node},
            }
        )
        append_audit(
            {
                "event": "tool_start",
                "tool": name,
                "risk_level": spec.risk_level,
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "session_id": context.session_id,
                "user_id": context.user_id,
                "user_role": context.user_role,
                "node": context.node,
            }
        )

        if spec.permission_policy == "deny":
            latency = int((time.monotonic() - t0) * 1000)
            events.append(
                {
                    "event": "permission_deny",
                    "data": {"tool": name, "node": context.node},
                }
            )
            events.append(
                {
                    "event": "tool_end",
                    "data": {
                        "tool": name,
                        "status": "error",
                        "latency_ms": latency,
                        "node": context.node,
                    },
                }
            )
            append_audit(
                {
                    "event": "tool_end",
                    "tool": name,
                    "status": "error",
                    "latency_ms": latency,
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "session_id": context.session_id,
                    "user_id": context.user_id,
                    "user_role": context.user_role,
                    "node": context.node,
                    "detail": {"reason": "permission_deny"},
                }
            )
            return ToolResult(ok=False, error="permission denied", events=events)

        try:
            raw = self._handlers[name](args, context)
        except Exception as exc:
            raw = ToolResult(ok=False, error=str(exc).splitlines()[0][:200])

        merged = list(events)
        for item in raw.events:
            merged.append(item)

        latency = int((time.monotonic() - t0) * 1000)
        status = "ok" if raw.ok else "error"
        merged.append(
            {
                "event": "tool_end",
                "data": {
                    "tool": name,
                    "status": status,
                    "latency_ms": latency,
                    "node": context.node,
                },
            }
        )
        detail: dict = {"ok": raw.ok, "error": raw.error}
        if raw.data:
            for key in (
                "risk_level",
                "affected_rows",
                "is_write",
                "sql",
                "sql_fingerprint",
            ):
                if key in raw.data:
                    detail[key] = raw.data[key]
        append_audit(
            {
                "event": "tool_end",
                "tool": name,
                "status": status,
                "latency_ms": latency,
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "session_id": context.session_id,
                "user_id": context.user_id,
                "user_role": context.user_role,
                "node": context.node,
                "detail": detail,
            }
        )
        raw.events = merged
        return raw


def get_registry() -> ToolRegistry:
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = ToolRegistry()
    return _registry_singleton
