from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from backend.app.gateway.write_policy import WriteGatewayError
from backend.app.types import SkillErrorCode, WritePlan

ALLOWED_OPERATION_TYPES = frozenset({"update_sku_status", "adjust_sku_inventory"})
_STATUS_VALUES = frozenset({"on_sale", "off_sale"})
_MAX_AFFECTED_ROWS = 100


class WriteOpDef(BaseModel):
    operation_type: str
    target_table: str
    allowed_columns: list[str]
    sql_template: str
    required_params: list[str] = []
    max_affected_rows: int = _MAX_AFFECTED_ROWS
    version_predicate: str | None = None
    locking_read: bool = False
    must_hitl: bool = True


class PreparedCommand(BaseModel):
    operation_type: str
    target_table: str
    sql: str
    params: dict[str, Any]
    object_ids: list[str]
    max_affected_rows: int = _MAX_AFFECTED_ROWS
    version_predicate: str | None = None
    locking_read: bool = False
    operation_id: str | None = None
    request_hash: str | None = None
    version_snapshots: dict[str, int] = Field(default_factory=dict)


def _default_yaml() -> Path:
    return Path(__file__).resolve().parents[4] / "seeds" / "write_ops.yaml"


def load_write_ops(path: str | Path | None = None) -> dict[str, WriteOpDef]:
    source = Path(path) if path is not None else _default_yaml()
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise WriteGatewayError(SkillErrorCode.REJECTED, "write_ops.yaml must be a list")
    ops: dict[str, WriteOpDef] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        op_type = item.get("operation_type")
        if op_type not in ALLOWED_OPERATION_TYPES:
            continue
        ops[op_type] = WriteOpDef.model_validate(
            {**item, "must_hitl": True, "max_affected_rows": _MAX_AFFECTED_ROWS}
        )
    return ops


def build_command(plan: WritePlan) -> PreparedCommand:
    ops = load_write_ops()
    op = ops.get(plan.operation_type)
    if op is None:
        raise WriteGatewayError(SkillErrorCode.REJECTED, f"unknown operation: {plan.operation_type}")
    try:
        ids = sorted({int(item) for item in plan.object_ids})
    except (TypeError, ValueError) as exc:
        raise WriteGatewayError(SkillErrorCode.REJECTED, "object_ids must be integers") from exc
    if len(ids) == 0 or len(ids) > op.max_affected_rows:
        raise WriteGatewayError(
            SkillErrorCode.WRITE_SCOPE_TOO_LARGE,
            f"write scope {len(ids)} exceeds max_affected_rows={op.max_affected_rows}",
        )
    params: dict[str, Any] = {"ids": ids}
    for name in op.required_params:
        if name not in plan.params:
            raise WriteGatewayError(SkillErrorCode.REJECTED, f"missing param: {name}")
        params[name] = plan.params[name]
    if plan.operation_type == "update_sku_status":
        status = params["status"]
        if status not in _STATUS_VALUES:
            raise WriteGatewayError(SkillErrorCode.REJECTED, "status must be on_sale or off_sale")
    elif plan.operation_type == "adjust_sku_inventory":
        try:
            params["inventory_qty"] = int(params["inventory_qty"])
        except (TypeError, ValueError) as exc:
            raise WriteGatewayError(SkillErrorCode.REJECTED, "inventory_qty must be an integer") from exc
    return PreparedCommand(
        operation_type=op.operation_type,
        target_table=op.target_table,
        sql=op.sql_template,
        params=params,
        object_ids=[str(item) for item in ids],
        max_affected_rows=op.max_affected_rows,
        version_predicate=op.version_predicate,
        locking_read=op.locking_read,
    )
