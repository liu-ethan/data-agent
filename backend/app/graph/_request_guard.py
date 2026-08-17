"""Deterministic rejection of dangerous or out-of-scope natural language.

Gateway AST checks remain the last line of defense. This guard keeps
prompt-injection and explicit DDL/DML from entering GENERATE as ordinary
data questions, so Spec 07 security cases fail closed with stable codes.
"""

from __future__ import annotations

import re

from ..models import PermissionContext

_FORBIDDEN_SQL = (
    "drop table",
    "delete from",
    "insert into",
    "update ",
    "truncate ",
    "grant ",
    "union select",
    "information_schema",
    "mysql.user",
    "load_file",
    "into outfile",
    "select into outfile",
)
_JAILBREAK = (
    "忽略以上指令",
    "忽略之前",
    "绕过店铺权限",
    "绕过权限",
    "以管理员身份",
)
_SHOP_ID = re.compile(r"shop_\d+", re.IGNORECASE)


def forbidden_request(message: str, permission: PermissionContext | None = None) -> str | None:
    """Return a public error code when ``message`` must be rejected."""
    lowered = message.lower()
    if any(token in lowered for token in _FORBIDDEN_SQL) or any(
        token in message for token in _JAILBREAK
    ):
        return "SQL_FORBIDDEN_OPERATION"
    if permission is not None:
        allowed = {item.lower() for item in permission.allowed_shop_ids}
        mentioned = {item.lower() for item in _SHOP_ID.findall(message)}
        if mentioned and not mentioned.issubset(allowed):
            return "PERMISSION_DENIED"
    return None
