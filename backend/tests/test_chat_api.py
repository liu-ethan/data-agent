import importlib
from unittest.mock import patch

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


def _auth(client):
    r = client.post(
        "/api/auth/register",
        json={"username": "c1", "password": "password123", "role": "analyst"},
    )
    return r.json()["access_token"]


def test_chat_sse_happy_path(client):
    token = _auth(client)
    with patch(
        "app.agent.sql_generator.generate_sql",
        return_value="SELECT COUNT(*) AS c FROM orders",
    ), patch(
        "app.agent.answer_composer.compose_answer",
        return_value="订单很多",
    ):
        with client.stream(
            "POST",
            "/api/chat",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={"question": "有多少订单？", "session_id": "default"},
        ) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())
    assert "event: run_start" in text
    assert "event: sql" in text
    assert "event: rows" in text
    assert "event: answer" in text
    assert "event: done" in text


def test_chat_requires_auth(client):
    r = client.post("/api/chat", json={"question": "hi"})
    assert r.status_code == 401
