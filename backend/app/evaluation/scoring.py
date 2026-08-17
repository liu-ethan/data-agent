"""Per-case scoring. Metrics aggregate these atoms; they do not re-interpret raw events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _condense(actions: list[str]) -> list[str]:
    condensed: list[str] = []
    for action in actions:
        if not action or (condensed and condensed[-1] == action):
            continue
        condensed.append(action)
    return condensed


@dataclass
class CaseOutcome:
    case_id: str
    category: str
    status: str
    expected_status: str
    action_sequence: list[str]
    expected_action_sequence: list[str]
    observed_intent: str | None = None
    expected_intent: str | None = None
    observed_metric_ids: list[str] = field(default_factory=list)
    expected_metric_ids: list[str] = field(default_factory=list)
    observed_objects: list[str] = field(default_factory=list)
    required_objects: list[str] = field(default_factory=list)
    observed_fields: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    observed_columns: list[str] = field(default_factory=list)
    observed_rows: list[dict[str, Any]] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    coverage: str | None = None
    schema_gap_recovered: bool | None = None
    retrieval_rounds: int = 0
    graph_steps: int = 0
    grounded_context_tokens: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    error_code: str | None = None
    last_action: str | None = None
    result_ok: bool = False
    deferred: bool = False
    sql_execution_accurate: bool = False
    data_version: str = "seed_v1"
    catalog_version: str = "catalog_v1"
    completed: bool = False
    task_frame_ok: bool = False
    action_ok: bool = False
    object_recall: float = 0.0
    field_recall: float = 0.0
    context_precision: float = 0.0

    def as_row(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "status": self.status,
            "expected_status": self.expected_status,
            "passed": self.completed,
            "latency_ms": round(self.latency_ms, 3),
            "error_code": self.error_code,
            "last_action": self.last_action,
        }

    def failure_record(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "error_code": self.error_code,
            "last_action": self.last_action,
            "status": self.status,
            "data_version": self.data_version,
            "catalog_version": self.catalog_version,
            "reproduce_command": (
                f"python3.12 scripts/run_evaluation.py --allow-test-double --case-id {self.case_id}"
            ),
        }


def score_case(outcome: CaseOutcome) -> CaseOutcome:
    if outcome.deferred:
        outcome.completed = False
        outcome.task_frame_ok = False
        outcome.action_ok = False
        return outcome
    expected_objects = set(outcome.required_objects)
    expected_fields = set(outcome.required_fields)
    observed_objects = set(outcome.observed_objects)
    observed_fields = set(outcome.observed_fields)
    outcome.object_recall = (
        len(expected_objects & observed_objects) / len(expected_objects)
        if expected_objects
        else 1.0
    )
    outcome.field_recall = (
        len(expected_fields & observed_fields) / len(expected_fields) if expected_fields else 1.0
    )
    relevant = len(expected_objects & observed_objects) + len(expected_fields & observed_fields)
    observed_total = len(observed_objects) + len(observed_fields)
    outcome.context_precision = relevant / observed_total if observed_total else 0.0
    condensed = _condense(outcome.action_sequence)
    outcome.action_ok = all(action in condensed for action in outcome.expected_action_sequence)
    outcome.task_frame_ok = outcome.observed_intent == outcome.expected_intent and set(
        outcome.expected_metric_ids
    ) <= set(outcome.observed_metric_ids)
    status_ok = outcome.status == outcome.expected_status
    # Spec 07: status (incl. clarify/reject), permission (encoded in status)
    # and result_compare must all pass. SQL string match is not enough.
    outcome.completed = bool(status_ok and outcome.result_ok)
    return outcome


def dump_outcome(outcome: CaseOutcome) -> dict[str, Any]:
    return asdict(outcome)
