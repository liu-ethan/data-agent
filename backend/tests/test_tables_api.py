import importlib

import pytest
from fastapi.testclient import TestClient

from app.db.init_db import init_database
from app.db.schema import BUSINESS_TABLES


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
    return client.post("/api/auth/register", json=body).json()["access_token"]


def test_tables_require_auth(client):
    assert client.get("/api/tables").status_code == 401


def test_list_business_tables(client):
    token = _token(client)
    r = client.get("/api/tables", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tables"]}
    assert names == set(BUSINESS_TABLES)
    orders = next(t for t in r.json()["tables"] if t["name"] == "orders")
    assert orders["row_count"] > 0
    assert orders["column_count"] > 0


def test_table_rows_page_size_fixed_and_analyst_hides_sensitive(client):
    token = _token(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/tables/users?page=1&page_size=999", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["page_size"] == 50
    assert len(body["rows"]) <= 50
    cols = {c["name"] for c in body["columns"]}
    assert cols.isdisjoint({"name", "phone", "email", "id_card"})
    for row in body["rows"]:
        assert set(row).isdisjoint({"name", "phone", "email", "id_card"})


def test_admin_sees_sensitive_columns(client):
    token = _token(client, "admin1", "admin")
    r = client.get(
        "/api/tables/users?page=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    cols = {c["name"] for c in r.json()["columns"]}
    assert {"name", "phone", "email", "id_card"}.issubset(cols)


def test_app_table_404(client):
    token = _token(client)
    r = client.get(
        "/api/tables/app_users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


def test_unknown_table_404(client):
    token = _token(client)
    r = client.get(
        "/api/tables/not_a_table",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
