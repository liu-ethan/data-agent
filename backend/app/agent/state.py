from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:
    question: str
    session_id: str
    user_id: str
    user_role: str
    request_id: str
    trace_id: str
    generated_sql: str | None = None
    columns: list[str] | None = None
    rows: list[dict] | None = None
    answer: str | None = None
    error: str | None = None
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: int | None = None
