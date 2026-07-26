from __future__ import annotations

from app.agent.chart_planner import plan_chart
from app.security.sql_sandbox import (
    GuardrailSandboxError,
    SandboxError,
    sandbox_execute,
)

MAX_DISPLAY_ROWS = 100


def build_display_payload(
    *,
    columns: list | None,
    rows: list | None,
    chart: dict | None,
    repaired: bool = False,
    guardrail_passed: bool = False,
    trace: list | None = None,
) -> dict:
    cols = list(columns or [])
    limited_rows = list(rows or [])[:MAX_DISPLAY_ROWS]
    return {
        "columns": cols,
        "rows": limited_rows,
        "chart": chart,
        "sql_repaired": bool(repaired),
        "guardrail_passed": bool(guardrail_passed),
        "trace": list(trace or []),
    }


def hydrate_display_from_sql(
    *,
    sql_text: str,
    question: str,
    user_role: str,
) -> dict | None:
    try:
        result = sandbox_execute(sql_text, user_role=user_role)
    except (GuardrailSandboxError, SandboxError):
        return None
    if result.is_write:
        return None
    columns = result.columns
    rows = result.rows[:MAX_DISPLAY_ROWS]
    chart = None
    if columns and rows:
        # Skip LLM for hydrate; heuristic (+ enrich) is enough for UI restore.
        chart = plan_chart("", columns, rows, title_hint=(question or "")[:40])
    return build_display_payload(
        columns=columns,
        rows=rows,
        chart=chart,
        repaired=False,
        guardrail_passed=True,
        trace=[
            {"event": "sql", "summary": "历史 SQL 已重新校验并执行"},
            {"event": "rows", "summary": f"{len(rows)} 行"},
            *(
                [{"event": "chart", "summary": str(chart.get("type") or "chart")}]
                if chart and chart.get("type") != "table"
                else []
            ),
        ],
    )
