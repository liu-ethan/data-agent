from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from backend.app.types import ResultSummary

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompt" / "response.yaml"
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_REFUSE = "无法根据查询结果作答，数字必须来自本次查询摘要。"


def load_response_prompt() -> str:
    data = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("response.yaml must be a mapping")
    return str(data["response"])


def facts_from_summary(summary: ResultSummary) -> dict[str, Any]:
    scalars: dict[str, Any] = {}
    if summary.preview_rows:
        for key, value in summary.preview_rows[0].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                scalars[key] = value
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


def build_response_prompt(facts: dict[str, Any]) -> str:
    return f"{load_response_prompt()}\n{json.dumps(facts, ensure_ascii=False)}"


def ground_answer(answer: str, facts: dict[str, Any]) -> str:
    blob = json.dumps(facts, ensure_ascii=False)
    for token in _NUMBER.findall(answer):
        if token not in blob:
            return _REFUSE
    return answer
