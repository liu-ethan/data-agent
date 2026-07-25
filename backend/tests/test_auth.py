import importlib

import pytest
from fastapi.testclient import TestClient

from app.db.init_db import init_database


@pytest.fixture()
def client(tmp_db_path):
    init_database(reset=True)
    from app.config import get_settings

    get_settings.cache_clear()
    import app.main as main_mod

    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def test_register_analyst_returns_jwt(client):
    r = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "password123",
            "role": "analyst",
            "invite_code": None,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "analyst"


def test_register_admin_requires_invite(client):
    bad = client.post(
        "/api/auth/register",
        json={
            "username": "boss",
            "password": "password123",
            "role": "admin",
            "invite_code": "wrong",
        },
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/register",
        json={
            "username": "boss",
            "password": "password123",
            "role": "admin",
            "invite_code": "test-invite",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["role"] == "admin"


def test_login_and_me(client):
    client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "password": "password123",
            "role": "analyst",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "password123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "bob"
    assert client.get("/api/auth/me").status_code == 401


def test_demo_analyst_login(client):
    r = client.post(
        "/api/auth/login",
        json={"username": "demo_analyst", "password": "demo1234"},
    )
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "analyst"
