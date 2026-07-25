from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    # 必填（入口注入）
    question: str
    session_id: str
    user_id: str
    user_role: str
    request_id: str
    trace_id: str

    # 意图与分流
    intent: str | None
    intent_confidence: float | None
    intent_summary: str | None
    route_mode: str | None  # "react" | "coordinator"
    route_source: str | None  # Phase 3 恒为 "model"
    slots: dict | None

    # 澄清
    need_clarification: bool
    clarification_question: str | None

    # Schema / SQL / 结果
    relevant_tables: list[str]
    relevant_columns: dict
    metric_specs: list[dict]
    generated_sql: str | None
    columns: list[str]
    rows: list[dict]
    answer: str | None
    error: str | None

    agent_trace: list[dict]
    latency_ms: int
    repaired: bool
