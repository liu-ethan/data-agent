from app.agent.state import AgentState


def test_agent_state_accepts_phase3_fields():
    state: AgentState = {
        "question": "q",
        "session_id": "default",
        "user_id": "1",
        "user_role": "analyst",
        "request_id": "req_1",
        "trace_id": "req_1",
        "intent": "channel_analysis",
        "route_mode": "react",
        "route_source": "model",
        "slots": {"metrics": ["gmv"], "time_range": "last_month"},
        "session_slots": None,
        "user_preferences": {"default_time_range": "last_month"},
        "recent_summaries": [],
        "react_messages": [],
        "react_step": 0,
        "need_clarification": False,
        "relevant_tables": ["orders"],
        "metric_specs": [],
        "repaired": False,
        "chart": {"type": "bar", "x": "channel", "y": "gmv", "title": "t"},
        "is_write": False,
        "affected_rows": None,
    }
    assert state["route_mode"] == "react"
    assert state["slots"]["metrics"] == ["gmv"]
    assert state["react_step"] == 0
    assert state["chart"]["type"] == "bar"
    assert state["is_write"] is False
    assert state["affected_rows"] is None
