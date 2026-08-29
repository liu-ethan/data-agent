from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.app.gateway.write_policy import check_write_sql
from backend.app.skills.write.registry import PreparedCommand, load_write_ops
from backend.app.types import RuntimeContext, SkillErrorCode

_ALLOWED_TABLES = frozenset({"dim_sku"})


class ExecuteWriteError(Exception):
    def __init__(self, code: SkillErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class WriteReceipt(BaseModel):
    operation_id: str
    request_hash: str
    operation_type: str
    status: Literal["pending", "committed", "unknown"]
    affected_rows: int | None = None
    audit_id: str | None = None


def execute_write(
    cmd: PreparedCommand,
    ctx: RuntimeContext,
    *,
    engine: Engine | None = None,
    _retry: bool = True,
) -> WriteReceipt:
    _reject_if_invalid(cmd)
    if engine is None:
        from backend.app.mysql.pool import get_engine

        engine = get_engine("writer")
    try:
        return _commit(cmd, ctx, engine, _retry=_retry)
    except ExecuteWriteError:
        raise
    except SQLAlchemyError as exc:
        return _unknown_or_existing(engine, cmd, exc)


def _reject_if_invalid(cmd: PreparedCommand) -> None:
    if not cmd.operation_id or not cmd.request_hash:
        raise ExecuteWriteError(SkillErrorCode.REJECTED, "operation_id and request_hash are required")
    if len(cmd.object_ids) > cmd.max_affected_rows:
        raise ExecuteWriteError(
            SkillErrorCode.WRITE_SCOPE_TOO_LARGE,
            f"write scope {len(cmd.object_ids)} exceeds max_affected_rows={cmd.max_affected_rows}",
        )
    ops = load_write_ops()
    op = ops.get(cmd.operation_type)
    if op is None:
        raise ExecuteWriteError(SkillErrorCode.REJECTED, f"unknown operation: {cmd.operation_type}")
    decision = check_write_sql(cmd.sql, cmd.params, op)
    if not decision.ok:
        raise ExecuteWriteError(SkillErrorCode.UNSAFE_SQL, decision.reason or "gateway rejected")
    if cmd.target_table not in _ALLOWED_TABLES:
        raise ExecuteWriteError(SkillErrorCode.UNSAFE_SQL, "table is not writable")


def _commit(cmd: PreparedCommand, ctx: RuntimeContext, engine: Engine, *, _retry: bool) -> WriteReceipt:
    with engine.connect() as conn:
        trans = conn.begin()
        rolled_back = False
        try:
            try:
                conn.execute(
                    text(
                        "INSERT INTO da_write_receipt "
                        "(operation_id, request_hash, operation_type, status, payload_json) "
                        "VALUES (:operation_id, :request_hash, :operation_type, :status, :payload_json)"
                    ),
                    {
                        "operation_id": cmd.operation_id,
                        "request_hash": cmd.request_hash,
                        "operation_type": cmd.operation_type,
                        "status": "pending",
                        "payload_json": json.dumps(_payload(cmd), ensure_ascii=False),
                    },
                )
            except IntegrityError:
                trans.rollback()
                rolled_back = True
                existing = _read_receipt(engine, cmd)
                if existing is not None:
                    return existing
                if _retry:
                    return execute_write(cmd, ctx, engine=engine, _retry=False)
                raise ExecuteWriteError(
                    SkillErrorCode.REJECTED,
                    "duplicate operation_id but receipt is missing",
                ) from None
            before = _reverify(conn, cmd)
            result = conn.execute(_bound(cmd.sql, cmd.params), cmd.params)
            affected = int(result.rowcount or 0)
            audit_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO da_write_audit "
                    "(audit_id, operation_id, actor_user_id, operation_type, "
                    "target_table, target_pk, before_json, after_json) "
                    "VALUES (:audit_id, :operation_id, :actor_user_id, :operation_type, "
                    ":target_table, :target_pk, :before_json, :after_json)"
                ),
                {
                    "audit_id": audit_id,
                    "operation_id": cmd.operation_id,
                    "actor_user_id": ctx.user_id,
                    "operation_type": cmd.operation_type,
                    "target_table": cmd.target_table,
                    "target_pk": json.dumps([int(i) for i in cmd.object_ids]),
                    "before_json": json.dumps(before, ensure_ascii=False, default=str),
                    "after_json": json.dumps(_after(before, cmd), ensure_ascii=False, default=str),
                },
            )
            conn.execute(
                text(
                    "UPDATE da_write_receipt "
                    "SET status = :status, affected_rows = :affected_rows, audit_id = :audit_id "
                    "WHERE operation_id = :operation_id"
                ),
                {
                    "operation_id": cmd.operation_id,
                    "status": "committed",
                    "affected_rows": affected,
                    "audit_id": audit_id,
                },
            )
            trans.commit()
            return WriteReceipt(
                operation_id=cmd.operation_id,
                request_hash=cmd.request_hash,
                operation_type=cmd.operation_type,
                status="committed",
                affected_rows=affected,
                audit_id=audit_id,
            )
        except ExecuteWriteError:
            if not rolled_back:
                trans.rollback()
            raise
        except Exception:
            if not rolled_back:
                trans.rollback()
            raise


