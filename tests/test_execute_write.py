from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.types import PermissionSet, RuntimeContext, SkillErrorCode, WritePlan

NOW = "2026-08-29T00:00:00+00:00"


def _ctx(*, write_ops: list[str] | None = None) -> RuntimeContext:
    ops = write_ops if write_ops is not None else ["update_sku_status", "adjust_sku_inventory"]
    return RuntimeContext(
        tenant_id="default",
        user_id="u-admin",
        role="operator",
        request_time_utc=NOW,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id="u-admin",
            role="operator",
            allowed_tables=["dim_sku"],
            allowed_columns=["data-agent-ecommerce.dim_sku.*"],
            allowed_metrics=[],
            allowed_write_ops=ops,
            catalog_version=1,
            permission_version=1,
        ),
        thread_id="t-write",
    )


def _prepare(
    plan: WritePlan,
    snapshots: dict[str, int],
    operation_id: str,
):
    from backend.app.gateway.write_policy import request_hash
    from backend.app.skills.write.registry import build_command

    cmd = build_command(plan)
    return cmd.model_copy(
        update={
            "operation_id": operation_id,
            "request_hash": request_hash(plan, snapshots),
            "version_snapshots": {str(k): int(v) for k, v in snapshots.items()},
        }
    )


def _sql_text(stmt) -> str:
    return str(getattr(stmt, "text", stmt))


