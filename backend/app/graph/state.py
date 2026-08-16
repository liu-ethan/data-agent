"""LLM-facing structured drafts used by the bounded runtime graph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _LLMContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskUnderstanding(_LLMContract):
    task_type: Literal[
        "SCHEMA_QUERY", "DATA_QUERY", "DATA_MUTATION", "RESULT_TRANSFORM",
        "METRIC_EXPLANATION", "CHAT_OR_OUT_OF_SCOPE",
    ]
    deliverables: list[Literal["DATA_TABLE", "CSV", "CHART", "TEXT"]] = Field(
        default_factory=lambda: ["TEXT"])
    mentions: dict[str, list[str]] = Field(default_factory=dict)
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    next_action: Literal["RETRIEVE", "ASK_USER", "RESPOND"] = "RETRIEVE"
    schema_version: Literal["task_understanding_v1"] = "task_understanding_v1"

    @model_validator(mode="after")
    def unresolved_requires_clarification(self) -> "TaskUnderstanding":
        if self.unresolved and self.next_action != "ASK_USER":
            raise ValueError("unresolved concepts require ASK_USER")
        if self.task_type in {"DATA_QUERY", "SCHEMA_QUERY", "METRIC_EXPLANATION"}:
            if self.next_action == "RESPOND":
                raise ValueError("grounded tasks cannot bypass retrieval")
        return self


class QueryDraft(_LLMContract):
    status: Literal["QUERY_PLAN", "SCHEMA_GAP"]
    candidate_sql: str | None = None
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    metric_refs: list[str] = Field(default_factory=list)
    dimension_refs: list[str] = Field(default_factory=list)
    expected_columns: list[str] = Field(default_factory=list)
    time_field: str | None = None
    required_object_ids: list[str] = Field(default_factory=list)
    missing_concepts: list[str] = Field(default_factory=list)
    schema_version: Literal["query_draft_v1"] = "query_draft_v1"

    @model_validator(mode="after")
    def status_has_consistent_payload(self) -> "QueryDraft":
        if self.status == "SCHEMA_GAP":
            if self.candidate_sql or self.parameters:
                raise ValueError("SCHEMA_GAP cannot contain executable SQL")
            if not self.missing_concepts:
                raise ValueError("SCHEMA_GAP requires missing_concepts")
        elif not (self.candidate_sql and self.expected_columns
                  and self.time_field and self.required_object_ids):
            raise ValueError("QUERY_PLAN requires SQL, columns, time field and objects")
        return self


class AnswerDraft(_LLMContract):
    answer: str = Field(min_length=1, max_length=2000)
    evidence_result_ids: list[str] = Field(min_length=1, max_length=5)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    schema_version: Literal["answer_draft_v1"] = "answer_draft_v1"


class ConversationalAnswerDraft(_LLMContract):
    answer: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    schema_version: Literal["conversational_answer_v1"] = "conversational_answer_v1"


class ThreadTitleDraft(_LLMContract):
    title: str = Field(min_length=2, max_length=10)
    schema_version: Literal["thread_title_v1"] = "thread_title_v1"
