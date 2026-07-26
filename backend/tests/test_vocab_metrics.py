import pytest

from app.agent.metrics import (
    KnowledgeConfigError,
    get_metric_spec,
    is_known_metric,
    load_metric_specs,
)
from app.agent.vocab import DIMENSION_VOCAB, INTENTS, METRIC_VOCAB, TIME_RANGE_VOCAB


def test_vocab_covers_docs_keys():
    assert "channel_analysis" in INTENTS
    assert "unknown" in INTENTS
    assert "gmv" in METRIC_VOCAB
    assert "order_count" in METRIC_VOCAB
    assert "channel" in DIMENSION_VOCAB
    assert "last_30d" in TIME_RANGE_VOCAB


def test_metric_specs_align_with_vocab():
    for key in METRIC_VOCAB:
        spec = get_metric_spec(key)
        assert spec is not None, key
        assert "expression" in spec
        assert "orders" in spec["tables"] or "traffic_logs" in spec["tables"] or "payments" in spec["tables"] or "order_items" in spec["tables"]
    assert not is_known_metric("not_a_metric")
    assert get_metric_spec("not_a_metric") is None


def test_gmv_expression():
    spec = get_metric_spec("gmv")
    assert "pay_amount" in spec["expression"]
    assert "orders" in spec["tables"]


def test_metric_specs_loaded_from_yaml():
    specs = load_metric_specs()
    assert specs["gmv"]["display_name"] == "销售额"
    assert "销售额" in specs["gmv"]["aliases"]


def test_invalid_metric_yaml_rejected(tmp_path, monkeypatch):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "metrics.yaml").write_text(
        "version: '1'\nmetrics:\n  broken:\n    expression: sum(x)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_KNOWLEDGE_DIR", str(knowledge_dir))
    from app.agent.knowledge.loader import clear_cache

    clear_cache()
    with pytest.raises(KnowledgeConfigError):
        load_metric_specs()
    monkeypatch.delenv("APP_KNOWLEDGE_DIR")
    clear_cache()
