from __future__ import annotations

import re

from app.agent.state import AgentState
from app.agent.vocab import METRIC_VOCAB

_VAGUE_METRIC_PATTERN = re.compile(r"表现|最好|不错|怎么样")
_LAST_SLOT_TOKEN = re.compile(r"last_\w+")


def clarification_checker(state: AgentState) -> dict:
    existing_q = state.get("clarification_question")
    if state.get("need_clarification") and existing_q:
        return {
            "need_clarification": True,
            "clarification_question": existing_q,
        }

    question = state.get("question") or ""
    slots = state.get("slots") or {}
    metrics: list = list(slots.get("metrics") or [])
    time_range = slots.get("time_range")

    parts: list[str] = []

    if not metrics and _VAGUE_METRIC_PATTERN.search(question):
        parts.append("「表现最好」想按哪个指标？例如 GMV、订单量或客单价。")

    if (
        not time_range
        and "最近" in question
        and not _LAST_SLOT_TOKEN.search(question)
    ):
        parts.append("时间用最近 7 天还是 30 天？")

    unknown = [m for m in metrics if m not in METRIC_VOCAB]
    if unknown:
        label = unknown[0]
        parts.append(
            f"「{label}」不在当前支持指标内，请说明要用 GMV、订单量、客单价等哪类指标。"
        )

    if parts:
        return {
            "need_clarification": True,
            "clarification_question": " ".join(parts),
        }

    return {
        "need_clarification": False,
        "clarification_question": None,
    }
