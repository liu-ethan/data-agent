from __future__ import annotations

import ast
import inspect
from datetime import timedelta
from pathlib import Path

from backend.app.types import (
    PermissionSet,
    RuntimeContext,
    SkillErrorCode,
    WritePlan,
    WriteTask,
)
from tests.test_execute_write import MemoryEngine

NOW = "2026-08-29T00:00:00+00:00"


def _ctx(
    *,
    user_id: str = "u-admin",
    role: str = "operator",
    write_ops: list[str] | None = None,
    permission_version: int = 1,
    request_time_utc: str = NOW,
) -> RuntimeContext:
    ops = (
        write_ops
        if write_ops is not None
        else (["update_sku_status", "adjust_sku_inventory"] if role == "operator" else [])
    )
    return RuntimeContext(
        tenant_id="default",
        user_id=user_id,
        role=role,  # type: ignore[arg-type]
        request_time_utc=request_time_utc,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id=user_id,
            role=role,  # type: ignore[arg-type]
            allowed_tables=["dim_sku"],
            allowed_columns=["data-agent-ecommerce.dim_sku.*"],
            allowed_metrics=[],
            allowed_write_ops=ops,
            catalog_version=1,
            permission_version=permission_version,
        ),
        thread_id="t-write-skill",
    )


def _task(**kwargs) -> WriteTask:
    return WriteTask(
        task_id=kwargs.get("task_id", "wt-1"),
        operation_type=kwargs.get("operation_type", "update_sku_status"),
        object_ids=kwargs.get("object_ids", ["1"]),
        params=kwargs.get("params", {"status": "off_sale"}),
        filters=kwargs.get("filters", []),
        permission_version=kwargs.get("permission_version", 1),
    )


class FakeWriteLlm:
    def __init__(self, plan: WritePlan | None = None) -> None:
        self._plan = plan
        self.calls = 0
        self.prompts: list[str] = []

    def write_plan(self, task: WriteTask, prompt: str) -> WritePlan:
        self.calls += 1
        self.prompts.append(prompt)
        if self._plan is not None:
            return self._plan
        return WritePlan(
            operation_type=task.operation_type,
            object_ids=list(task.object_ids),
            params=dict(task.params),
            filters=list(task.filters),
        )


def _prepare(task, ctx, engine, llm=None, **kwargs):
    from backend.app.skills.write.graph import prepare_write

    return prepare_write(
        task,
        ctx,
        llm=llm or FakeWriteLlm(),
        reader_engine=engine,
        reload_permissions_fn=kwargs.get(
            "reload_permissions_fn", lambda *a, **k: ctx.permissions
        ),
        approval_ttl_minutes=kwargs.get("approval_ttl_minutes", 15),
    )


def _execute(prepared, ctx, engine, **kwargs):
    from backend.app.skills.write.graph import execute_write

    preview = prepared.preview or {}
    return execute_write(
        prepared.operation_id or preview.get("operation_id"),
        preview.get("request_hash"),
        ctx,
        preview=preview,
        reader_engine=engine,
        writer_engine=engine,
        reload_permissions_fn=kwargs.get(
            "reload_permissions_fn", lambda *a, **k: ctx.permissions
        ),
        approval_ttl_minutes=kwargs.get("approval_ttl_minutes", 15),
    )


def test_write_skill_modules_do_not_call_interrupt():
    from backend.app.skills.write import graph, preview

    prompt = Path("backend/app/prompt/write_plan.yaml").read_text(encoding="utf-8")
    assert "interrupt" not in prompt.lower() or "不要调用 interrupt" in prompt

    for mod in (graph, preview):
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "interrupt" not in names
        assert "interrupt" not in attrs
        assert "interrupt(" not in src


def test_prepare_write_returns_preview_without_receipt_or_lock():
    engine = MemoryEngine()
    llm = FakeWriteLlm()
    result = _prepare(_task(), _ctx(), engine, llm=llm)

    assert result.ok is True
    assert result.status == "preview"
    assert result.operation_id
    assert result.preview is not None
    assert result.preview["request_hash"]
    assert result.preview["version_snapshots"] == {"1": 1}
    assert result.preview["affected_rows"] == 1
    assert result.preview["operation_type"] == "update_sku_status"
    assert engine.receipts == {}
    assert engine.skus[1]["status"] == "on_sale"
    assert engine.skus[1]["row_version"] == 1
    assert not any("for update" in sql.lower() for sql, _ in engine.calls)
    assert not any("insert into da_write_receipt" in sql.lower() for sql, _ in engine.calls)
    assert llm.calls == 1
    prompt = llm.prompts[0].lower()
    assert "sql" in prompt or "写 sql" in llm.prompts[0]
    assert "interrupt" in prompt


