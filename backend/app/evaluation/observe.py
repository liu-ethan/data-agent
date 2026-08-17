"""Extract public evaluation evidence from HTTP chat payloads."""

from __future__ import annotations

from typing import Any


def evidence_from_payload(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("evidence")
    if not isinstance(raw, dict):
        events = list(body.get("events") or [])
        for event in reversed(events):
            candidate = event.get("evidence") if isinstance(event, dict) else None
            if isinstance(candidate, dict):
                raw = candidate
                break
        else:
            raw = {}
    tokens = raw.get("grounded_context_tokens")
    recovered = raw.get("schema_gap_recovered")
    return {
        "intent": raw.get("intent"),
        "metric_ids": list(raw.get("metric_ids") or []),
        "objects": list(raw.get("object_names") or []),
        "fields": list(raw.get("field_names") or []),
        "coverage": raw.get("coverage"),
        "retrieval_rounds": int(raw.get("retrieval_rounds") or 0),
        "grounded_tokens": int(tokens) if tokens is not None else None,
        "schema_gap_recovered": recovered if recovered is not None else None,
    }
