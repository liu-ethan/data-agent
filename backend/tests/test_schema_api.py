import importlib

import pytest
from fastapi.testclient import TestClient

from app.db.init_db import init_database
from app.db.schema import APP_TABLES, BUSINESS_TABLES


@pytest.fixture()
def client(tmp_db_path):
    init_database(reset=True)
    from app.config import get_settings

    get_settings.cache_clear()
    import app.main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def test_schema_returns_only_business_tables(client):
    r = client.get("/api/schema")
    assert r.status_code == 200
    tables = r.json()["tables"]
    names = {t["name"] for t in tables}
    assert names == set(BUSINESS_TABLES)
    assert names.isdisjoint(APP_TABLES)
    orders = next(t for t in tables if t["name"] == "orders")
    col_names = {c["name"] for c in orders["columns"]}
    assert "pay_amount" in col_names
    assert "X-Request-Id" in r.headers


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
