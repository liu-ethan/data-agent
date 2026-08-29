from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.app.results.store import ResultStore, ResultStoreError, ResultWriteMeta
from backend.app.types import (
    FilterCond,
    LocalFilterSpec,
    PermissionSet,
    RuntimeContext,
    SkillErrorCode,
    TimeRange,
)
from scripts.init_sqlite import SQL_DIR, apply_sql

NOW = "2026-08-29T00:00:00+00:00"
TIME_RANGE = TimeRange(
    start="2026-08-01T00:00:00+00:00",
    end="2026-09-01T00:00:00+00:00",
    grain="month",
    label="2026-08",
    source="user",
)


def _ctx(
    *,
    user_id: str = "u1",
    permission_version: int = 1,
    tenant_id: str = "default",
) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=tenant_id,
        user_id=user_id,
        role="analyst",
        request_time_utc=NOW,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id=tenant_id,
            user_id=user_id,
            role="analyst",
            allowed_tables=["fact_order_item"],
            allowed_columns=[],
            allowed_metrics=["gmv"],
            allowed_write_ops=[],
            catalog_version=1,
            permission_version=permission_version,
        ),
        thread_id="t1",
    )


def _meta(
    *,
    user_id: str = "u1",
    parent_result_id: str | None = None,
    permission_version: int = 1,
    request_time_utc: str = NOW,
) -> ResultWriteMeta:
    return ResultWriteMeta(
        user_id=user_id,
        thread_id="t1",
        parent_result_id=parent_result_id,
        permission_version=permission_version,
        catalog_version=1,
        time_range=TIME_RANGE,
        request_time_utc=request_time_utc,
        metric_versions={"gmv": 1},
    )


@pytest.fixture
def store(tmp_path: Path) -> ResultStore:
    db = tmp_path / "results.sqlite"
    apply_sql(db, SQL_DIR / "results.sql")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    return ResultStore(
        results_db=db,
        results_dir=results_dir,
        ttl_hours=1,
        max_rows=100,
        max_bytes=64 * 1024,
    )


def _write_ready(store: ResultStore, rows: list[dict] | None = None, **meta_kw) -> str:
    rid = store.create_writing(_meta(**meta_kw))
    store.append_rows(
        rid,
        rows
        or [
            {"sku_id": "s1", "gmv": 10},
            {"sku_id": "s2", "gmv": 30},
            {"sku_id": "s3", "gmv": 20},
        ],
    )
    store.finalize(rid, data_as_of="2026-08-28T00:00:00+00:00")
    return rid


def test_crash_during_write_leaves_no_parquet(store: ResultStore):
    rid = store.create_writing(_meta())
    store.append_rows(rid, [{"sku_id": "s1", "gmv": 10}])
    assert (store.results_dir / f"{rid}.part").exists()
    assert not (store.results_dir / f"{rid}.parquet").exists()


def test_finalize_makes_result_readable(store: ResultStore):
    rid = _write_ready(store)
    assert (store.results_dir / f"{rid}.parquet").exists()
    assert not (store.results_dir / f"{rid}.part").exists()
    page = store.read_page(rid, _ctx(), offset=0, limit=20)
    assert page.result_id == rid
    assert page.row_count == 3
    assert page.columns == ["sku_id", "gmv"]
    assert page.schema_version == 1
    assert page.preview_rows[0]["sku_id"] == "s1"
    assert page.parent_result_id is None


def test_abort_removes_part_and_leaves_no_parquet(store: ResultStore):
    rid = store.create_writing(_meta())
    store.append_rows(rid, [{"sku_id": "s1", "gmv": 10}])
    store.abort(rid)
    assert not (store.results_dir / f"{rid}.part").exists()
    assert not (store.results_dir / f"{rid}.parquet").exists()
    with sqlite3.connect(store.results_db) as conn:
        status = conn.execute(
            "SELECT status FROM query_result WHERE result_id = ?", (rid,)
        ).fetchone()[0]
    assert status == "DELETED"


def test_expired_result_is_rejected(store: ResultStore):
    rid = _write_ready(store)
    past = "2026-08-28T00:00:00+00:00"
    with sqlite3.connect(store.results_db) as conn:
        conn.execute(
            "UPDATE query_result SET expires_at = ? WHERE result_id = ?",
            (past, rid),
        )
        conn.commit()
    with pytest.raises(ResultStoreError) as exc:
        store.read_page(rid, _ctx())
    assert exc.value.code == SkillErrorCode.RESULT_EXPIRED


