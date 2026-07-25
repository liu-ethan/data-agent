import json
from unittest.mock import patch

from app.agent.nodes.intent_analyzer import build_intent_prompt, intent_analyzer


def test_prompt_has_no_full_schema():
    messages = build_intent_prompt("上个月 GMV 最高的渠道？")
    blob = json.dumps(messages, ensure_ascii=False)
    assert "id_card" not in blob
    assert "CREATE TABLE" not in blob
    assert "order_items" not in blob  # 业务表名不应整表灌入 Intent
    assert "METRIC" in blob.upper() or "gmv" in blob
    assert "channel_analysis" in blob


def test_prompt_includes_optional_memory_context():
    messages = build_intent_prompt(
        "那按城市拆一下",
        session_slots={
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
        },
        preferences={"preferred_dimensions": ["channel"]},
        recent_summaries=[{"question_summary": "最近30天各渠道GMV"}],
    )

    user_content = messages[-1]["content"]
    assert user_content.startswith("那按城市拆一下")
    assert "gmv" in user_content
    assert "last_30d" in user_content
    assert "preferred_dimensions" in user_content
    assert "最近30天各渠道GMV" in user_content
    assert "CREATE TABLE" not in user_content


def test_prompt_without_memory_keeps_question_unchanged():
    messages = build_intent_prompt("上个月 GMV 最高的渠道？")

    assert messages[-1] == {
        "role": "user",
        "content": "上个月 GMV 最高的渠道？",
    }


def test_prompt_truncates_each_memory_context_line():
    messages = build_intent_prompt(
        "继续",
        session_slots={"last_result_summary": "s" * 1000},
        preferences={"note": "p" * 1000},
        recent_summaries=[{"answer_summary": "r" * 1000}],
    )

    context_lines = messages[-1]["content"].splitlines()[-3:]
    assert len(context_lines) == 3
    assert all(len(line) < 400 for line in context_lines)


def test_intent_parses_llm_json():
    payload = {
        "intent": "channel_analysis",
        "confidence": 0.9,
        "summary": "渠道 GMV Top",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "top_n": 5,
            "write_intent": False,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        out = intent_analyzer({"question": "上个月 GMV 最高的 5 个渠道是什么？"})
    assert out["intent"] == "channel_analysis"
    assert out["route_mode"] == "react"
    assert out["slots"]["metrics"] == ["gmv"]
    assert out["intent_confidence"] == 0.9


def test_intent_analyzer_passes_state_memory_to_prompt():
    payload = {
        "intent": "sales_analysis",
        "confidence": 0.8,
        "summary": "follow-up",
        "route_mode": "react",
        "slots": {},
        "need_clarification": False,
        "clarification_question": None,
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(payload),
    ) as completion:
        intent_analyzer(
            {
                "question": "继续",
                "session_slots": {"metrics": ["gmv"]},
                "user_preferences": {"default_time_range": "last_30d"},
                "recent_summaries": [{"answer_summary": "渠道 A 最高"}],
            }
        )

    user_content = completion.call_args.args[0][-1]["content"]
    assert "gmv" in user_content
    assert "last_30d" in user_content
    assert "渠道 A 最高" in user_content


def test_intent_bad_json_fallback():
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value="not-json",
    ):
        out = intent_analyzer({"question": "随便问问"})
    assert out["intent"] == "unknown"
    assert out["route_mode"] == "react"
