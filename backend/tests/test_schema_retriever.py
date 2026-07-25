from app.agent.nodes.schema_retriever import schema_retriever
from app.db.init_db import init_database


def test_channel_gmv_schema(tmp_db_path):
    init_database(reset=True)
    out = schema_retriever({
        "intent": "channel_analysis",
        "slots": {"metrics": ["gmv"], "group_by": ["channel"], "time_range": "last_month"},
        "user_role": "analyst",
    })
    assert "orders" in out["relevant_tables"]
    assert "app_users" not in out["relevant_tables"]
    assert any(s["key"] == "gmv" for s in out["metric_specs"])
    assert "pay_amount" in out["relevant_columns"]["orders"]
    assert "channel" in out["relevant_columns"]["orders"]


def test_analyst_hides_sensitive(tmp_db_path):
    init_database(reset=True)
    out = schema_retriever({
        "intent": "user_analysis",
        "slots": {"metrics": ["order_count"], "group_by": ["city"]},
        "user_role": "analyst",
    })
    if "users" in out["relevant_columns"]:
        for sens in ("name", "phone", "email", "id_card"):
            assert sens not in out["relevant_columns"]["users"]
