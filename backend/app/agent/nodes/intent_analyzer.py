from __future__ import annotations

import json
import re

from app.agent.llm import chat_completion
from app.agent.state import AgentState
from app.agent.vocab import (
    DIMENSION_VOCAB,
    INTENTS,
    METRIC_VOCAB,
    TIME_RANGE_VOCAB,
)

_ROUTE_MODES = frozenset({"react", "coordinator"})
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_CONTEXT_VALUE_LIMIT = 300


def _context_line(label: str, value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > _CONTEXT_VALUE_LIMIT:
        serialized = f"{serialized[:_CONTEXT_VALUE_LIMIT]}…"
    return f"{label}: {serialized}"


def build_intent_prompt(
    question: str,
    *,
    session_slots: dict | None = None,
    preferences: dict | None = None,
    recent_summaries: list[dict] | None = None,
) -> list[dict]:
    intent_list = ", ".join(
        sorted(i for i in INTENTS if i != "unknown")
    ) + ", unknown"
    metrics = ", ".join(sorted(METRIC_VOCAB))
    dimensions = ", ".join(sorted(DIMENSION_VOCAB))
    time_ranges = ", ".join(sorted(TIME_RANGE_VOCAB))

    system = f"""你是电商经营分析问题意图分析器。根据用户问题输出 JSON。

intent 封闭枚举: {intent_list}

METRIC 词表（slots.metrics 元素）: {metrics}
DIMENSION / group_by 词表: {dimensions}
TIME_RANGE 词表（slots.time_range）: {time_ranges}

route_mode 说明:
- react: 单指标、TopN、路径清晰
- coordinator: 多指标对比、归因、多步协作

默认口径: GMV 默认支付金额，不必为此澄清。

输出 JSON 对象字段:
- intent (string)
- confidence (number 0-1)
- summary (string)
- route_mode ("react" | "coordinator")
- slots: {{ "metrics": string[], "time_range": string|null, "group_by": string[], "top_n": number|null, "write_intent": bool, "filters": object|null }}
- need_clarification (bool)
- clarification_question (string|null)
"""
    context_lines = []
    if session_slots:
        context_lines.append(_context_line("会话槽位", session_slots))
    if preferences:
        context_lines.append(_context_line("用户偏好", preferences))
    if recent_summaries:
        context_lines.append(_context_line("近期分析", recent_summaries))
    user_content = question
    if context_lines:
        user_content += (
            "\n\n参考上下文（仅用于理解追问，不覆盖用户明确表达）:\n"
            + "\n".join(context_lines)
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def _extract_json_object(text: str) -> dict | None:
    raw = text.strip()
    fence = _JSON_FENCE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _fallback_intent() -> dict:
    return {
        "intent": "unknown",
        "intent_confidence": None,
        "intent_summary": None,
        "route_mode": "react",
        "slots": {},
        "need_clarification": False,
        "clarification_question": None,
    }


def _normalize_parsed(data: dict) -> dict:
    intent = data.get("intent")
    if intent not in INTENTS:
        intent = "unknown"

    route_mode = data.get("route_mode")
    if route_mode not in _ROUTE_MODES:
        route_mode = "react"

    slots = data.get("slots")
    if not isinstance(slots, dict):
        slots = {}

    confidence = data.get("confidence")
    if confidence is not None and not isinstance(confidence, (int, float)):
        confidence = None

    summary = data.get("summary")
    if summary is not None and not isinstance(summary, str):
        summary = None

    need_clarification = bool(data.get("need_clarification", False))
    clarification_question = data.get("clarification_question")
    if clarification_question is not None and not isinstance(clarification_question, str):
        clarification_question = None

    return {
        "intent": intent,
        "intent_confidence": confidence,
        "intent_summary": summary,
        "route_mode": route_mode,
        "slots": slots,
        "need_clarification": need_clarification,
        "clarification_question": clarification_question,
    }


def intent_analyzer(state: AgentState) -> dict:
    question = state.get("question") or ""
    messages = build_intent_prompt(
        question,
        session_slots=state.get("session_slots"),
        preferences=state.get("user_preferences"),
        recent_summaries=state.get("recent_summaries"),
    )
    raw = chat_completion(messages)
    parsed = _extract_json_object(raw)
    if parsed is None:
        return _fallback_intent()
    return _normalize_parsed(parsed)
