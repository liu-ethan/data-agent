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


def _token(client):
    response = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "password123",
            "role": "analyst",
        },
    )
    return response.json()["access_token"]


def test_examples_requires_auth(client):
    assert client.get("/api/examples").status_code == 401


def test_examples_returns_at_least_fifteen_questions(client):
    token = _token(client)
    response = client.get(
        "/api/examples",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    examples = response.json()["examples"]
    assert len(examples) >= 15
    assert all(
        isinstance(example["id"], str) and isinstance(example["question"], str)
        for example in examples
    )
