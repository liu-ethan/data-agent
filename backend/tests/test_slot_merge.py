# backend/tests/test_slot_merge.py
from app.agent.memory.merge import merge_slots


def test_empty_curr_inherits_prev():
    prev = {
        "metrics": ["gmv"],
        "time_range": "last_30d",
        "group_by": ["channel"],
        "top_n": 5,
        "filters": {"province": "华东"},
        "write_intent": False,
    }
    curr = {
        "metrics": [],
        "time_range": None,
        "group_by": ["city"],
        "top_n": None,
        "filters": None,
        "write_intent": False,
    }
    out = merge_slots(prev, curr)
    assert out["metrics"] == ["gmv"]
    assert out["time_range"] == "last_30d"
    assert out["group_by"] == ["city"]
    assert out["top_n"] == 5
    assert out["filters"] == {"province": "华东"}


def test_nonempty_curr_overrides():
    prev = {"metrics": ["gmv"], "time_range": "last_30d", "group_by": ["channel"]}
    curr = {"metrics": ["order_count"], "time_range": "last_7d", "group_by": ["city"]}
    out = merge_slots(prev, curr)
    assert out["metrics"] == ["order_count"]
    assert out["time_range"] == "last_7d"
    assert out["group_by"] == ["city"]


def test_preferences_default_time_range_when_no_prev():
    out = merge_slots(
        None,
        {"metrics": ["gmv"], "time_range": None, "group_by": []},
        {"default_time_range": "last_30d"},
    )
    assert out["time_range"] == "last_30d"


def test_slot_merge_node_uses_session_slots(tmp_db_path):
    from app.agent.nodes.slot_merge import slot_merge

    out = slot_merge(
        {
            "session_slots": {
                "metrics": ["gmv"],
                "time_range": "last_30d",
                "group_by": ["channel"],
            },
            "slots": {
                "metrics": [],
                "time_range": None,
                "group_by": ["city"],
            },
            "user_preferences": {},
        }
    )
    assert out["slots"]["metrics"] == ["gmv"]
    assert out["slots"]["group_by"] == ["city"]


def test_slot_merge_node_strips_session_metadata():
    from app.agent.nodes.slot_merge import slot_merge

    out = slot_merge(
        {
            "session_slots": {
                "metrics": ["gmv"],
                "time_range": "last_30d",
                "group_by": ["channel"],
                "last_sql": "SELECT 1",
                "last_question": "prior question",
                "last_intent": "query",
                "last_result_summary": "ok",
            },
            "slots": {
                "metrics": [],
                "time_range": None,
                "group_by": ["city"],
            },
            "user_preferences": {},
        }
    )
    merged = out["slots"]
    assert "last_sql" not in merged
    assert "last_question" not in merged
    assert "last_intent" not in merged
    assert "last_result_summary" not in merged
    assert merged["metrics"] == ["gmv"]
    assert merged["group_by"] == ["city"]
