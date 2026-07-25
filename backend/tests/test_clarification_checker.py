from app.agent.nodes.clarification_checker import clarification_checker


def test_vague_best_needs_clarification():
    out = clarification_checker({
        "question": "最近哪个渠道表现最好？",
        "slots": {"metrics": [], "time_range": None, "group_by": ["channel"]},
        "need_clarification": False,
        "clarification_question": None,
    })
    assert out["need_clarification"] is True
    assert out["clarification_question"]
    assert "指标" in out["clarification_question"] or "GMV" in out["clarification_question"]


def test_clear_gmv_no_clarification():
    out = clarification_checker({
        "question": "上个月 GMV 最高的 5 个渠道是什么？",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "top_n": 5,
        },
        "need_clarification": False,
        "clarification_question": None,
    })
    assert out["need_clarification"] is False


def test_unknown_metric_needs_clarification():
    out = clarification_checker({
        "question": "看看用户质量",
        "slots": {"metrics": ["user_quality"], "time_range": "last_30d"},
        "need_clarification": False,
    })
    assert out["need_clarification"] is True
