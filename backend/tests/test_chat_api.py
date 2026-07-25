import importlib
import json
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
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "订单量",
        "route_mode": "react",
        "slots": {
            "metrics": ["order_count"],
            "time_range": "last_month",
            "group_by": [],
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ), patch(
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
    assert "event: route_decision" in text
    assert "event: sql" in text
    assert "event: rows" in text
    assert "event: answer" in text
    assert "event: done" in text
    assert "event: tool_start" in text
    assert "event: tool_end" in text


def test_chat_sse_clarification_has_no_sql(client):
    token = _auth(client)
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.7,
        "summary": "模糊渠道表现",
        "route_mode": "react",
        "slots": {"metrics": [], "time_range": None, "group_by": ["channel"]},
        "need_clarification": True,
        "clarification_question": "想按 GMV 还是订单量？时间用近 7 天还是 30 天？",
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ):
        with client.stream(
            "POST",
            "/api/chat",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream",
            },
            json={"question": "最近哪个渠道表现最好？", "session_id": "default"},
        ) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())
    assert "event: route_decision" in text
    assert "event: answer" in text
    assert '"need_clarification": true' in text
    assert "event: sql" not in text


def test_chat_requires_auth(client):
    r = client.post("/api/chat", json={"question": "hi"})
    assert r.status_code == 401
