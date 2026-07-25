from __future__ import annotations

import re

_PHONE = re.compile(r"(?<!\d)1\d{10}(?!\d)")
_EMAIL = re.compile(
    r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+"
)
_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def strip_sensitive(text: str) -> str:
    text = _PHONE.sub("[phone]", text or "")
    text = _EMAIL.sub("[email]", text)
    return _ID.sub("[id_card]", text)


def merge_preferences(existing: dict, slots: dict) -> dict:
    out = dict(existing or {})
    time_range = slots.get("time_range")
    if time_range:
        out["default_time_range"] = time_range
    dimensions = list(slots.get("group_by") or [])
    if dimensions:
        preferred = list(out.get("preferred_dimensions") or [])
        for dimension in dimensions:
            if dimension not in preferred:
                preferred.append(dimension)
        out["preferred_dimensions"] = preferred[:8]
    return out


def build_result_summary(
    *,
    answer: str | None,
    error: str | None,
    clarification: str | None,
) -> str:
    if clarification:
        return strip_sensitive(f"clarification: {clarification}")[:240]
    if error:
        return strip_sensitive(f"error: {error}")[:240]
    return strip_sensitive((answer or "")[:240])
