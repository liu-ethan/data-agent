from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypedDict

import yaml
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.app.gateway.write_policy import WriteGatewayError, check_write_sql, request_hash
from backend.app.mysql.execute_write import ExecuteWriteError
from backend.app.mysql.execute_write import execute_write as commit_write
from backend.app.runtime.permissions import reload_permissions
from backend.app.skills.write.preview import (
    PreviewError,
    approval_expired,
    approval_expires_at,
    build_preview,
    precheck_rows,
    snapshots_from_rows,
)
from backend.app.skills.write.registry import PreparedCommand, build_command, load_write_ops
from backend.app.types import (
    RuntimeContext,
    SkillErrorCode,
    WritePlan,
    WriteSkillResult,
    WriteTask,
)

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompt" / "write_plan.yaml"
_FORBIDDEN_PARAM_KEYS = frozenset({"sql", "table", "target_table"})
_DEFAULT_TTL = 15


class WriteLlm(Protocol):
    def write_plan(self, task: WriteTask, prompt: str) -> WritePlan: ...


class WriteState(TypedDict, total=False):
    task: WriteTask
    ctx: RuntimeContext
    operation_id: str | None
    request_hash: str | None
    preview: dict[str, Any] | None
    plan: WritePlan | None
    command: PreparedCommand | None
    rows: list[dict[str, Any]] | None
    result: WriteSkillResult | None


def _load_prompt() -> str:
    data = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("write_plan.yaml must be a mapping")
    return str(data["write_plan"])


def _fail(
    code: SkillErrorCode,
    message: str,
    *,
    status: str = "rejected",
    operation_id: str | None = None,
    preview: dict[str, Any] | None = None,
) -> WriteSkillResult:
    return WriteSkillResult(
        ok=False,
        status=status,  # type: ignore[arg-type]
        error_code=code,
        error_message=message,
        operation_id=operation_id,
        preview=preview,
    )


def _reload(
    ctx: RuntimeContext,
    reload_fn: Callable[..., Any],
    users_db: str | Path | None,
) -> RuntimeContext:
    permissions = reload_fn(
        ctx.user_id,
        users_db=users_db,
        catalog_version=ctx.permissions.catalog_version,
    )
    return ctx.model_copy(update={"permissions": permissions, "role": permissions.role})


def _authorize(task: WriteTask, ctx: RuntimeContext) -> WriteSkillResult | None:
    if ctx.tenant_id != "default" or ctx.permissions.tenant_id != "default":
        return _fail(SkillErrorCode.REJECTED, "tenant_id must be default")
    if ctx.role != "operator" or not ctx.permissions.allowed_write_ops:
        return _fail(SkillErrorCode.REJECTED, "analyst cannot write")
    if task.operation_type not in ctx.permissions.allowed_write_ops:
        return _fail(SkillErrorCode.REJECTED, "operation is not permitted")
    if task.permission_version != ctx.permissions.permission_version:
        return _fail(SkillErrorCode.PERMISSION_CHANGED, "permission_version changed")
    return None


def _plan_from_preview(preview: dict[str, Any]) -> WritePlan:
    return WritePlan.model_validate(preview["plan"])


def _command_for(plan: WritePlan, operation_id: str, req_hash: str, snapshots: dict[str, int]) -> PreparedCommand:
    cmd = build_command(plan)
    ops = load_write_ops()
    op = ops[cmd.operation_type]
    decision = check_write_sql(cmd.sql, cmd.params, op)
    if not decision.ok:
        raise WriteGatewayError(SkillErrorCode.UNSAFE_SQL, decision.reason or "gateway rejected")
    return cmd.model_copy(
        update={
            "operation_id": operation_id,
            "request_hash": req_hash,
            "version_snapshots": {str(k): int(v) for k, v in snapshots.items()},
        }
    )


def _load_receipt(engine: Engine, operation_id: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT operation_id, request_hash, operation_type, status, "
                    "affected_rows, audit_id FROM da_write_receipt "
                    "WHERE operation_id = :operation_id"
                ),
                {"operation_id": operation_id},
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


