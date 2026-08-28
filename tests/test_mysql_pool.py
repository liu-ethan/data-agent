from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.mysql.pool import get_engine

READER_INSERT = (
    "INSERT INTO da_write_receipt "
    "(operation_id, request_hash, operation_type, status, payload_json) "
    "VALUES ('probe-t3', REPEAT('0', 64), 'probe', 'pending', '{}')"
)


def _connect_or_skip(role: str):
    if not Path("config.yaml").exists():
        pytest.skip("config.yaml missing")
    try:
        engine = get_engine(role)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        pytest.skip(f"MySQL {role} unreachable: {exc}")


@pytest.fixture
def reader_engine():
    return _connect_or_skip("reader")


@pytest.fixture
def writer_engine():
    return _connect_or_skip("writer")


@pytest.mark.integration
def test_reader_cannot_write(reader_engine):
    with reader_engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(SQLAlchemyError):
                conn.execute(text(READER_INSERT))
        finally:
            trans.rollback()


@pytest.mark.integration
def test_writer_cannot_drop(writer_engine):
    with writer_engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(SQLAlchemyError):
                conn.execute(text("DROP TABLE dim_sku"))
        finally:
            trans.rollback()


@pytest.mark.integration
def test_writer_cannot_update_fact_order(writer_engine):
    with writer_engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(SQLAlchemyError):
                conn.execute(text("UPDATE fact_order SET status='probe' WHERE id=1"))
        finally:
            trans.rollback()


def test_get_engine_binds_distinct_accounts():
    if not Path("config.yaml").exists():
        pytest.skip("config.yaml missing")
    from backend.app.config import load_settings

    settings = load_settings()
    reader = get_engine("reader")
    writer = get_engine("writer")
    admin = get_engine("admin")
    assert reader.url.username == settings.mysql.reader.user
    assert writer.url.username == settings.mysql.writer.user
    assert admin.url.username == settings.mysql.admin.user
    assert reader is not writer
    assert writer is not admin
    assert admin is not reader


def test_unknown_role_rejected():
    with pytest.raises(ValueError, match="role"):
        get_engine("root")  # type: ignore[arg-type]


@pytest.mark.integration
def test_migrate_is_noop_when_slice_exists(reader_engine):
    from backend.app.mysql.migrate import ensure_slice_tables

    ensure_slice_tables()
    ensure_slice_tables()
    with reader_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'dim_sku'"
            )
        )
        assert rows.scalar_one() == 1