class FakeResult:
    def __init__(self, rows: list[dict], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict]:
        return list(self._rows)

    def first(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def one_or_none(self) -> dict | None:
        if len(self._rows) > 1:
            raise RuntimeError("multiple rows")
        return self._rows[0] if self._rows else None


class FakeTrans:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


class FakeConn:
    def __init__(self, engine: MemoryEngine) -> None:
        self._engine = engine
        self._open = True
        self._snapshot_skus: dict[int, dict[str, Any]] | None = None
        self._snapshot_receipts: dict[str, dict[str, Any]] | None = None
        self._snapshot_audits: dict[str, dict[str, Any]] | None = None
        self._skus = engine.skus
        self._receipts = engine.receipts
        self._audits = engine.audits

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self._open = False
        return False

    def begin(self) -> FakeTrans:
        self._snapshot_skus = copy.deepcopy(self._engine.skus)
        self._snapshot_receipts = copy.deepcopy(self._engine.receipts)
        self._snapshot_audits = copy.deepcopy(self._engine.audits)
        self._skus = copy.deepcopy(self._engine.skus)
        self._receipts = copy.deepcopy(self._engine.receipts)
        self._audits = copy.deepcopy(self._engine.audits)
        return FakeTrans(self)

    def commit(self) -> None:
        self._engine.skus = self._skus
        self._engine.receipts = self._receipts
        self._engine.audits = self._audits
        self._snapshot_skus = None

    def rollback(self) -> None:
        if self._snapshot_skus is not None:
            self._skus = self._snapshot_skus
            self._receipts = self._snapshot_receipts or {}
            self._audits = self._snapshot_audits or {}
        self._snapshot_skus = None

    def execute(self, stmt, params=None):
        sql = _sql_text(stmt)
        params = dict(params or {})
        self._engine.calls.append((sql, params))
        return self._handle(sql, params)

    def _handle(self, sql: str, params: dict[str, Any]) -> FakeResult:
        low = " ".join(sql.lower().replace("`", "").split())
        if "insert into da_write_receipt" in low:
            oid = params["operation_id"]
            if oid in self._receipts:
                raise IntegrityError(sql, params, Exception("Duplicate entry"))
            self._receipts[oid] = {
                "operation_id": oid,
                "request_hash": params["request_hash"],
                "operation_type": params["operation_type"],
                "status": params.get("status", "pending"),
                "affected_rows": params.get("affected_rows"),
                "audit_id": params.get("audit_id"),
                "payload_json": params.get("payload_json", "{}"),
            }
            return FakeResult([], 1)
        if "update da_write_receipt" in low:
            oid = params["operation_id"]
            row = self._receipts[oid]
            if "affected_rows" in params:
                row["affected_rows"] = params["affected_rows"]
            if "audit_id" in params:
                row["audit_id"] = params["audit_id"]
            if "status" in params:
                row["status"] = params["status"]
            return FakeResult([], 1)
        if "from da_write_receipt" in low:
            oid = params["operation_id"]
            row = self._receipts.get(oid)
            return FakeResult([] if row is None else [dict(row)])
        if "insert into da_write_audit" in low:
            aid = params["audit_id"]
            self._audits[aid] = dict(params)
            return FakeResult([], 1)
        if "from dim_sku" in low:
            ids = [int(i) for i in params["ids"]]
            rows = []
            for sku_id in ids:
                sku = self._skus.get(sku_id)
                if sku is not None:
                    rows.append({"id": sku_id, **sku})
            if self._engine.lock_extra_rows:
                extra_start = 1000
                for i in range(self._engine.lock_extra_rows):
                    rows.append(
                        {
                            "id": extra_start + i,
                            "status": "on_sale",
                            "inventory_qty": 1,
                            "row_version": 1,
                        }
                    )
            return FakeResult(rows, len(rows))
        if "update dim_sku" in low:
            ids = [int(i) for i in params.get("ids", [])]
            if "id" in params and not ids:
                ids = [int(params["id"])]
            n = 0
            for sku_id in ids:
                sku = self._skus.get(sku_id)
                if sku is None:
                    continue
                if "row_version" in params and int(sku["row_version"]) != int(params["row_version"]):
                    continue
                if "status" in params:
                    sku["status"] = params["status"]
                if "inventory_qty" in params:
                    sku["inventory_qty"] = params["inventory_qty"]
                sku["row_version"] = int(sku["row_version"]) + 1
                n += 1
            return FakeResult([], n)
        raise AssertionError(f"unexpected sql: {sql}")


class MemoryEngine:
    def __init__(self) -> None:
        self.skus = {
            1: {"status": "on_sale", "inventory_qty": 32, "row_version": 1},
            2: {"status": "on_sale", "inventory_qty": 10, "row_version": 3},
        }
        self.receipts: dict[str, dict[str, Any]] = {}
        self.audits: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.connects = 0
        self.lock_extra_rows = 0

    def connect(self) -> FakeConn:
        self.connects += 1
        return FakeConn(self)


def _call_kinds(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    kinds: list[str] = []
    for sql, _ in calls:
        low = sql.lower()
        if "insert into da_write_receipt" in low:
            kinds.append("insert_receipt")
        elif "update da_write_receipt" in low:
            kinds.append("update_receipt")
        elif "from da_write_receipt" in low:
            kinds.append("select_receipt")
        elif "insert into da_write_audit" in low:
            kinds.append("insert_audit")
        elif "for update" in low:
            kinds.append("lock")
        elif "update dim_sku" in low:
            kinds.append("update_sku")
        elif "from dim_sku" in low:
            kinds.append("select_sku")
    return kinds


def test_rejects_non_whitelist_sql_without_touching_mysql():
    from backend.app.mysql.execute_write import ExecuteWriteError, execute_write

    engine = MemoryEngine()
    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=["1"],
        params={"status": "off_sale"},
    )
    cmd = _prepare(plan, {"1": 1}, "op-unsafe")
    cmd = cmd.model_copy(update={"sql": "DELETE FROM dim_sku WHERE id IN :ids"})
    with pytest.raises(ExecuteWriteError) as exc:
        execute_write(cmd, _ctx(), engine=engine)
    assert exc.value.code == SkillErrorCode.UNSAFE_SQL
    assert engine.connects == 0
    assert engine.calls == []
    assert engine.skus[1]["status"] == "on_sale"


def test_rejects_101_rows_without_touching_mysql():
    from backend.app.mysql.execute_write import ExecuteWriteError, execute_write
    from backend.app.skills.write.registry import load_write_ops

    engine = MemoryEngine()
    op = load_write_ops()["update_sku_status"]
    ids = [str(i) for i in range(1, 102)]
    from backend.app.skills.write.registry import PreparedCommand

    cmd = PreparedCommand(
        operation_type="update_sku_status",
        target_table="dim_sku",
        sql=op.sql_template,
        params={"status": "off_sale", "ids": list(range(1, 102))},
        object_ids=ids,
        max_affected_rows=100,
        version_predicate="row_version",
        locking_read=False,
        operation_id="op-101",
        request_hash="a" * 64,
        version_snapshots={i: 1 for i in ids},
    )
    with pytest.raises(ExecuteWriteError) as exc:
        execute_write(cmd, _ctx(), engine=engine)
    assert exc.value.code == SkillErrorCode.WRITE_SCOPE_TOO_LARGE
    assert engine.connects == 0


def test_same_operation_id_second_call_does_not_change_again():
    from backend.app.mysql.execute_write import execute_write

    engine = MemoryEngine()
    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=["1"],
        params={"status": "off_sale"},
    )
    cmd = _prepare(plan, {"1": 1}, "op-dup")
    first = execute_write(cmd, _ctx(), engine=engine)
    second = execute_write(cmd, _ctx(), engine=engine)
    assert first.status == "committed"
    assert second.status == "committed"
    assert first.operation_id == second.operation_id == "op-dup"
    assert first.audit_id == second.audit_id
    assert first.affected_rows == second.affected_rows == 1
    assert engine.skus[1]["status"] == "off_sale"
    assert engine.skus[1]["row_version"] == 2
    assert len(engine.audits) == 1


