from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from backend.app.skills.write.registry import PreparedCommand
from backend.app.resources.domain import writable_tables
from backend.app.resources.sql import load_sql
from backend.app.types import SkillErrorCode, WritePlan

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PREVIEW_ROW_LIMIT = 20


class PreviewError(Exception):
    def __init__(self, code: SkillErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _aware(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def approval_expires_at(prepared_at: str, ttl_minutes: int) -> str:
    return (_aware(prepared_at) + timedelta(minutes=ttl_minutes)).isoformat()


def approval_expired(expires_at: str, request_time_utc: str) -> bool:
    return _aware(request_time_utc) >= _aware(expires_at)


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


def precheck_rows(cmd: PreparedCommand, engine: Engine) -> list[dict[str, Any]]:
    """Reader precheck: explicit PKs and row_version, never FOR UPDATE."""
    table = cmd.target_table
    if table not in writable_tables() or not _IDENT.match(table):
        raise PreviewError(SkillErrorCode.REJECTED, "invalid target table")
    ids = cmd.params.get("ids") or [int(i) for i in cmd.object_ids]
    if not ids:
        raise PreviewError(SkillErrorCode.WRITE_SCOPE_TOO_LARGE, "write targets are missing")
    if len(ids) > cmd.max_affected_rows:
        raise PreviewError(
            SkillErrorCode.WRITE_SCOPE_TOO_LARGE,
            f"write scope {len(ids)} exceeds max_affected_rows={cmd.max_affected_rows}",
        )
    sql = load_sql("write.precheck_target_rows", table=table)
    with engine.connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(_bound(sql, {"ids": ids}), {"ids": ids}).mappings().all()
        ]
    if len(rows) > cmd.max_affected_rows:
        raise PreviewError(
            SkillErrorCode.WRITE_SCOPE_TOO_LARGE,
            f"precheck scope {len(rows)} exceeds max_affected_rows={cmd.max_affected_rows}",
        )
    found = {str(row["id"]) for row in rows}
    requested = {str(item) for item in cmd.object_ids}
    if found != requested:
        raise PreviewError(SkillErrorCode.REJECTED, "write targets are missing or not unique")
    return rows


def snapshots_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {str(row["id"]): int(row["row_version"]) for row in rows}


def describe_changes(rows: list[dict[str, Any]], plan: WritePlan) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if plan.operation_type == "update_sku_status":
        field, target = "status", plan.params["status"]
    elif plan.operation_type == "adjust_sku_inventory":
        field, target = "inventory_qty", plan.params["inventory_qty"]
    else:
        return changes
    for row in rows:
        changes.append(
            {
                "id": str(row["id"]),
                "field": field,
                "from": row[field],
                "to": target,
            }
        )
    return changes


def build_preview(
    *,
    operation_id: str,
    request_hash: str,
    plan: WritePlan,
    cmd: PreparedCommand,
    rows: list[dict[str, Any]],
    ctx_user_id: str,
    prepared_at: str,
    expires_at: str,
) -> dict[str, Any]:
    snapshots = snapshots_from_rows(rows)
    serialized = []
    for row in rows[:_PREVIEW_ROW_LIMIT]:
        serialized.append(
            {
                "id": str(row["id"]),
                "status": row.get("status"),
                "inventory_qty": row.get("inventory_qty"),
                "row_version": int(row["row_version"]),
            }
        )
    return {
        "operation_id": operation_id,
        "request_hash": request_hash,
        "operation_type": plan.operation_type,
        "object_ids": list(cmd.object_ids),
        "params": dict(plan.params),
        "filters": [item.model_dump() for item in plan.filters],
        "target_pks": list(cmd.object_ids),
        "affected_rows": len(rows),
        "version_snapshots": snapshots,
        "rows": serialized,
        "changes": describe_changes(rows, plan),
        "operator_user_id": ctx_user_id,
        "prepared_at": prepared_at,
        "expires_at": expires_at,
        "plan": plan.model_dump(),
    }