def _reverify(conn: Any, cmd: PreparedCommand) -> list[dict[str, Any]]:
    table = cmd.target_table
    sql = f"SELECT id, row_version, status, inventory_qty FROM `{table}` WHERE id IN :ids FOR UPDATE"
    rows = [
        dict(row)
        for row in conn.execute(_bound(sql, cmd.params), {"ids": cmd.params["ids"]}).mappings().all()
    ]
    if len(rows) > cmd.max_affected_rows:
        raise ExecuteWriteError(
            SkillErrorCode.WRITE_SCOPE_TOO_LARGE,
            f"locked scope {len(rows)} exceeds max_affected_rows={cmd.max_affected_rows}",
        )
    found = {str(row["id"]): int(row["row_version"]) for row in rows}
    expected = {str(key): int(val) for key, val in cmd.version_snapshots.items()}
    requested = {str(item) for item in cmd.object_ids}
    found_requested = {key: found[key] for key in requested if key in found}
    if found_requested != expected:
        raise ExecuteWriteError(SkillErrorCode.VERSION_CONFLICT, "row_version snapshot mismatch")
    return rows


def _after(before: list[dict[str, Any]], cmd: PreparedCommand) -> list[dict[str, Any]]:
    updated = []
    for row in before:
        item = dict(row)
        if "status" in cmd.params:
            item["status"] = cmd.params["status"]
        if "inventory_qty" in cmd.params:
            item["inventory_qty"] = cmd.params["inventory_qty"]
        item["row_version"] = int(item["row_version"]) + 1
        updated.append(item)
    return updated


def _payload(cmd: PreparedCommand) -> dict[str, Any]:
    return {
        "operation_type": cmd.operation_type,
        "object_ids": cmd.object_ids,
        "params": {k: v for k, v in cmd.params.items() if k != "ids"},
        "version_snapshots": cmd.version_snapshots,
    }


def _read_receipt(engine: Engine, cmd: PreparedCommand) -> WriteReceipt | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT operation_id, request_hash, operation_type, status, "
                    "affected_rows, audit_id FROM da_write_receipt "
                    "WHERE operation_id = :operation_id"
                ),
                {"operation_id": cmd.operation_id},
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    if str(row["request_hash"]) != cmd.request_hash:
        raise ExecuteWriteError(SkillErrorCode.REJECTED, "operation_id reused with a different request_hash")
    return WriteReceipt(
        operation_id=str(row["operation_id"]),
        request_hash=str(row["request_hash"]),
        operation_type=str(row["operation_type"]),
        status=row["status"],
        affected_rows=row["affected_rows"],
        audit_id=row["audit_id"],
    )


def _unknown_or_existing(engine: Engine, cmd: PreparedCommand, exc: BaseException) -> WriteReceipt:
    try:
        existing = _read_receipt(engine, cmd)
    except SQLAlchemyError:
        return WriteReceipt(
            operation_id=cmd.operation_id or "",
            request_hash=cmd.request_hash or "",
            operation_type=cmd.operation_type,
            status="unknown",
        )
    if existing is None:
        raise ExecuteWriteError(SkillErrorCode.REJECTED, f"write failed and receipt is absent: {exc}") from exc
    return existing


def _bound(sql: str, params: dict[str, Any]):
    stmt = text(sql)
    expanding = [
        bindparam(name, expanding=True)
        for name, value in params.items()
        if isinstance(value, (list, tuple))
    ]
    if expanding:
        stmt = stmt.bindparams(*expanding)
    return stmt
