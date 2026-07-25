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


def test_intent_bad_json_fallback():
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value="not-json",
    ):
        out = intent_analyzer({"question": "随便问问"})
    assert out["intent"] == "unknown"
    assert out["route_mode"] == "react"
