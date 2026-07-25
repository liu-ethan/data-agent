from __future__ import annotations

import re

from app.agent.state import AgentState

_COMPLEX_RE = re.compile(
    r"(对比|同比|环比|归因|并且|以及|同时看|多指标)",
)


def decide_route(
    question: str,
    slots: dict | None,
    model_route: str | None,
) -> tuple[str, str]:
    slots = slots or {}
    metrics = list(slots.get("metrics") or [])
    time_range = slots.get("time_range")
    group_by = list(slots.get("group_by") or [])
    top_n = slots.get("top_n")
    q = question or ""

    complex_hit = len(metrics) >= 2 or bool(_COMPLEX_RE.search(q))
    if complex_hit:
        return "coordinator", "rule_override"

    simple_hit = (
        len(metrics) == 1
        and bool(time_range)
        and not complex_hit
        and (top_n is not None or len(group_by) <= 1)
    )
    if simple_hit:
        return "react", "rule_override"

    mode = model_route if model_route in ("react", "coordinator") else "react"
    return mode, "model"


def complexity_router(state: AgentState) -> dict:
    mode, src = decide_route(
        state.get("question") or "",
        state.get("slots"),
        state.get("route_mode"),
    )
    return {"route_mode": mode, "route_source": src}
