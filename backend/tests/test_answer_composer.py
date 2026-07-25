from app.agent.answer_composer import compose_answer


def test_write_success_message():
    text = compose_answer(
        "更新预算",
        [],
        [],
        is_write=True,
        affected_rows=3,
    )
    assert "写操作" in text
    assert "3" in text


def test_read_fallback_without_llm(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("no llm")

    monkeypatch.setattr(
        "app.agent.answer_composer.chat_completion", boom
    )
    text = compose_answer("q", ["n"], [{"n": 1}])
    assert "1" in text
