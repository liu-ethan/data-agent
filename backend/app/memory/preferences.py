"""Long-term preference recall with current explicit conditions winning."""

from __future__ import annotations

import re
from typing import Any

from ..models import FilterSpec, PermissionContext, TaskFrame

_FUTURE_DEFAULT = ("以后默认", "以后都", "设为默认", "设成默认")
_ONE_OFF = ("这次", "本次")


def is_long_term_preference_request(message: str) -> bool:
    return any(term in message for term in _FUTURE_DEFAULT)


def extract_explicit_conditions(message: str) -> list[str]:
    conditions: list[str] = []
    one_off = re.search(r"((?:这次|本次)[^，。！？,]*)", message)
    if one_off:
        conditions.append(one_off.group(1).strip())
    elif (only := re.search(r"(只看[^，。！？,]+)", message)):
        conditions.append(only.group(1).strip())
    return [item for item in conditions if item]


def _has_one_off_scope(task: TaskFrame) -> bool:
    blob = " ".join(task.explicit_conditions)
    return any(term in blob for term in (*_ONE_OFF, "只看"))


def apply_preferences(
    task: TaskFrame,
    preferences: dict[str, Any],
    permission: PermissionContext,
) -> TaskFrame:
    if _has_one_off_scope(task):
        return task
    shop_id = preferences.get("default_shop_id")
    if not shop_id or shop_id not in permission.allowed_shop_ids:
        return task
    if any(item.field.endswith("shop_id") for item in task.filters):
        return task
    return task.model_copy(update={
        "filters": [
            *task.filters,
            FilterSpec(field="orders.shop_id", operator="=", value=shop_id, source="user"),
        ],
    })
