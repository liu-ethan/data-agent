"""Deterministic CoverageEvaluator for Schema RAG.

This is the named spec 04 component that compares TaskFrame slots against
retrieved evidence and emits CoverageResult / SchemaGap. Retrieval
orchestration must not invent a second coverage policy.
"""

from __future__ import annotations

from uuid import uuid4

from ..models import (
    CoverageResult,
    CoverageStatus,
    SchemaGap,
    TaskFrame,
)


class CoverageEvaluator:
    """Compare required metric, field and time slots with grounded evidence."""

    def __init__(self, *, ambiguity_gap: float, max_retrieval_rounds: int = 2) -> None:
        self.ambiguity_gap = ambiguity_gap
        self.max_retrieval_rounds = max_retrieval_rounds

    def evaluate(
        self, *, task: TaskFrame, objects, fields, metric_ids: list[str],
        dimension_ids: list[str], required_fields: set[str],
        schema_gap: SchemaGap | None = None, query: str = "",
    ) -> CoverageResult:
        field_names = {field.name for field in fields}
        missing: list[str] = []
        if not objects:
            missing.append("authorized schema evidence")
        if task.intent.value == "DATA_QUERY" and not metric_ids:
            missing.append("catalog metric binding")
        missing_dimensions = set(dimension_ids) - field_names
        if missing_dimensions:
            missing.extend(f"dimension.{item}" for item in sorted(missing_dimensions))
        missing_required = required_fields - field_names
        if missing_required:
            missing.extend(f"field.{item}" for item in sorted(missing_required))
        if (task.intent.value == "DATA_QUERY"
                and not any(field.classification == "BUSINESS_TIME"
                            for field in fields)):
            missing.append("time field")
        ambiguous: list[str] = []
        if (not metric_ids and not dimension_ids and len(objects) > 1
                and abs(objects[0].score - objects[1].score) < self.ambiguity_gap):
            ambiguous.append("business object")
        status = (CoverageStatus.SUFFICIENT if not missing and not ambiguous
                  else CoverageStatus.AMBIGUOUS if ambiguous
                  else CoverageStatus.PARTIAL)
        gap = None
        if status != CoverageStatus.SUFFICIENT:
            round_number = schema_gap.retrieval_round + 1 if schema_gap else 1
            gap = SchemaGap(
                gap_id=f"gap_{uuid4().hex[:16]}",
                missing_concepts=list(dict.fromkeys(missing + ambiguous)),
                candidate_object_ids=[item.object_id for item in objects],
                narrow_query="; ".join(missing + ambiguous) or query,
                reason="catalog coverage is incomplete or ambiguous",
                retrieval_round=min(self.max_retrieval_rounds, round_number),
            )
        covered = [*(f"metric.{item}" for item in metric_ids),
                   *(f"dimension.{item}" for item in dimension_ids),
                   *(f"field.{item}" for item in sorted(required_fields & field_names))]
        return CoverageResult(
            status=status, covered=covered, missing=list(dict.fromkeys(missing)),
            ambiguous=ambiguous, schema_gap=gap)