def _from_receipt(row: dict[str, Any], request_hash: str) -> WriteSkillResult:
    if str(row["request_hash"]) != request_hash:
        return _fail(SkillErrorCode.REJECTED, "operation_id reused with a different request_hash")
    status = str(row["status"])
    if status == "unknown":
        return WriteSkillResult(
            ok=False,
            operation_id=str(row["operation_id"]),
            status="unknown",
            affected_rows=row["affected_rows"],
            audit_id=row["audit_id"],
            error_code=SkillErrorCode.UNKNOWN_COMMIT,
        )
    return WriteSkillResult(
        ok=status == "committed",
        operation_id=str(row["operation_id"]),
        status=status if status in {"preview", "committed", "rejected", "unknown"} else "unknown",
        affected_rows=row["affected_rows"],
        audit_id=row["audit_id"],
    )


def _preview_result(
    *,
    ctx: RuntimeContext,
    plan: WritePlan,
    cmd: PreparedCommand,
    rows: list[dict[str, Any]],
    ttl_minutes: int,
    operation_id: str | None = None,
) -> WriteSkillResult:
    snapshots = snapshots_from_rows(rows)
    oid = operation_id or str(uuid.uuid4())
    req_hash = request_hash(plan, snapshots)
    prepared_at = ctx.request_time_utc
    preview = build_preview(
        operation_id=oid,
        request_hash=req_hash,
        plan=plan,
        cmd=cmd,
        rows=rows,
        ctx_user_id=ctx.user_id,
        prepared_at=prepared_at,
        expires_at=approval_expires_at(prepared_at, ttl_minutes),
    )
    return WriteSkillResult(
        ok=True,
        operation_id=oid,
        status="preview",
        preview=preview,
        affected_rows=len(rows),
    )


def _conflict_preview(
    ctx: RuntimeContext,
    plan: WritePlan,
    rows: list[dict[str, Any]],
    ttl_minutes: int,
) -> WriteSkillResult:
    cmd = build_command(plan)
    fresh = _preview_result(ctx=ctx, plan=plan, cmd=cmd, rows=rows, ttl_minutes=ttl_minutes)
    return WriteSkillResult(
        ok=False,
        operation_id=fresh.operation_id,
        status="preview",
        preview=fresh.preview,
        affected_rows=fresh.affected_rows,
        error_code=SkillErrorCode.VERSION_CONFLICT,
        error_message="row_version snapshot mismatch",
    )


def build_prepare_graph(
    *,
    llm: WriteLlm,
    reload_permissions_fn: Callable[..., Any] | None = None,
    users_db: str | Path | None = None,
    reader_engine: Engine | None = None,
    approval_ttl_minutes: int = _DEFAULT_TTL,
):
    reload_fn = reload_permissions_fn or reload_permissions
    prompt = _load_prompt()

    def reader() -> Engine:
        if reader_engine is not None:
            return reader_engine
        from backend.app.mysql.pool import get_engine

        return get_engine("reader")

    def w01_plan(state: WriteState) -> dict[str, Any]:
        ctx = _reload(state["ctx"], reload_fn, users_db)
        denied = _authorize(state["task"], ctx)
        if denied is not None:
            return {"ctx": ctx, "result": denied}
        plan = llm.write_plan(state["task"], prompt)
        if _FORBIDDEN_PARAM_KEYS & set(plan.params):
            return {
                "ctx": ctx,
                "result": _fail(SkillErrorCode.REJECTED, "WritePlan must not include SQL or table names"),
            }
        return {"ctx": ctx, "plan": plan}

    def w02_command(state: WriteState) -> dict[str, Any]:
        plan = state["plan"]
        assert plan is not None
        try:
            cmd = _command_for(plan, "", "", {})
        except WriteGatewayError as exc:
            return {"result": _fail(exc.code, str(exc))}
        return {"command": cmd}

    def w04_precheck(state: WriteState) -> dict[str, Any]:
        cmd = state["command"]
        plan = state["plan"]
        assert cmd is not None and plan is not None
        try:
            rows = precheck_rows(cmd, reader())
        except PreviewError as exc:
            return {"result": _fail(exc.code, str(exc))}
        previewed = _preview_result(
            ctx=state["ctx"],
            plan=plan,
            cmd=cmd,
            rows=rows,
            ttl_minutes=approval_ttl_minutes,
        )
        return {"rows": rows, "result": previewed}

    def route_if_done(state: WriteState) -> str:
        return "end" if state.get("result") is not None else "next"

    graph = StateGraph(WriteState)
    graph.add_node("w01_plan", w01_plan)
    graph.add_node("w02_command", w02_command)
    graph.add_node("w04_precheck", w04_precheck)
    graph.add_edge(START, "w01_plan")
    graph.add_conditional_edges("w01_plan", route_if_done, {"end": END, "next": "w02_command"})
    graph.add_conditional_edges("w02_command", route_if_done, {"end": END, "next": "w04_precheck"})
    graph.add_edge("w04_precheck", END)
    return graph.compile()


