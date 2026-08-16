"""Authoritative MySQL permission lookup.

JWT answers *who* made the request; these tables answer what the user may see.
Claims never supply row scopes, roles or policy versions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from ..auth import Principal
from ..errors import RuntimeAgentError
from ..models import PermissionContext, ScopeMode
from ..repositories.runtime import RuntimePersistence


class PermissionService:
    def __init__(self, persistence: RuntimePersistence, config: dict[str, Any]) -> None:
        self.persistence = persistence
        self.config = config

    def for_principal(self, principal: Principal) -> PermissionContext:
        with self.persistence.engine.connect() as connection:
            user = connection.execute(text("SELECT user_id, role_name, active, policy_version FROM app_users WHERE user_id=:user_id"), {"user_id": principal.user_id}).mappings().first()
            if not user or not user["active"]:
                raise RuntimeAgentError("PERMISSION_DENIED", "account is not active")
            scopes = connection.execute(text(
                "SELECT shop_id FROM app_user_shop_scopes "
                "WHERE user_id=:user_id AND policy_version=:policy_version "
                "ORDER BY shop_id"), {
                    "user_id": principal.user_id,
                    "policy_version": user["policy_version"],
                }).scalars().all()
        # The signed role is a session assertion only; database role wins and a
        # mismatch invalidates a stale/escalated token.
        if user["role_name"] not in principal.roles:
            raise RuntimeAgentError("PERMISSION_DENIED", "token role is stale")
        return PermissionContext(user_id=principal.user_id, roles=[user["role_name"]],
            scope_mode=ScopeMode.ALLOWLIST if scopes else ScopeMode.NONE, allowed_shop_ids=list(scopes),
            denied_classifications=list(self.config.get("denied_classifications", ["PHONE", "ID_CARD"])),
            allowed_domains=list(self.config.get("allowed_domains", ["ECOMMERCE_TRADE"])),
            allowed_source_ids=list(self.config.get("allowed_source_ids", ["mysql_ecommerce_local"])),
            object_scope_ref=f"acl_objects_{principal.user_id}_{user['policy_version']}",
            row_scope_refs={"shop_id": f"scope_shops_{principal.user_id}_{user['policy_version']}"},
            policy_version=user["policy_version"])
