from app.agent.nodes.complexity_router import decide_route


def test_force_react_single_metric_topn():
    mode, src = decide_route(
        "各渠道 GMV Top5",
        {"metrics": ["gmv"], "time_range": "last_30d", "group_by": ["channel"], "top_n": 5},
        "coordinator",
    )
    assert mode == "react"
    assert src == "rule_override"


def test_force_coordinator_multi_metric():
    mode, src = decide_route(
        "对比 GMV 和订单量",
        {"metrics": ["gmv", "order_count"], "time_range": "last_30d", "group_by": []},
        "react",
    )
    assert mode == "coordinator"
    assert src == "rule_override"


def test_force_coordinator_keywords():
    mode, src = decide_route(
        "请做渠道归因分析",
        {"metrics": ["gmv"], "time_range": "last_30d", "group_by": ["channel"]},
        "react",
    )
    assert mode == "coordinator"
    assert src == "rule_override"


def test_keep_model_when_ambiguous():
    mode, src = decide_route(
        "帮我看看数据",
        {"metrics": [], "time_range": None, "group_by": []},
        "coordinator",
    )
    assert mode == "coordinator"
    assert src == "model"
