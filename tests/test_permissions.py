from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.app.runtime.context import build_runtime_context
from backend.app.runtime.permissions import reload_permissions
from backend.app.types import Intent, RuntimeContext


from scripts.init_sqlite import SQL_DIR

USERS_DDL = (SQL_DIR / "users.sql").read_text(encoding="utf-8")


@pytest.fixture
def users_db(tmp_path: Path) -> Path:
    db = tmp_path / "users.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(USERS_DDL)
        conn.execute(
            "INSERT INTO app_user VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            ("u1", "u1", "hash", "User One", "operator", "default", "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO user_permission VALUES (?, 1, ?, ?, ?, ?, ?)",
            (
                "u1",
                json.dumps(["fact_order", "dim_sku"]),
                json.dumps(["data-agent-ecommerce.fact_order.id"]),
                json.dumps(["gmv"]),
                json.dumps(["update_sku_status"]),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()
    return db


def test_permissions_are_reloaded_not_cached_from_checkpoint(users_db: Path):
    p1 = reload_permissions("u1", users_db=users_db)
    p2 = reload_permissions("u1", users_db=users_db)
    assert p1.permission_version == p2.permission_version
    assert p1.allowed_tables

    with sqlite3.connect(users_db) as conn:
        conn.execute(
            "INSERT INTO user_permission VALUES (?, 2, ?, ?, ?, ?, ?)",
            (
                "u1",
                json.dumps(["fact_order"]),
                json.dumps(["data-agent-ecommerce.fact_order.id"]),
                json.dumps(["gmv"]),
                json.dumps([]),
                "2026-01-02T00:00:00+00:00",
            ),
        )
        conn.commit()

    p3 = reload_permissions("u1", users_db=users_db)
    assert p1.permission_version == 1
    assert p3.permission_version == 2
    assert p3.allowed_tables == ["fact_order"]
    assert p1.tenant_id == "default"
    assert p3.tenant_id == "default"
    assert p3.role == "operator"


def test_tenant_id_must_be_default(users_db: Path):
    with sqlite3.connect(users_db) as conn:
        conn.execute("UPDATE app_user SET tenant_id = 'other' WHERE user_id = 'u1'")
        conn.commit()
    with pytest.raises(ValueError, match="default"):
        reload_permissions("u1", users_db=users_db)


def test_unknown_user_raises(users_db: Path):
    with pytest.raises(LookupError):
        reload_permissions("missing", users_db=users_db)


def test_runtime_context_has_no_db_connection(users_db: Path):
    ctx = build_runtime_context(
        user_id="u1",
        thread_id="t1",
        request_time_utc="2026-08-28T16:00:00+00:00",
        timezone="Asia/Shanghai",
        users_db=users_db,
    )
    assert ctx.tenant_id == "default"
    assert ctx.user_id == "u1"
    assert ctx.thread_id == "t1"
    assert ctx.permissions.permission_version == 1
    assert "connection" not in RuntimeContext.model_fields
    assert "engine" not in RuntimeContext.model_fields


def test_intent_has_followup_not_filter_or_requery():
    assert Intent.FOLLOWUP.value == "followup"
    names = {i.name for i in Intent}
    assert "FOLLOWUP_FILTER" not in names
    assert "FOLLOWUP_REQUERY" not in names
