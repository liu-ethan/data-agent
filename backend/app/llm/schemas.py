"""Structured LLM output schemas. Canonical shapes live in types.py."""

from __future__ import annotations

from typing import Any

from backend.app.types import QuerySkeleton, SchemaGap, WritePlan

__all__ = [
    "QuerySkeleton",
    "parse_query_skeleton",
    "parse_write_plan",
    "parse_schema_gap",
    "parse_clarify",
    "sanitize_intent",
]

_EMPTY = (None, "", [], {})
_OPS = frozenset({"=", "!=", "in", "not_in", ">", ">=", "<", "<=", "like"})
_OP_ALIAS = {
    "=": "=",
    "==": "=",
    "eq": "=",
    "equal": "=",
    "equals": "=",
    "!=": "!=",
    "<>": "!=",
    "ne": "!=",
    "neq": "!=",
    "gt": ">",
    ">": ">",
    "lt": "<",
    "<": "<",
    "gte": ">=",
    ">=": ">=",
    "lte": "<=",
    "<=": "<=",
    "in": "in",
    "not in": "not_in",
    "notin": "not_in",
    "not_in": "not_in",
    "like": "like",
}
_COMPARISONS = frozenset({"yoy", "mom", "ratio", "topn"})


def as_list(value: object) -> list[Any]:
    if value is None or value == "" or value == {}:
        return []
    if isinstance(value, list):
        return value
    return [value]


def as_str_list(value: object) -> list[str]:
    out: list[str] = []
    for item in as_list(value):
        if item is None or item == "":
            continue
        if isinstance(item, (list, dict)):
            continue
        out.append(str(item))
    return out


def as_metric_ids(value: object) -> list[str]:
    ids: list[str] = []
    for item in as_str_list(value):
        lowered = item.strip().lower()
        ids.append(lowered or item)
    return ids


def as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "yes", "1"}:
        return True
    return False


def as_opt_int(value: object) -> int | None:
    if value in _EMPTY or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def as_opt_str(value: object) -> str | None:
    if value in _EMPTY:
        return None
    if isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    return text or None


def as_filters(value: object) -> list[dict[str, Any]]:
    if value in _EMPTY:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        field = item.get("field") or item.get("column") or item.get("name")
        raw_op = item.get("op") or item.get("operator") or "="
        op = _OP_ALIAS.get(str(raw_op).strip().lower())
        if not field or op not in _OPS:
            continue
        out.append({"field": str(field), "op": op, "value": item.get("value")})
    return out


def sanitize_intent(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    data["metric_ids"] = as_metric_ids(data.get("metric_ids"))
    data["dimensions"] = as_str_list(data.get("dimensions"))
    data["order_by"] = as_str_list(data.get("order_by"))
    data["object_ids"] = as_str_list(data.get("object_ids"))
    data["filters"] = as_filters(data.get("filters"))
    data["params"] = as_dict(data.get("params"))
    data["refer_previous_skus"] = as_bool(data.get("refer_previous_skus"))
    data["limit"] = as_opt_int(data.get("limit"))
    data["operation_type"] = as_opt_str(data.get("operation_type"))
    data["clarify_kind"] = as_opt_str(data.get("clarify_kind"))
    data["clarify_query"] = as_opt_str(data.get("clarify_query"))
    time_text = as_opt_str(data.get("time_text"))
    if time_text is not None:
        data["time_text"] = time_text
    return data


def parse_query_skeleton(data: object) -> QuerySkeleton:
    if not isinstance(data, dict):
        data = {}
    payload = dict(data)
    payload["metric_ids"] = as_metric_ids(payload.get("metric_ids"))
    payload["select_dims"] = as_str_list(payload.get("select_dims"))
    payload["group_by"] = as_str_list(payload.get("group_by"))
    payload["filters"] = as_filters(payload.get("filters"))
    payload["limit"] = as_opt_int(payload.get("limit"))
    payload["from_table"] = as_opt_str(payload.get("from_table")) or ""
    payload["time_field"] = as_opt_str(payload.get("time_field")) or ""
    payload["comparisons"] = [
        item
        for item in (str(raw).strip().lower() for raw in as_list(payload.get("comparisons")))
        if item in _COMPARISONS
    ]
    joins: list[dict[str, str]] = []
    for item in as_list(payload.get("joins")):
        if not isinstance(item, dict):
            continue
        joins.append({str(key): str(val) for key, val in item.items() if val is not None})
    payload["joins"] = joins
    return QuerySkeleton.model_validate(payload)


def parse_write_plan(data: object) -> WritePlan:
    if not isinstance(data, dict):
        raise TypeError("write plan JSON must be an object")
    payload = dict(data)
    payload["operation_type"] = as_opt_str(payload.get("operation_type")) or ""
    payload["object_ids"] = as_str_list(payload.get("object_ids"))
    payload["params"] = as_dict(payload.get("params"))
    payload["filters"] = as_filters(payload.get("filters"))
    return WritePlan.model_validate(payload)


def parse_schema_gap(data: object, *, fallback: dict[str, Any]) -> SchemaGap:
    payload = dict(data) if isinstance(data, dict) else {}
    constraints = payload.get("constraints")
    excluded = payload.get("excluded")
    if not isinstance(constraints, list):
        constraints = fallback.get("constraints") or []
    if not isinstance(excluded, list):
        excluded = fallback.get("excluded") or []
    return SchemaGap.model_validate(
        {
            "missing_concept": as_opt_str(payload.get("missing_concept"))
            or fallback["missing_concept"],
            "purpose": as_opt_str(payload.get("purpose")) or fallback["purpose"],
            "constraints": as_str_list(constraints),
            "excluded": as_str_list(excluded),
        }
    )


def parse_clarify(data: object) -> dict[str, Any]:
    payload = dict(data) if isinstance(data, dict) else {}
    candidates: list[dict[str, str]] = []
    for item in as_list(payload.get("candidates")):
        if not isinstance(item, dict):
            continue
        cid = as_opt_str(item.get("id"))
        if not cid:
            continue
        label = as_opt_str(item.get("label")) or cid
        kind = as_opt_str(item.get("kind")) or ""
        entry = {"id": cid, "label": label}
        if kind:
            entry["kind"] = kind
        candidates.append(entry)
    return {
        "message": as_opt_str(payload.get("message")) or "",
        "candidates": candidates,
    }