def test_transaction_order_receipt_then_reverify_then_change_then_audit():
    from backend.app.mysql.execute_write import execute_write

    engine = MemoryEngine()
    plan = WritePlan(
        operation_type="adjust_sku_inventory",
        object_ids=["1"],
        params={"inventory_qty": 50},
    )
    cmd = _prepare(plan, {"1": 1}, "op-order")
    receipt = execute_write(cmd, _ctx(), engine=engine)
    assert receipt.status == "committed"
    kinds = _call_kinds(engine.calls)
    assert kinds[0] == "insert_receipt"
    assert "lock" in kinds
    assert kinds.index("lock") < kinds.index("update_sku")
    assert kinds.index("update_sku") < kinds.index("insert_audit")
    assert kinds.index("insert_audit") < kinds.index("update_receipt")
    assert engine.receipts["op-order"]["status"] == "committed"
    assert engine.receipts["op-order"]["audit_id"] == receipt.audit_id


def test_version_conflict_rolls_back_uncommitted_receipt():
    from backend.app.mysql.execute_write import ExecuteWriteError, execute_write

    engine = MemoryEngine()
    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=["1"],
        params={"status": "off_sale"},
    )
    cmd = _prepare(plan, {"1": 99}, "op-conflict")
    with pytest.raises(ExecuteWriteError) as exc:
        execute_write(cmd, _ctx(), engine=engine)
    assert exc.value.code == SkillErrorCode.VERSION_CONFLICT
    assert "op-conflict" not in engine.receipts
    assert engine.skus[1]["status"] == "on_sale"
    assert engine.skus[1]["row_version"] == 1
    assert engine.audits == {}


def test_confirm_time_scope_over_100_does_not_commit():
    from backend.app.mysql.execute_write import ExecuteWriteError, execute_write

    engine = MemoryEngine()
    engine.lock_extra_rows = 100
    plan = WritePlan(
        operation_type="adjust_sku_inventory",
        object_ids=["1"],
        params={"inventory_qty": 8},
    )
    cmd = _prepare(plan, {"1": 1}, "op-scope")
    with pytest.raises(ExecuteWriteError) as exc:
        execute_write(cmd, _ctx(), engine=engine)
    assert exc.value.code == SkillErrorCode.WRITE_SCOPE_TOO_LARGE
    assert "op-scope" not in engine.receipts
    assert engine.skus[1]["inventory_qty"] == 32


def test_defaults_to_writer_engine_not_reader(monkeypatch):
    from backend.app.mysql.execute_write import execute_write

    engine = MemoryEngine()
    roles: list[str] = []

    def fake_get_engine(role: str):
        roles.append(role)
        return engine

    monkeypatch.setattr("backend.app.mysql.pool.get_engine", fake_get_engine)
    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=["1"],
        params={"status": "off_sale"},
    )
    execute_write(_prepare(plan, {"1": 1}, "op-role"), _ctx())
    assert roles == ["writer"]


def _connect_or_skip(role: str = "writer"):
    if not Path("config.yaml").exists():
        pytest.skip("config.yaml missing")
    try:
        from sqlalchemy import text

        from backend.app.mysql.pool import get_engine

        engine = get_engine(role)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL {role} unreachable: {exc}")


@pytest.mark.integration
def test_concurrent_same_operation_id_changes_mysql_once():
    from sqlalchemy import text

    from backend.app.mysql.execute_write import execute_write
    from backend.app.mysql.pool import get_engine

    writer = _connect_or_skip("writer")
    get_engine("writer")
    sku_id = 1
    with writer.connect() as conn:
        original = dict(
            conn.execute(
                text(
                    "SELECT status, inventory_qty, row_version FROM dim_sku WHERE id = :id"
                ),
                {"id": sku_id},
            ).mappings().one()
        )

    target = "off_sale" if original["status"] == "on_sale" else "on_sale"
    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=[str(sku_id)],
        params={"status": target},
    )
    snapshots = {str(sku_id): int(original["row_version"])}
    cmd = _prepare(plan, snapshots, str(uuid.uuid4()))
    results: list = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(execute_write(cmd, _ctx(), engine=writer))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert errors == []
        assert len(results) == 2
        assert {r.status for r in results} == {"committed"}
        assert results[0].audit_id == results[1].audit_id
        assert results[0].affected_rows == results[1].affected_rows == 1
        with writer.connect() as conn:
            after = dict(
                conn.execute(
                    text(
                        "SELECT status, inventory_qty, row_version FROM dim_sku WHERE id = :id"
                    ),
                    {"id": sku_id},
                ).mappings().one()
            )
        assert after["status"] == target
        assert after["row_version"] == int(original["row_version"]) + 1
    finally:
        with writer.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dim_sku SET status = :status, inventory_qty = :inventory_qty, "
                    "row_version = :row_version WHERE id = :id"
                ),
                {**original, "id": sku_id},
            )
