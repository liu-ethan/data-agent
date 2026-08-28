from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.app.config import load_settings
from backend.app.types import PermissionSet

TENANT_ID = "default"


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
        row = conn.execute(
            """
            SELECT u.user_id, u.role, u.tenant_id, p.permission_version,
                   p.allowed_tables_json, p.allowed_columns_json,
                   p.allowed_metrics_json, p.allowed_write_ops_json
            FROM app_user u
            JOIN user_permission p ON p.user_id = u.user_id
            WHERE u.user_id = ? AND u.is_active = 1
            ORDER BY p.permission_version DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
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
