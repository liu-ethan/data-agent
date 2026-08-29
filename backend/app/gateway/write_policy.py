from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlglot import exp

from backend.app.gateway.ast import ParseMysqlError, parse_mysql
from backend.app.gateway.read_policy import GatewayDecision
from backend.app.types import SkillErrorCode, WritePlan

_HASH_KEYS = ("operation_type", "object_ids_sorted", "params", "filters", "version_snapshots")


class WriteGatewayError(Exception):
    def __init__(self, code: SkillErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _unsafe(reason: str) -> GatewayDecision:
    return GatewayDecision(ok=False, reason=reason, kind="unsafe")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_hash(plan: WritePlan, version_snapshots: dict[str, Any]) -> str:
    payload = {
        "operation_type": plan.operation_type,
        "object_ids_sorted": sorted(plan.object_ids, key=str),
        "params": plan.params,
        "filters": [item.model_dump() for item in plan.filters],
        "version_snapshots": {str(key): int(val) for key, val in version_snapshots.items()},
    }
    body = canonical_json({key: payload[key] for key in _HASH_KEYS})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def check_write_sql(sql: str, params: dict[str, Any], op_def: Any) -> GatewayDecision:
    del params
    try:
        tree = parse_mysql(sql)
        template = parse_mysql(op_def.sql_template)
    except ParseMysqlError as exc:
        return _unsafe(str(exc))
    if not isinstance(tree, exp.Update) or tree != template:
        return _unsafe("SQL is not isomorphic to the registered template")
    return GatewayDecision(ok=True, reason=None, kind="ok")
