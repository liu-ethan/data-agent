from __future__ import annotations

from pathlib import Path

from backend.app.config import load_settings
from backend.app.runtime.permissions import TENANT_ID, reload_permissions
from backend.app.types import RuntimeContext


def build_runtime_context(
    user_id: str,
    thread_id: str,
    request_time_utc: str,
    *,
    timezone: str | None = None,
    users_db: str | Path | None = None,
    catalog_version: int = 0,
) -> RuntimeContext:
    tz = timezone if timezone is not None else load_settings().app.timezone
    permissions = reload_permissions(
        user_id,
        users_db=users_db,
        catalog_version=catalog_version,
    )
    return RuntimeContext(
        tenant_id=TENANT_ID,
        user_id=user_id,
        role=permissions.role,
        request_time_utc=request_time_utc,
        timezone=tz,
        permissions=permissions,
        thread_id=thread_id,
    )
