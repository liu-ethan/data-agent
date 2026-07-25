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


def _token(client, username="alice", role="analyst", invite=None):
    body = {"username": username, "password": "password123", "role": role}
    if role == "admin":
        body["invite_code"] = invite or "test-invite"
    response = client.post("/api/auth/register", json=body)
    return response.json()["access_token"]


def test_schema_requires_auth(client):
    assert client.get("/api/schema").status_code == 401


def test_schema_returns_only_business_tables(client):
    token = _token(client)
    r = client.get(
        "/api/schema",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    tables = r.json()["tables"]
    names = {t["name"] for t in tables}
    assert names == set(BUSINESS_TABLES)
    assert names.isdisjoint(APP_TABLES)
    orders = next(t for t in tables if t["name"] == "orders")
    col_names = {c["name"] for c in orders["columns"]}
    assert "pay_amount" in col_names
    assert "X-Request-Id" in r.headers


def test_schema_analyst_hides_sensitive_columns(client):
    token = _token(client)
    r = client.get(
        "/api/schema",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    users = next(t for t in r.json()["tables"] if t["name"] == "users")
    cols = {c["name"] for c in users["columns"]}
    assert "city" in cols
    assert cols.isdisjoint({"name", "phone", "email", "id_card"})


def test_schema_admin_sees_sensitive_columns(client):
    token = _token(client, "admin1", "admin")
    r = client.get(
        "/api/schema",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    users = next(t for t in r.json()["tables"] if t["name"] == "users")
    cols = {c["name"] for c in users["columns"]}
    assert {"name", "phone", "email", "id_card"}.issubset(cols)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
