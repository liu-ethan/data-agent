from __future__ import annotations

from langgraph.types import interrupt


def interrupt_hitl(payload):
    return interrupt(payload)
