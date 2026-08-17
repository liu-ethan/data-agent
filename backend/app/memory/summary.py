"""Deterministic rolling summary with tagged fact sources."""

from __future__ import annotations

from typing import Any

from ..models import AgentState, MutationSpec, RollingSummary, SummaryFact


class RollingSummaryBuilder:
    def update(
        self,
        state: AgentState,
        *,
        pending_mutation: MutationSpec | dict[str, Any] | None = None,
    ) -> RollingSummary:
        facts: list[SummaryFact] = []
        if state.task_frame:
            facts.append(SummaryFact(
                text=state.task_frame.question, source="USER_CONFIRMED"))
            for condition in state.task_frame.explicit_conditions:
                facts.append(SummaryFact(text=condition, source="USER_CONFIRMED"))
        observation = state.latest_observation
        if observation is not None:
            columns = observation.summary.columns if observation.summary else []
            facts.append(SummaryFact(
                text=(
                    f"result_id={observation.result_id} "
                    f"rows={observation.summary.row_count if observation.summary else 0} "
                    f"columns={','.join(columns)}"
                ),
                source="SYSTEM_OBSERVED",
            ))
        mutation = pending_mutation
        if isinstance(mutation, MutationSpec):
            mutation = mutation.model_dump(mode="json")
        return RollingSummary(
            facts=facts,
            result_ids=list(state.result_ids or (
                [observation.result_id] if observation and observation.result_id else [])),
            artifact_ids=list(state.artifact_ids),
            pending_mutation=mutation,
        )
