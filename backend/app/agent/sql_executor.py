from __future__ import annotations


class GuardrailError(Exception):
    """Raised when SQL fails guardrail checks before execution."""


def execute_sql(sql: str, *, user_role: str) -> tuple[list[str], list[dict]]:
    """Removed bypass: chat/graph SQL must use Tool Registry ``execute_sql``."""
    raise RuntimeError("use tools/registry")
