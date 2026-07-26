from unittest.mock import patch

import pytest

from app.agent.memory.title import generate_session_title
from app.db.init_db import init_database


@pytest.fixture
def memory_user_id(tmp_db_path):
    init_database(reset=True)
    return "1"


def test_generate_session_title_clips_llm_output():
    with patch(
        "app.agent.memory.title.chat_completion",
        return_value="这是一个远远超过十个字的很长标题内容",
    ):
        title = generate_session_title("上个月各渠道 GMV", "渠道 A 领先")
    assert len(title) <= 10
    assert title


def test_generate_session_title_falls_back_on_llm_error():
    with patch(
        "app.agent.memory.title.chat_completion",
        side_effect=RuntimeError("llm down"),
    ):
        title = generate_session_title("各渠道GMV对比如何", None)
    assert title == "各渠道GMV对比如何"[:10]


def test_memory_save_returns_session_title(memory_user_id):
    from app.agent.memory.store import create_session, get_session_title
    from app.agent.nodes.memory_save import memory_save

    sess = create_session(memory_user_id)
    with patch(
        "app.agent.nodes.memory_save.generate_session_title",
        return_value="渠道GMV",
    ) as gen:
        out = memory_save(
            {
                "session_id": sess["id"],
                "user_id": memory_user_id,
                "question": "各渠道 GMV 对比",
                "intent": "channel_analysis",
                "slots": {
                    "metrics": ["gmv"],
                    "filters": {},
                    "group_by": ["channel"],
                },
                "generated_sql": "SELECT 1",
                "answer": "渠道 A 领先",
                "need_clarification": False,
            }
        )
    gen.assert_called_once()
    assert out.get("session_title") == "渠道GMV"
    assert get_session_title(sess["id"], memory_user_id) == "渠道GMV"

    with patch(
        "app.agent.nodes.memory_save.generate_session_title",
        return_value="不应再调用",
    ) as gen2:
        out2 = memory_save(
            {
                "session_id": sess["id"],
                "user_id": memory_user_id,
                "question": "再问一次",
                "intent": "channel_analysis",
                "slots": {"metrics": ["gmv"], "filters": {}, "group_by": []},
                "answer": "ok",
                "need_clarification": False,
            }
        )
    gen2.assert_not_called()
    assert "session_title" not in out2


def test_iter_pipeline_events_includes_session_title():
    from langgraph.graph import END, START, StateGraph

    from app.agent.pipeline import iter_pipeline_events
    from app.agent.state import AgentState

    state = {
        "request_id": "req_t",
        "trace_id": "tr_t",
        "session_id": "sess_t",
        "user_id": "u_t",
        "user_role": "analyst",
        "question": "各渠道 GMV",
    }

    graph = StateGraph(AgentState)
    graph.add_node(
        "MemorySave", lambda _state: {"session_title": "渠道GMV"}
    )
    graph.add_edge(START, "MemorySave")
    graph.add_edge("MemorySave", END)

    with patch("app.agent.pipeline.build_graph", return_value=graph.compile()), patch(
        "app.agent.memory.store.patch_latest_turn_display"
    ):
        events = list(iter_pipeline_events(state))

    assert any(
        e[0] == "session_title"
        and e[1].get("title") == "渠道GMV"
        and e[1].get("session_id") == "sess_t"
        for e in events
    )


def test_agent_state_stream_updates_keep_session_title():
    from langgraph.graph import END, START, StateGraph

    from app.agent.state import AgentState

    graph = StateGraph(AgentState)
    graph.add_node("MemorySave", lambda _state: {"session_title": "渠道GMV"})
    graph.add_edge(START, "MemorySave")
    graph.add_edge("MemorySave", END)

    updates = list(graph.compile().stream({}, stream_mode="updates"))

    assert updates == [{"MemorySave": {"session_title": "渠道GMV"}}]
