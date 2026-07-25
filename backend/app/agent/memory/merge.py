from __future__ import annotations


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def merge_slots(
    prev: dict | None,
    curr: dict | None,
    preferences: dict | None = None,
) -> dict:
    prev = dict(prev or {})
    curr = dict(curr or {})
    keys = set(prev) | set(curr) | {
        "metrics",
        "time_range",
        "group_by",
        "top_n",
        "filters",
        "write_intent",
    }
    out: dict = {}
    for key in keys:
        c = curr.get(key, None) if key in curr else None
        p = prev.get(key, None)
        if key in curr and not _is_empty(c):
            out[key] = c
        elif not _is_empty(p):
            out[key] = p
        else:
            out[key] = c if key in curr else p
    prefs = preferences or {}
    if _is_empty(out.get("time_range")) and prefs.get("default_time_range"):
        out["time_range"] = prefs["default_time_range"]
    if "metrics" not in out or out["metrics"] is None:
        out["metrics"] = []
    if "group_by" not in out or out["group_by"] is None:
        out["group_by"] = []
    return out