def test_same_operation_id_and_hash_second_execute_does_not_change_again():
    engine = MemoryEngine()
    ctx = _ctx()
    prepared = _prepare(_task(), ctx, engine)
    first = _execute(prepared, ctx, engine)
    second = _execute(prepared, ctx, engine)

    assert first.ok is True
    assert first.status == "committed"
    assert first.operation_id == prepared.operation_id
    assert first.affected_rows == 1
    assert first.audit_id
    assert second.ok is True
    assert second.status == "committed"
    assert second.operation_id == first.operation_id
    assert second.audit_id == first.audit_id
    assert second.affected_rows == first.affected_rows
    assert engine.skus[1]["status"] == "off_sale"
    assert engine.skus[1]["row_version"] == 2
    assert len(engine.audits) == 1
    assert len(engine.receipts) == 1


def test_reject_paths_do_not_write_business_tables():
    engine = MemoryEngine()
    analyst = _ctx(user_id="u-analyst", role="analyst", write_ops=[])
    denied = _prepare(_task(), analyst, engine)
    assert denied.ok is False
    assert denied.status == "rejected"
    assert denied.error_code == SkillErrorCode.REJECTED
    assert engine.skus[1]["status"] == "on_sale"
    assert engine.receipts == {}
    assert engine.audits == {}
    assert engine.calls == []

    too_big = _prepare(
        _task(object_ids=[str(i) for i in range(1, 102)]),
        _ctx(),
        engine,
    )
    assert too_big.ok is False
    assert too_big.error_code == SkillErrorCode.WRITE_SCOPE_TOO_LARGE
    assert too_big.status == "rejected"
    assert engine.skus[1]["status"] == "on_sale"
    assert engine.receipts == {}
    assert not any("insert into da_write_receipt" in sql.lower() for sql, _ in engine.calls)
    assert not any("update dim_sku" in sql.lower() for sql, _ in engine.calls)


def test_version_conflict_returns_new_preview_and_does_not_write():
    engine = MemoryEngine()
    ctx = _ctx()
    prepared = _prepare(_task(), ctx, engine)
    old_id = prepared.operation_id
    old_hash = prepared.preview["request_hash"]
    engine.skus[1]["row_version"] = 9
    engine.skus[1]["status"] = "on_sale"

    result = _execute(prepared, ctx, engine)
    assert result.ok is False
    assert result.error_code == SkillErrorCode.VERSION_CONFLICT
    assert result.status == "preview"
    assert result.operation_id != old_id
    assert result.preview is not None
    assert result.preview["request_hash"] != old_hash
    assert result.preview["version_snapshots"] == {"1": 9}
    assert result.preview["operation_id"] == result.operation_id
    assert old_id not in engine.receipts
    assert engine.skus[1]["status"] == "on_sale"
    assert engine.skus[1]["row_version"] == 9
    assert engine.audits == {}


def test_expired_approval_and_other_operator_do_not_write():
    from datetime import datetime

    engine = MemoryEngine()
    ctx = _ctx()
    prepared = _prepare(_task(), ctx, engine)

    later = (datetime.fromisoformat(NOW) + timedelta(minutes=16)).isoformat()
    expired = _execute(prepared, _ctx(request_time_utc=later), engine)
    assert expired.ok is False
    assert expired.status == "rejected"
    assert expired.error_code == SkillErrorCode.REJECTED
    assert engine.skus[1]["status"] == "on_sale"
    assert engine.receipts == {}

    other = _execute(prepared, _ctx(user_id="u-other"), engine)
    assert other.ok is False
    assert other.status == "rejected"
    assert other.error_code == SkillErrorCode.REJECTED
    assert engine.skus[1]["status"] == "on_sale"
    assert engine.receipts == {}
