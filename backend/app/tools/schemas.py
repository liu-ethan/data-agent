from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk_level: str  # low|medium|high
    permission_policy: str  # allow|allow_after_validation|deny
    enabled: bool = True
    input_schema: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolContext:
    request_id: str
    trace_id: str
    session_id: str
    user_id: str
    user_role: str
    node: str


@dataclass
class ToolResult:
    ok: bool
    data: dict | None = None
    error: str | None = None
    events: list[dict] = field(default_factory=list)  # {event, data}
