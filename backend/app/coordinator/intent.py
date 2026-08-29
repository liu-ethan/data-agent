from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from backend.app.runtime.time import resolve_time_range
from backend.app.types import FilterCond, Intent, QueryTask, RuntimeContext, WriteTask

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompt" / "coordinator.yaml"


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


def load_coordinator_prompt() -> str:
    data = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("coordinator.yaml must be a mapping")
    return str(data["coordinator"])


def normalize_intent(draft: IntentDraft) -> Intent:
    writeish = bool(draft.operation_type)
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
    dimensions = list(draft.dimensions)
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
