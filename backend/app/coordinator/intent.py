from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.llm.schemas import sanitize_intent
from backend.app.resources.domain import allowed_operation_types, dimension_aliases, empty_text, time_presets
from backend.app.resources.prompts import render_prompt
from backend.app.runtime.time import resolve_time_range
from backend.app.types import FilterCond, Intent, QueryTask, RuntimeContext, WriteTask


class IntentDraft(BaseModel):
    intent: Intent
    metric_ids: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[FilterCond] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    time_text: str | None = None
    operation_type: str | None = None
    object_ids: list[str] = Field(default_factory=list)
    refer_previous_skus: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    clarify_kind: Literal["metric", "product", "time"] | None = None
    clarify_query: str | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_llm_payload(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = sanitize_intent(dict(value))
        if not data.get("time_text"):
            extra = data.get("time_range")
            preset = None
            if isinstance(extra, str) and extra.strip():
                preset = extra.strip()
            elif isinstance(extra, dict):
                preset = extra.get("preset") or extra.get("label") or extra.get("text")
            if preset:
                key = str(preset).strip()
                data["time_text"] = time_presets().get(key.lower(), key)
        return data

    @field_validator("intent", mode="before")
    @classmethod
    def coerce_intent(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("clarify_kind", mode="before")
    @classmethod
    def coerce_clarify_kind(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("time_text", mode="before")
    @classmethod
    def coerce_time_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return time_presets().get(stripped.lower(), stripped) or None
        return value

    @field_validator("operation_type", mode="before")
    @classmethod
    def coerce_operation_type(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.lower() in empty_text():
            return None
        return stripped


def load_coordinator_prompt() -> str:
    return render_prompt("coordinator.intent")


def normalize_intent(draft: IntentDraft) -> Intent:
    writeish = draft.operation_type in allowed_operation_types()
    queryish = bool(draft.metric_ids) or draft.intent in (Intent.QUERY, Intent.FOLLOWUP)
    if writeish and queryish:
        return Intent.UNSUPPORTED
    if writeish and draft.intent in (Intent.QUERY, Intent.FOLLOWUP, Intent.CLARIFY):
        return Intent.UNSUPPORTED
    if draft.metric_ids and draft.intent == Intent.WRITE:
        return Intent.UNSUPPORTED
    return draft.intent


def build_query_task(
    draft: IntentDraft,
    ctx: RuntimeContext,
    *,
    parent: QueryTask | None,
    result_id: str | None,
) -> QueryTask:
    intent = normalize_intent(draft)
    inherit = intent == Intent.FOLLOWUP and parent is not None and not draft.time_text
    if inherit:
        time_range = parent.time_range
    else:
        time_range = resolve_time_range(draft.time_text, ctx.request_time_utc, ctx.timezone)
    metric_ids = list(draft.metric_ids)
    aliases = dimension_aliases()
    dimensions = [aliases.get(item, item) for item in draft.dimensions]
    filters = list(draft.filters)
    order_by = list(draft.order_by)
    limit = draft.limit
    if intent == Intent.FOLLOWUP and parent is not None:
        if not metric_ids:
            metric_ids = list(parent.metric_ids)
        if not dimensions:
            dimensions = list(parent.dimensions)
        if not filters:
            filters = list(parent.filters)
        if not order_by:
            order_by = list(parent.order_by)
        if limit is None:
            limit = parent.limit
    return QueryTask(
        task_id=str(uuid.uuid4()),
        metric_ids=metric_ids,
        dimensions=dimensions,
        filters=filters,
        time_range=time_range,
        order_by=order_by,
        limit=limit,
        parent_result_id=result_id if intent == Intent.FOLLOWUP else None,
        catalog_version=ctx.permissions.catalog_version,
        permission_version=ctx.permissions.permission_version,
    )


def build_write_task(draft: IntentDraft, ctx: RuntimeContext, object_ids: list[str]) -> WriteTask:
    return WriteTask(
        task_id=str(uuid.uuid4()),
        operation_type=draft.operation_type or "",
        object_ids=object_ids,
        params=dict(draft.params),
        filters=list(draft.filters),
        permission_version=ctx.permissions.permission_version,
    )
