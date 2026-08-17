"""Fixed eval-case contract owned by spec 07."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ..models import Contract, FilterSpec, Intent, TimeRange

TIME_ANCHOR = "2026-08-16T10:00:00+08:00"
DEFAULT_DATA_VERSION = "seed_v1"
DEFAULT_CATALOG_VERSION = "catalog_v1"


class ResultCompare(Contract):
    row_order: Literal["explicit", "any"] = "explicit"
    numeric_abs_tolerance: float = 0.01
    numeric_rel_tolerance: float = 0.0001
    null_equals_zero: bool = False


class EvalBudgets(Contract):
    max_steps: int = Field(ge=1, le=32)
    max_retrieval_rounds: int = Field(ge=1, le=4)
    max_seconds: float = Field(gt=0, le=300)


class GoldenTaskFrame(Contract):
    # Legacy fixtures nested required_objects on the frame; ignore extras.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    intent: Intent
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    time_range: TimeRange | None = None
    filters: list[FilterSpec] = Field(default_factory=list)


class EvalMessage(Contract):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class EvalCase(Contract):
    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    messages: list[EvalMessage] = Field(min_length=1)
    golden_task_frame: GoldenTaskFrame
    required_objects: list[str] = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)
    expected_action_sequence: list[str] = Field(min_length=1)
    golden_result_ref: str = Field(min_length=1)
    should_clarify: bool = False
    should_reject: bool = False
    budgets: EvalBudgets = Field(
        default_factory=lambda: EvalBudgets(max_steps=6, max_retrieval_rounds=2, max_seconds=30)
    )
    data_version: str = DEFAULT_DATA_VERSION
    catalog_version: str = DEFAULT_CATALOG_VERSION
    result_compare: ResultCompare = Field(default_factory=ResultCompare)
    schema_version: Literal["eval_case_v1"] = "eval_case_v1"
    deferred_reason: str | None = None
    requires_prior_turn: bool = False

    @model_validator(mode="after")
    def multi_turn_uses_full_session(self) -> EvalCase:
        multi_turn = self.requires_prior_turn or self.category in {
            "follow_up",
            "multi_turn",
            "checkpoint",
            "hitl",
            "long_term_memory",
        }
        if multi_turn and len(self.messages) < 2:
            raise ValueError("messages must contain the full multi-turn conversation")
        return self


def load_cases(directory: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in sorted(Path(directory).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        items: list[Any] = payload if isinstance(payload, list) else [payload]
        cases.extend(EvalCase.model_validate(item) for item in items)
    return cases