def build_execute_graph(
    *,
    reload_permissions_fn: Callable[..., Any] | None = None,
    users_db: str | Path | None = None,
    reader_engine: Engine | None = None,
    writer_engine: Engine | None = None,
    approval_ttl_minutes: int = _DEFAULT_TTL,
    commit_write_fn: Callable[..., Any] | None = None,
):
    reload_fn = reload_permissions_fn or reload_permissions
    commit = commit_write_fn or commit_write

    def reader() -> Engine:
        if reader_engine is not None:
            return reader_engine
        from backend.app.mysql.pool import get_engine

        return get_engine("reader")

    def writer() -> Engine:
        if writer_engine is not None:
            return writer_engine
        from backend.app.mysql.pool import get_engine

        return get_engine("writer")

    def w07_recheck(state: WriteState) -> dict[str, Any]:
        ctx = _reload(state["ctx"], reload_fn, users_db)
        preview = state.get("preview") or {}
        operation_id = state.get("operation_id") or ""
        req_hash = state.get("request_hash") or ""
        if not preview or not operation_id or not req_hash:
            return {"ctx": ctx, "result": _fail(SkillErrorCode.REJECTED, "preview is required")}
        if operation_id != preview.get("operation_id") or req_hash != preview.get("request_hash"):
            return {"ctx": ctx, "result": _fail(SkillErrorCode.REJECTED, "operation_id or request_hash mismatch")}
        stub = WriteTask(
            task_id="execute",
            operation_type=str(preview.get("operation_type") or ""),
            object_ids=list(preview.get("object_ids") or []),
            params=dict(preview.get("params") or {}),
            permission_version=ctx.permissions.permission_version,
        )
        denied = _authorize(stub, ctx)
        if denied is not None:
            return {"ctx": ctx, "result": denied}
        if ctx.user_id != preview.get("operator_user_id"):
            return {"ctx": ctx, "result": _fail(SkillErrorCode.REJECTED, "approver must be the same operator")}

        existing = _load_receipt(writer(), operation_id)
        if existing is not None:
            return {"ctx": ctx, "result": _from_receipt(existing, req_hash)}

        if approval_expired(str(preview["expires_at"]), ctx.request_time_utc):
            return {"ctx": ctx, "result": _fail(SkillErrorCode.REJECTED, "approval has expired")}

        plan = _plan_from_preview(preview)
        try:
            cmd = _command_for(plan, operation_id, req_hash, preview.get("version_snapshots") or {})
            rows = precheck_rows(cmd, reader())
        except (WriteGatewayError, PreviewError) as exc:
            code = exc.code if isinstance(exc, (WriteGatewayError, PreviewError)) else SkillErrorCode.REJECTED
            return {"ctx": ctx, "result": _fail(code, str(exc))}

        live = snapshots_from_rows(rows)
        expected = {str(k): int(v) for k, v in (preview.get("version_snapshots") or {}).items()}
        targets = [str(i) for i in preview.get("object_ids") or []]
        live_targets = [str(i) for i in cmd.object_ids]
        params_now = dict(plan.params)
        params_then = dict(preview.get("params") or {})
        if live != expected or live_targets != targets or params_now != params_then:
            return {"ctx": ctx, "plan": plan, "result": _conflict_preview(ctx, plan, rows, approval_ttl_minutes)}
        return {"ctx": ctx, "plan": plan, "command": cmd, "rows": rows}

    def w08_commit(state: WriteState) -> dict[str, Any]:
        cmd = state["command"]
        plan = state["plan"]
        assert cmd is not None and plan is not None
        try:
            receipt = commit(cmd, state["ctx"], engine=writer())
        except ExecuteWriteError as exc:
            if exc.code == SkillErrorCode.VERSION_CONFLICT:
                try:
                    live_rows = precheck_rows(cmd, reader())
                except PreviewError as preview_exc:
                    return {"result": _fail(preview_exc.code, str(preview_exc))}
                return {"result": _conflict_preview(state["ctx"], plan, live_rows, approval_ttl_minutes)}
            if exc.code == SkillErrorCode.UNKNOWN_COMMIT:
                return {
                    "result": WriteSkillResult(
                        ok=False,
                        operation_id=cmd.operation_id,
                        status="unknown",
                        error_code=SkillErrorCode.UNKNOWN_COMMIT,
                        error_message=str(exc),
                    )
                }
            return {"result": _fail(exc.code, str(exc))}
        status = receipt.status
        if status == "unknown":
            return {
                "result": WriteSkillResult(
                    ok=False,
                    operation_id=receipt.operation_id,
                    status="unknown",
                    affected_rows=receipt.affected_rows,
                    audit_id=receipt.audit_id,
                    error_code=SkillErrorCode.UNKNOWN_COMMIT,
                )
            }
        return {
            "result": WriteSkillResult(
                ok=True,
                operation_id=receipt.operation_id,
                status="committed",
                affected_rows=receipt.affected_rows,
                audit_id=receipt.audit_id,
            )
        }

    def route_if_done(state: WriteState) -> str:
        return "end" if state.get("result") is not None else "next"

    graph = StateGraph(WriteState)
    graph.add_node("w07_recheck", w07_recheck)
    graph.add_node("w08_commit", w08_commit)
    graph.add_edge(START, "w07_recheck")
    graph.add_conditional_edges("w07_recheck", route_if_done, {"end": END, "next": "w08_commit"})
    graph.add_edge("w08_commit", END)
    return graph.compile()


