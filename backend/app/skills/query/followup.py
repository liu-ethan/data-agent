from __future__ import annotations

from typing import Literal

from backend.app.types import FilterCond, LocalFilterSpec, QueryTask

FollowupKind = Literal["filter", "requery"]


def _bare(field: str) -> str:
    token = field.split()[0]
    return token.rsplit(".", 1)[-1]


def _in_parent(field: str, parent_columns: list[str]) -> bool:
    cols = set(parent_columns)
    bares = {_bare(column) for column in parent_columns}
    name = _bare(field)
    return field in cols or name in cols or name in bares


def decide_followup(
    task: QueryTask,
    *,
    parent_task: QueryTask,
    parent_columns: list[str],
) -> FollowupKind:
    parent_metrics = set(parent_task.metric_ids)
    if any(metric_id not in parent_metrics for metric_id in task.metric_ids):
        return "requery"
    if (
        task.time_range.start != parent_task.time_range.start
        or task.time_range.end != parent_task.time_range.end
    ):
        return "requery"
    for dim in task.dimensions:
        if not _in_parent(dim, parent_columns):
            return "requery"
    for cond in task.filters:
        if not _in_parent(cond.field, parent_columns):
            return "requery"
    for item in task.order_by:
        if not _in_parent(item, parent_columns):
            return "requery"
    return "filter"


def merge_query_task(parent: QueryTask, current: QueryTask) -> QueryTask:
    metrics = list(dict.fromkeys([*parent.metric_ids, *current.metric_ids]))
    dims = list(dict.fromkeys([*parent.dimensions, *current.dimensions]))
    seen = {(cond.field, cond.op, str(cond.value)) for cond in parent.filters}
    filters = list(parent.filters)
    for cond in current.filters:
        key = (cond.field, cond.op, str(cond.value))
        if key in seen:
            continue
        filters.append(cond)
        seen.add(key)
    return current.model_copy(
        update={
            "metric_ids": metrics,
            "dimensions": dims,
            "filters": filters,
            "parent_result_id": current.parent_result_id or parent.parent_result_id,
            "order_by": current.order_by or parent.order_by,
            "limit": current.limit if current.limit is not None else parent.limit,
        }
    )


def local_filter_spec(task: QueryTask, parent_columns: list[str]) -> LocalFilterSpec:
    filters = [
        FilterCond(field=_bare(cond.field), op=cond.op, value=cond.value)
        for cond in task.filters
    ]
    order_by: list[str] = []
    for item in task.order_by:
        parts = item.split()
        col = _bare(parts[0])
        order_by.append(f"{col} {parts[1]}" if len(parts) == 2 else col)
    select = [_bare(dim) for dim in task.dimensions if _in_parent(dim, parent_columns)]
    return LocalFilterSpec(
        filters=filters,
        order_by=order_by,
        select=select,
        topn=task.limit,
    )
