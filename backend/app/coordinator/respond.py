from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.app.llm.client import strip_reasoning
from backend.app.resources.prompts import render_prompt
from backend.app.types import ResultSummary

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_REFUSE = "无法根据查询结果作答，数字必须来自本次查询摘要。"


def load_response_prompt() -> str:
    return render_prompt("coordinator.respond")


def _as_json_number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return None


def facts_from_summary(summary: ResultSummary) -> dict[str, Any]:
    scalars: dict[str, Any] = {}
    if summary.preview_rows:
        for key, value in summary.preview_rows[0].items():
            number = _as_json_number(value)
            if number is not None:
                scalars[key] = number
    return {
        "result_id": summary.result_id,
        "row_count": summary.row_count,
        "columns": list(summary.columns),
        "units": dict(summary.units),
        "time_range": summary.time_range.model_dump(),
        "data_as_of": summary.data_as_of,
        "metric_versions": dict(summary.metric_versions),
        "scalars": scalars,
    }


def empty_result_answer(summary: ResultSummary) -> str | None:
    if summary.row_count:
        return None
    label = summary.time_range.label or "这个时间窗"
    return f"{label} 没有查到数据。可以说「本月」再问一次。"


def build_response_prompt(facts: dict[str, Any]) -> str:
    del facts
    return load_response_prompt()


def _allowed_decimals(facts: dict[str, Any]) -> list[Decimal]:
    out: list[Decimal] = []
    for value in (facts.get("scalars") or {}).values():
        number = _as_json_number(value)
        if number is None:
            continue
        out.append(Decimal(str(number)))
    return out


def ground_answer(answer: str, facts: dict[str, Any]) -> str:
    answer = strip_reasoning(answer)
    blob = json.dumps(facts, ensure_ascii=False)
    allowed = _allowed_decimals(facts)
    for token in _NUMBER.findall(answer):
        if token in blob:
            continue
        try:
            parsed = Decimal(token)
        except InvalidOperation:
            return _REFUSE
        if any(parsed == item for item in allowed):
            continue
        return _REFUSE
    return answer
