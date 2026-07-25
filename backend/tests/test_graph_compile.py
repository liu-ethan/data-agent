from app.agent.graph import build_graph


def test_graph_compiles():
    g = build_graph()
    assert g is not None


def test_complexity_router_defaults():
    from app.agent.nodes.complexity_router import complexity_router

    out = complexity_router({"question": "", "slots": None, "route_mode": None})
    assert out["route_mode"] == "react"
    assert out["route_source"] == "model"


def test_clarification_reply_sets_answer():
    from app.agent.nodes.clarification_reply import clarification_reply
    out = clarification_reply({"clarification_question": "请说明指标"})
    assert out["answer"] == "请说明指标"
