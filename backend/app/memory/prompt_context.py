"""Project AgentState into the minimum Prompt for each LLM node."""

from __future__ import annotations

from typing import Any

from ..models import AgentState, ResultObservation


def _summary_only(observation: ResultObservation | None) -> dict[str, Any] | None:
    if observation is None:
        return None
    payload: dict[str, Any] = {
        "status": observation.status.value,
        "result_id": observation.result_id,
        "error_code": observation.error_code,
        "query_plan_id": observation.query_plan_id,
    }
    if observation.summary is not None:
        payload["summary"] = {
            "row_count": observation.summary.row_count,
            "columns": observation.summary.columns,
            "empty": observation.summary.empty,
        }
    return payload


class PromptContextBuilder:
    def build(self, *, node: str, state: AgentState) -> dict[str, Any]:
        if node == "query_generation_node":
            context = state.grounded_context
            return {
                "catalog_version": context.catalog_version if context else None,
                "objects": [item.model_dump(mode="json") for item in context.objects]
                if context else [],
                "fields": [item.model_dump(mode="json") for item in context.fields]
                if context else [],
                "joins": [item.model_dump(mode="json") for item in context.join_paths]
                if context else [],
                "task": state.task_frame.model_dump(mode="json") if state.task_frame else None,
                "previous_gateway_error": state.previous_query_error,
            }
        if node == "response_node":
            return {
                "task": state.task_frame.model_dump(mode="json") if state.task_frame else None,
                "result_id": (state.latest_observation.result_id
                              if state.latest_observation else None),
                "summary": _summary_only(state.latest_observation),
                "artifact_ids": state.artifact_ids[-10:],
            }
        return {
            "task_frame": state.task_frame.model_dump(mode="json") if state.task_frame else None,
            "rolling_summary": (state.rolling_summary.model_dump(mode="json")
                                if state.rolling_summary else None),
            "recent_messages": state.messages[-8:],
            "latest_observation": _summary_only(state.latest_observation),
            "result_ids": state.result_ids[-5:],
            "artifact_ids": state.artifact_ids[-10:],
            "goal_checklist": state.goal_checklist,
            "budgets": state.budgets,
            "coverage": str(state.coverage),
        }
