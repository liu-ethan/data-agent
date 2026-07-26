import json
from datetime import datetime

import pytest

from app.agent.memory import store
from app.agent.memory.store import (
    MemoryError,
    create_session,
    delete_session,
    get_session_title,
    list_sessions,
    list_turns,
    save_turn,
    set_session_title_if_empty,
)
from app.auth.passwords import hash_password
from app.db.database import get_connection
from app.db.init_db import init_database


@pytest.fixture
def memory_user_id(tmp_db_path):
    init_database(reset=True)
    return "1"


@pytest.fixture
def other_user_id(memory_user_id):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_users (id, username, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                2,
                "other_analyst",
                hash_password("other1234"),
                "analyst",
                datetime.now().isoformat(sep=" ", timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return "2"


def test_ensure_session_and_isolation(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    with pytest.raises(store.MemoryError):
        store.ensure_session("s1", "2")


def test_save_turn_and_load_slots(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    store.save_turn(
        session_id="s1",
        user_id="1",
        question="各渠道 GMV",
        intent="channel_analysis",
        sql_text="SELECT 1",
        slots={
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "filters": {},
        },
        result_summary="ok",
    )
    slots = store.load_last_turn_slots("s1", "1")
    assert slots is not None
    assert slots["metrics"] == ["gmv"]
    assert slots["time_range"] == "last_30d"
    assert slots["group_by"] == ["channel"]


def test_turn_cap_keeps_latest_n(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    for i in range(12):
        store.save_turn(
            session_id="s1",
            user_id="1",
            question=f"q{i}",
            intent="sales_analysis",
            sql_text=None,
            slots={"metrics": ["gmv"], "time_range": "last_7d", "group_by": []},
            result_summary=f"r{i}",
        )
    from app.db.database import get_connection

    with get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM session_turns WHERE session_id = ?",
            ("s1",),
        ).fetchone()["c"]
    assert n == store.MAX_TURNS_PER_SESSION


def test_preferences_and_summaries(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    store.update_preferences_from_slots(
        "1",
        {"time_range": "last_30d", "group_by": ["channel"]},
    )
    prefs = store.load_preferences("1")
    assert prefs.get("default_time_range") == "last_30d"
    assert "channel" in (prefs.get("preferred_dimensions") or [])
    store.append_summary(
        user_id="1",
        session_id="s1",
        question_summary="各渠道 GMV",
        answer_summary="渠道 A 最高",
        metrics=["gmv"],
        filters={},
    )
    rows = store.load_recent_summaries("1", limit=5)
    assert len(rows) == 1
    assert rows[0]["question_summary"] == "各渠道 GMV"


def test_memory_load_returns_session_and_user_memory(tmp_db_path):
    from app.agent.nodes.memory_load import memory_load

    init_database(reset=True)
    store.ensure_session("s1", "1")
    store.save_turn(
        session_id="s1",
        user_id="1",
        question="各渠道 GMV",
        intent="channel_analysis",
        sql_text="SELECT 1",
        slots={
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "filters": {},
        },
        result_summary="ok",
    )
    store.update_preferences_from_slots(
        "1",
        {"time_range": "last_30d", "group_by": ["channel"]},
    )
    store.append_summary(
        user_id="1",
        session_id="s1",
        question_summary="各渠道 GMV",
        answer_summary="渠道 A 最高",
        metrics=["gmv"],
        filters={},
    )

    out = memory_load(
        {"session_id": "s1", "user_id": "1", "repaired": True}
    )

    assert out["session_slots"]["metrics"] == ["gmv"]
    assert out["user_preferences"]["default_time_range"] == "last_30d"
    assert len(out["recent_summaries"]) == 1
    assert out["react_step"] == 0
    assert out["repaired"] is True


def test_memory_load_returns_error_for_wrong_session_owner(tmp_db_path):
    from app.agent.nodes.memory_load import memory_load

    init_database(reset=True)
    store.ensure_session("s1", "1")

    out = memory_load({"session_id": "s1", "user_id": "2"})

    assert out == {"error": "Session belongs to another user"}


@pytest.mark.parametrize(
    ("state_overrides", "expected_summary"),
    [
        (
            {
                "need_clarification": True,
                "clarification_question": "请补充时间范围",
                "answer": "请补充时间范围",
            },
            "clarification: 请补充时间范围",
        ),
        (
            {"error": "SQL execution failed"},
            "error: SQL execution failed",
        ),
    ],
)
def test_memory_save_only_saves_turn_for_non_success_paths(
    monkeypatch, state_overrides, expected_summary
):
    from app.agent.nodes.memory_save import memory_save

    calls = []
    monkeypatch.setattr(
        store, "save_turn", lambda **kwargs: calls.append(("turn", kwargs))
    )
    monkeypatch.setattr(store, "get_session_title", lambda *args: "已有标题")
    monkeypatch.setattr(
        store,
        "update_preferences_from_slots",
        lambda *args, **kwargs: calls.append(("preferences", args, kwargs)),
    )
    monkeypatch.setattr(
        store,
        "append_summary",
        lambda **kwargs: calls.append(("summary", kwargs)),
    )
    state = {
        "session_id": "s1",
        "user_id": "1",
        "question": "各渠道 GMV",
        "intent": "channel_analysis",
        "slots": {"metrics": ["gmv"], "filters": {}},
        "generated_sql": None,
        **state_overrides,
    }

    assert memory_save(state) == {}

    assert [call[0] for call in calls] == ["turn"]
    assert calls[0][1]["result_summary"] == expected_summary


def test_memory_save_updates_long_term_memory_on_success(monkeypatch):
    from app.agent.nodes.memory_save import memory_save

    calls = []
    monkeypatch.setattr(
        store, "save_turn", lambda **kwargs: calls.append(("turn", kwargs))
    )
    monkeypatch.setattr(store, "get_session_title", lambda *args: "已有标题")
    monkeypatch.setattr(
        store,
        "update_preferences_from_slots",
        lambda *args: calls.append(("preferences", args)),
    )
    monkeypatch.setattr(
        store,
        "append_summary",
        lambda **kwargs: calls.append(("summary", kwargs)),
    )
    state = {
        "session_id": "s1",
        "user_id": "1",
        "question": "各渠道 GMV",
        "intent": "channel_analysis",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "filters": {},
        },
        "generated_sql": "SELECT 1",
        "answer": "渠道 A 最高",
        "need_clarification": False,
    }

    assert memory_save(state) == {}

    assert [call[0] for call in calls] == ["turn", "preferences", "summary"]
    assert calls[0][1]["result_summary"] == "渠道 A 最高"
    assert calls[2][1]["answer_summary"] == "渠道 A 最高"


def test_memory_save_ignores_session_ownership_error(monkeypatch):
    from app.agent.nodes.memory_save import memory_save

    def reject_turn(**kwargs):
        raise store.MemoryError("Session belongs to another user")

    monkeypatch.setattr(store, "save_turn", reject_turn)
    monkeypatch.setattr(store, "get_session_title", lambda *args: "已有标题")
    monkeypatch.setattr(
        store,
        "update_preferences_from_slots",
        lambda *args: pytest.fail("preferences must not be updated"),
    )
    monkeypatch.setattr(
        store,
        "append_summary",
        lambda **kwargs: pytest.fail("summary must not be appended"),
    )

    assert memory_save(
        {
            "session_id": "s1",
            "user_id": "2",
            "question": "q",
            "slots": {},
            "answer": "answer",
        }
    ) == {}


def test_save_turn_filters_json_redaction_and_round_trip(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    filters = {
        "phone": "13800138000",
        "nested": {"contact": "联系13800138000"},
        "amount": 13800138000,
    }
    store.save_turn(
        session_id="s1",
        user_id="1",
        question="q",
        intent="sales_analysis",
        sql_text=None,
        slots={
            "metrics": ["gmv"],
            "time_range": "last_7d",
            "group_by": [],
            "filters": filters,
        },
        result_summary="ok",
    )
    slots = store.load_last_turn_slots("s1", "1")
    assert slots is not None
    loaded = slots["filters"]
    assert loaded["phone"] == "[phone]"
    assert loaded["nested"]["contact"] == "联系[phone]"
    assert "13800138000" not in loaded["phone"]
    assert "13800138000" not in loaded["nested"]["contact"]
    assert loaded["amount"] == 13800138000
    from app.db.database import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT filters_json FROM session_turns WHERE session_id = ? ORDER BY turn_index DESC LIMIT 1",
            ("s1",),
        ).fetchone()
    assert json.loads(row["filters_json"]) == loaded


def test_create_and_list_sessions_ordered(memory_user_id):
    older = create_session(memory_user_id)
    newer = create_session(memory_user_id)
    sessions = list_sessions(memory_user_id)
    assert [s["id"] for s in sessions[:2]] == [newer["id"], older["id"]]
    assert sessions[0]["turn_count"] == 0
    assert sessions[0]["title"] is None


def test_list_turns_requires_owner(memory_user_id, other_user_id):
    sess = create_session(memory_user_id)
    save_turn(
        session_id=sess["id"],
        user_id=memory_user_id,
        question="q1",
        intent="sales_overview",
        sql_text="SELECT 1",
        slots={"metrics": ["gmv"], "filters": {}, "group_by": [], "time_range": None},
        result_summary="ok",
    )
    turns = list_turns(sess["id"], memory_user_id)
    assert len(turns) == 1
    assert turns[0]["question"] == "q1"
    assert turns[0]["sql_text"] == "SELECT 1"
    try:
        list_turns(sess["id"], other_user_id)
        assert False, "expected MemoryError"
    except MemoryError:
        pass


def test_save_turn_refreshes_session_updated_at(memory_user_id):
    sess = create_session(memory_user_id)
    before = sess["updated_at"]
    save_turn(
        session_id=sess["id"],
        user_id=memory_user_id,
        question="q",
        intent="sales_overview",
        sql_text=None,
        slots={"metrics": ["gmv"], "filters": {}, "group_by": [], "time_range": None},
        result_summary="ok",
    )
    after = next(s["updated_at"] for s in list_sessions(memory_user_id) if s["id"] == sess["id"])
    assert after >= before
    assert after != before


def test_set_session_title_if_empty(memory_user_id):
    sess = create_session(memory_user_id)
    long_title = "最近三十天各渠道 GMV 趋势如何变化以及同比环比情况请详细分析并给出 actionable 建议"
    assert len(long_title) > 10
    written = set_session_title_if_empty(sess["id"], memory_user_id, long_title)
    assert written is True
    listed = list_sessions(memory_user_id)
    title = next(s["title"] for s in listed if s["id"] == sess["id"])
    assert title is not None
    assert len(title) == 10
    written2 = set_session_title_if_empty(sess["id"], memory_user_id, "第二次不应覆盖")
    assert written2 is False
    listed2 = list_sessions(memory_user_id)
    assert next(s["title"] for s in listed2 if s["id"] == sess["id"]) == title


def test_set_session_title_if_empty_blank_title_returns_false(memory_user_id):
    sess = create_session(memory_user_id)
    assert set_session_title_if_empty(sess["id"], memory_user_id, "   ") is False
    assert get_session_title(sess["id"], memory_user_id) is None


def test_get_session_title_none_then_value(memory_user_id):
    sess = create_session(memory_user_id)
    assert get_session_title(sess["id"], memory_user_id) is None
    set_session_title_if_empty(sess["id"], memory_user_id, "渠道GMV")
    assert get_session_title(sess["id"], memory_user_id) == "渠道GMV"


def test_delete_session_removes_turns(memory_user_id, other_user_id):
    sess = create_session(memory_user_id)
    save_turn(
        session_id=sess["id"],
        user_id=memory_user_id,
        question="q",
        intent="x",
        sql_text=None,
        slots={"metrics": [], "filters": {}, "group_by": [], "time_range": None},
        result_summary="ok",
    )
    delete_session(sess["id"], memory_user_id)
    assert all(s["id"] != sess["id"] for s in list_sessions(memory_user_id))
    with pytest.raises(MemoryError):
        list_turns(sess["id"], memory_user_id)
    other = create_session(memory_user_id)
    with pytest.raises(MemoryError):
        delete_session(other["id"], other_user_id)


def test_strip_sensitive():
    from app.agent.memory.summarize import (
        build_result_summary,
        merge_preferences,
        strip_sensitive,
    )

    text = "用户张三手机13800138000邮箱a@b.com身份证110101199001011234"
    out = strip_sensitive(text)
    assert "13800138000" not in out
    assert "110101199001011234" not in out
    assert "a@b.com" not in out
    assert strip_sensitive("电话13800138000结束") == "电话[phone]结束"
    assert (
        strip_sensitive("身份证110101199001011234结束")
        == "身份证[id_card]结束"
    )
    assert build_result_summary(
        answer="ignored",
        error=None,
        clarification="请联系 13800138000",
    ) == "clarification: 请联系 [phone]"
    assert merge_preferences(
        {"preferred_dimensions": ["province"]},
        {"time_range": "last_30d", "group_by": ["channel", "province"]},
    ) == {
        "default_time_range": "last_30d",
        "preferred_dimensions": ["province", "channel"],
    }
