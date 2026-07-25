from __future__ import annotations

import re

from app.agent.metrics import get_metric_spec
from app.api.schema import build_schema_tables
from app.security.sql_guardrail import check_sql
from app.security.sql_sandbox import SandboxError, sandbox_execute
from app.tools.audit import append_audit
from app.tools.registry import ToolRegistry, get_registry
from app.tools.schemas import ToolContext, ToolResult, ToolSpec


def _sql_detail(sql: str) -> dict:
    compact = " ".join(sql.split())
    return {
        "sql": compact[:200],
        "sql_fingerprint": compact[:64],
    }


def _looks_numeric(values: list) -> bool:
    seen = False
    for value in values:
        if value is None:
            continue
        seen = True
        if isinstance(value, (int, float)):
            continue
        if isinstance(value, str):
            try:
                float(value)
            except ValueError:
                return False
        else:
            return False
    return seen


def _handle_query_schema(_args: dict, context: ToolContext) -> ToolResult:
    tables = build_schema_tables(context.user_role)
    return ToolResult(ok=True, data={"tables": tables})


def _handle_retrieve_metric_definition(args: dict, _context: ToolContext) -> ToolResult:
    key = str(args.get("metric") or "").strip()
    spec = get_metric_spec(key)
    if spec is None:
        return ToolResult(ok=False, error="Unknown metric")
    return ToolResult(ok=True, data=dict(spec))


def _handle_validate_sql(args: dict, context: ToolContext) -> ToolResult:
    sql = str(args.get("sql") or "")
    result = check_sql(sql, user_role=context.user_role)
    detail = _sql_detail(sql)
    if not result.ok:
        reason = result.reason or "SQL blocked by guardrail"
        append_audit(
            {
                "event": "guardrail_deny",
                "tool": "validate_sql",
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "session_id": context.session_id,
                "user_id": context.user_id,
                "user_role": context.user_role,
                "node": context.node,
                "detail": {**detail, "reason": reason},
            }
        )
        return ToolResult(
            ok=False,
            error=reason,
            data={"ok": False, "reason": reason, **detail},
        )
    return ToolResult(
        ok=True,
        data={"ok": True, "reason": result.reason, **detail},
    )


def _handle_execute_sql(args: dict, context: ToolContext) -> ToolResult:
    sql = str(args.get("sql") or "")
    detail = _sql_detail(sql)
    try:
        result = sandbox_execute(sql, user_role=context.user_role)
    except SandboxError as exc:
        msg = str(exc).splitlines()[0][:200]
        risk = "high" if _is_write_sql(sql) else "medium"
        return ToolResult(
            ok=False,
            error=msg,
            data={"ok": False, "risk_level": risk, **detail},
        )

    if result.is_write:
        data = {
            "affected_rows": result.affected_rows,
            "is_write": True,
            "risk_level": "high",
            **detail,
        }
    else:
        data = {
            "columns": result.columns,
            "rows": result.rows,
            "is_write": False,
            "risk_level": "medium",
            **detail,
        }
    return ToolResult(ok=True, data=data)


def _is_write_sql(sql: str) -> bool:
    head = re.sub(r"\A(?:\s*--[^\n]*(?:\n|\Z))*\s*", "", sql.strip())
    if re.match(r"(?:INSERT|UPDATE|DELETE)\b", head, re.IGNORECASE):
        return True
    if re.match(r"WITH\b", head, re.IGNORECASE):
        return bool(re.search(r"\b(INSERT|UPDATE|DELETE)\b", head, re.IGNORECASE))
    return False


def _handle_render_chart(args: dict, _context: ToolContext) -> ToolResult:
    columns = list(args.get("columns") or [])
    rows = list(args.get("rows") or [])
    title = str(args.get("title") or "")

    if len(columns) >= 2:
        y_col = columns[1]
        values = [row.get(y_col) for row in rows if isinstance(row, dict)]
        if _looks_numeric(values):
            return ToolResult(
                ok=True,
                data={
                    "type": "bar",
                    "x": columns[0],
                    "y": columns[1],
                    "title": title,
                },
            )

    return ToolResult(
        ok=True,
        data={
            "type": "table",
            "x": columns[0] if columns else "",
            "y": columns[1] if len(columns) > 1 else "",
            "title": title,
        },
    )


def ensure_builtins_registered() -> ToolRegistry:
    reg = get_registry()
    if getattr(reg, "_builtins_ready", False):
        return reg

    reg.register(
        ToolSpec(
            name="query_schema",
            description="Return business table schema for the user role",
            risk_level="low",
            permission_policy="allow",
        ),
        _handle_query_schema,
    )
    reg.register(
        ToolSpec(
            name="retrieve_metric_definition",
            description="Return metric definition by key",
            risk_level="low",
            permission_policy="allow",
        ),
        _handle_retrieve_metric_definition,
    )
    reg.register(
        ToolSpec(
            name="validate_sql",
            description="Validate SQL against role guardrail",
            risk_level="medium",
            permission_policy="allow_after_validation",
        ),
        _handle_validate_sql,
    )
    reg.register(
        ToolSpec(
            name="execute_sql",
            description="Execute validated SQL in sandbox (read or admin controlled write)",
            risk_level="medium",
            permission_policy="allow_after_validation",
        ),
        _handle_execute_sql,
    )
    reg.register(
        ToolSpec(
            name="render_chart",
            description="Build a simple chart config from tabular data",
            risk_level="low",
            permission_policy="allow",
        ),
        _handle_render_chart,
    )

    reg._builtins_ready = True
    return reg