def prepare_write(
    task: WriteTask,
    ctx: RuntimeContext,
    *,
    llm: WriteLlm,
    reload_permissions_fn: Callable[..., Any] | None = None,
    users_db: str | Path | None = None,
    reader_engine: Engine | None = None,
    approval_ttl_minutes: int = _DEFAULT_TTL,
) -> WriteSkillResult:
    graph = build_prepare_graph(
        llm=llm,
        reload_permissions_fn=reload_permissions_fn,
        users_db=users_db,
        reader_engine=reader_engine,
        approval_ttl_minutes=approval_ttl_minutes,
    )
    final = graph.invoke(
        {
            "task": task,
            "ctx": ctx,
            "plan": None,
            "command": None,
            "rows": None,
            "result": None,
        }
    )
    result = final.get("result")
    if result is None:
        return _fail(SkillErrorCode.REJECTED, "write skill produced no preview")
    return result


def execute_write(
    operation_id: str,
    request_hash: str,
    ctx: RuntimeContext,
    *,
    preview: dict[str, Any],
    reload_permissions_fn: Callable[..., Any] | None = None,
    users_db: str | Path | None = None,
    reader_engine: Engine | None = None,
    writer_engine: Engine | None = None,
    approval_ttl_minutes: int = _DEFAULT_TTL,
    commit_write_fn: Callable[..., Any] | None = None,
) -> WriteSkillResult:
    graph = build_execute_graph(
        reload_permissions_fn=reload_permissions_fn,
        users_db=users_db,
        reader_engine=reader_engine,
        writer_engine=writer_engine,
        approval_ttl_minutes=approval_ttl_minutes,
        commit_write_fn=commit_write_fn,
    )
    final = graph.invoke(
        {
            "ctx": ctx,
            "operation_id": operation_id,
            "request_hash": request_hash,
            "preview": preview,
            "result": None,
        }
    )
    result = final.get("result")
    if result is None:
        return _fail(SkillErrorCode.REJECTED, "write skill produced no result")
    return result