def test_permission_version_change_rejects_read(store: ResultStore):
    rid = _write_ready(store, permission_version=1)
    with pytest.raises(ResultStoreError) as exc:
        store.read_page(rid, _ctx(permission_version=2))
    assert exc.value.code == SkillErrorCode.PERMISSION_CHANGED


def test_read_rejects_non_owner(store: ResultStore):
    rid = _write_ready(store, user_id="u1")
    with pytest.raises(ResultStoreError) as exc:
        store.read_page(rid, _ctx(user_id="u2"))
    assert exc.value.code == SkillErrorCode.REJECTED


def test_read_rejects_non_default_tenant(store: ResultStore):
    rid = _write_ready(store)
    with pytest.raises(ResultStoreError) as exc:
        store.read_page(rid, _ctx(tenant_id="other"))
    assert exc.value.code == SkillErrorCode.REJECTED


def test_exceeding_max_rows_aborts_without_parquet(store: ResultStore):
    rid = store.create_writing(_meta())
    with pytest.raises(ResultStoreError) as exc:
        store.append_rows(rid, [{"sku_id": f"s{i}", "gmv": i} for i in range(101)])
    assert exc.value.code == SkillErrorCode.TOO_BROAD
    assert not (store.results_dir / f"{rid}.parquet").exists()
    assert not (store.results_dir / f"{rid}.part").exists()


def test_exceeding_max_bytes_aborts_without_parquet(tmp_path: Path):
    db = tmp_path / "results.sqlite"
    apply_sql(db, SQL_DIR / "results.sql")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    tiny = ResultStore(
        results_db=db,
        results_dir=results_dir,
        ttl_hours=1,
        max_rows=100000,
        max_bytes=1,
    )
    rid = tiny.create_writing(_meta())
    with pytest.raises(ResultStoreError) as exc:
        tiny.append_rows(
            rid,
            [{"sku_id": "x" * 50, "gmv": i} for i in range(50)],
        )
    assert exc.value.code == SkillErrorCode.TOO_BROAD
    assert not (results_dir / f"{rid}.parquet").exists()
    assert not (results_dir / f"{rid}.part").exists()


def test_filter_local_sets_parent_and_applies_spec(store: ResultStore):
    parent = _write_ready(store)
    child = store.filter_local(
        parent,
        LocalFilterSpec(
            filters=[FilterCond(field="gmv", op=">=", value=20)],
            order_by=["gmv DESC"],
            select=["sku_id", "gmv"],
            topn=1,
        ),
        _ctx(),
    )
    page = store.read_page(child, _ctx())
    assert page.parent_result_id == parent
    assert page.row_count == 1
    assert page.preview_rows[0]["sku_id"] == "s2"
    assert page.preview_rows[0]["gmv"] == 30


def test_filter_local_rejects_unknown_column(store: ResultStore):
    rid = _write_ready(store)
    with pytest.raises(ResultStoreError) as exc:
        store.filter_local(
            rid,
            LocalFilterSpec(filters=[FilterCond(field="not_a_column", op="=", value=1)]),
            _ctx(),
        )
    assert exc.value.code == SkillErrorCode.REJECTED


def test_schema_version_equals_catalog_version(store: ResultStore):
    rid = _write_ready(store)
    with sqlite3.connect(store.results_db) as conn:
        row = conn.execute(
            "SELECT schema_version, catalog_version FROM query_result WHERE result_id = ?",
            (rid,),
        ).fetchone()
    assert row[0] == row[1] == 1


def test_sweep_expires_ready_then_deletes(store: ResultStore):
    rid = _write_ready(store)
    parquet = store.results_dir / f"{rid}.parquet"
    assert parquet.exists()
    future = (datetime.fromisoformat(NOW) + timedelta(hours=2)).isoformat()
    store.sweep_ttl(now=future)
    with sqlite3.connect(store.results_db) as conn:
        status = conn.execute(
            "SELECT status FROM query_result WHERE result_id = ?", (rid,)
        ).fetchone()[0]
    assert status in {"EXPIRED", "DELETED"}
    assert not parquet.exists()
    with pytest.raises(ResultStoreError) as exc:
        store.read_page(rid, _ctx())
    assert exc.value.code == SkillErrorCode.RESULT_EXPIRED
    if status == "EXPIRED":
        store.sweep_ttl(now=future)
        with sqlite3.connect(store.results_db) as conn:
            status = conn.execute(
                "SELECT status FROM query_result WHERE result_id = ?", (rid,)
            ).fetchone()[0]
        assert status == "DELETED"


def test_sweep_cleans_orphan_part(store: ResultStore):
    orphan = store.results_dir / "orphan-id.part"
    orphan.write_bytes(b"leftover")
    store.sweep_ttl(now=NOW)
    assert not orphan.exists()
