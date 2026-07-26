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


def _token(client, username="alice"):
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123", "role": "analyst"},
    )
    return r.json()["access_token"]


def test_sessions_require_auth(client):
    assert client.get("/api/sessions").status_code == 401


def test_create_list_and_turns(client):
    token = _token(client)
    h = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/sessions", headers=h)
    assert created.status_code == 200
    sid = created.json()["id"]
    assert sid.startswith("sess_")

    listed = client.get("/api/sessions", headers=h)
    assert listed.status_code == 200
    assert any(s["id"] == sid for s in listed.json()["sessions"])

    turns = client.get(f"/api/sessions/{sid}/turns", headers=h)
    assert turns.status_code == 200
    assert turns.json()["turns"] == []

    me = client.get("/api/auth/me", headers=h).json()
    from app.agent.memory.store import save_turn, set_session_title_if_empty

    save_turn(
        session_id=sid,
        user_id=str(me["id"]),
        question="上个月 GMV 最高的 5 个渠道是什么？",
        intent="channel_sales",
        sql_text="SELECT 1",
        slots={
            "metrics": ["gmv"],
            "filters": {},
            "group_by": ["channel"],
            "time_range": None,
        },
        result_summary="top channels",
    )
    set_session_title_if_empty(
        sid, str(me["id"]), "上个月 GMV 最高的 5 个渠道是什么？"
    )
    turns2 = client.get(f"/api/sessions/{sid}/turns", headers=h).json()["turns"]
    assert len(turns2) == 1
    assert "GMV" in turns2[0]["question"]
    sessions = client.get("/api/sessions", headers=h).json()["sessions"]
    mine = next(s for s in sessions if s["id"] == sid)
    assert mine["title"]
    assert mine["turn_count"] == 1


def test_delete_session_ok_and_404(client):
    token = _token(client, "del_user")
    h = {"Authorization": f"Bearer {token}"}
    sid = client.post("/api/sessions", headers=h).json()["id"]
    assert client.delete(f"/api/sessions/{sid}", headers=h).status_code == 204
    listed = client.get("/api/sessions", headers=h).json()["sessions"]
    assert all(s["id"] != sid for s in listed)
    r2 = client.delete(f"/api/sessions/{sid}", headers=h)
    assert r2.status_code == 404
    assert r2.json()["detail"] == "Session not found"
    assert client.get(f"/api/sessions/{sid}/turns", headers=h).status_code == 404


def test_delete_session_other_user_404(client):
    t1 = _token(client, "del_u1")
    t2 = _token(client, "del_u2")
    sid = client.post(
        "/api/sessions", headers={"Authorization": f"Bearer {t1}"}
    ).json()["id"]
    r = client.delete(
        f"/api/sessions/{sid}",
        headers={"Authorization": f"Bearer {t2}"},
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Session not found"
    owner_turns = client.get(
        f"/api/sessions/{sid}/turns",
        headers={"Authorization": f"Bearer {t1}"},
    )
    assert owner_turns.status_code == 200


def test_turns_other_user_404(client):
    t1 = _token(client, "u1")
    t2 = _token(client, "u2")
    sid = client.post(
        "/api/sessions", headers={"Authorization": f"Bearer {t1}"}
    ).json()["id"]
    r = client.get(
        f"/api/sessions/{sid}/turns",
        headers={"Authorization": f"Bearer {t2}"},
    )
    assert r.status_code == 404


def test_memory_save_sets_session_title(client):
    from unittest.mock import patch

    from app.agent.nodes.memory_save import memory_save

    token = _token(client, "title_user")
    h = {"Authorization": f"Bearer {token}"}
    sid = client.post("/api/sessions", headers=h).json()["id"]
    me = client.get("/api/auth/me", headers=h).json()

    with patch(
        "app.agent.nodes.memory_save.generate_session_title",
        return_value="渠道GMV",
    ):
        memory_save(
            {
                "session_id": sid,
                "user_id": me["id"],
                "question": "各渠道 GMV 对比",
                "intent": "channel_analysis",
                "slots": {"metrics": ["gmv"], "filters": {}, "group_by": ["channel"]},
                "generated_sql": "SELECT 1",
                "answer": "渠道 A 领先",
                "need_clarification": False,
            }
        )

    sessions = client.get("/api/sessions", headers=h).json()["sessions"]
    mine = next(s for s in sessions if s["id"] == sid)
    assert mine["title"] == "渠道GMV"
