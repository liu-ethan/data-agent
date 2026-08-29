from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.app.config import load_settings
from backend.app.resources.domain import TENANT_ID
from backend.app.resources.sql import load_sql
from backend.app.types import PermissionSet


def _users_path(users_db: str | Path | None) -> Path:
    if users_db is not None:
        return Path(users_db)
    return Path(load_settings().sqlite.users)


def reload_permissions(
    user_id: str,
    *,
    users_db: str | Path | None = None,
    catalog_version: int = 0,
) -> PermissionSet:
    """Load the latest PermissionSet from users.sqlite. Never read Checkpoint."""
    with sqlite3.connect(_users_path(users_db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(load_sql("auth.select_permissions"), (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"unknown user: {user_id}")
    if row["tenant_id"] != TENANT_ID:
        raise ValueError(f"tenant_id must be {TENANT_ID!r}, got {row['tenant_id']!r}")
    return PermissionSet(
        tenant_id=TENANT_ID,
        user_id=row["user_id"],
        role=row["role"],
        allowed_tables=json.loads(row["allowed_tables_json"]),
        allowed_columns=json.loads(row["allowed_columns_json"]),
        allowed_metrics=json.loads(row["allowed_metrics_json"]),
        allowed_write_ops=json.loads(row["allowed_write_ops_json"]),
        catalog_version=catalog_version,
        permission_version=int(row["permission_version"]),
    )
