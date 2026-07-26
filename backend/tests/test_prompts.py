from pathlib import Path

import pytest
import yaml


@pytest.fixture()
def prompts_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PROMPTS_DIR", str(tmp_path))
    from app import prompts

    prompts.clear_cache()
    yield tmp_path
    prompts.clear_cache()


def _write(dir_path: Path, name: str, system: str, user: str):
    (dir_path / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {"version": "1", "description": "t", "system": system, "user": user},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_render_injects_variables(prompts_dir):
    _write(
        prompts_dir,
        "demo",
        "Hello {name}. Use {{literal}} braces.",
        "Q: {question}",
    )
    from app.prompts import render

    out = render("demo", name="Ada", question="GMV?")
    assert out["system"] == "Hello Ada. Use {literal} braces."
    assert out["user"] == "Q: GMV?"


def test_render_missing_file(prompts_dir):
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="not found"):
        render("nope")


def test_render_missing_placeholder(prompts_dir):
    _write(prompts_dir, "demo", "Hi {name}", "Q")
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="Missing"):
        render("demo")


def test_render_extra_kwargs(prompts_dir):
    _write(prompts_dir, "demo", "Hi {name}", "Q")
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="Unexpected"):
        render("demo", name="Ada", unused=1)


def test_render_requires_system_and_user(prompts_dir):
    (prompts_dir / "bad.yaml").write_text(
        yaml.safe_dump({"system": "only"}, allow_unicode=True),
        encoding="utf-8",
    )
    from app.prompts import PromptRenderError, render

    with pytest.raises(PromptRenderError, match="user"):
        render("bad")


def test_bundled_prompts_render_with_required_vars():
    from app.prompts import clear_cache, render

    clear_cache()
    intent = render(
        "intent_analyzer",
        intent_list="x",
        metrics="gmv",
        dimensions="channel",
        time_ranges="last_month",
        question="q",
        context_block="",
    )
    assert "只输出 JSON" in intent["system"] or "JSON" in intent["system"]
    assert "q" in intent["user"]

    react = render("react_agent", slots_json="{}", question="q")
    assert "propose_sql" in react["system"]

    sql = render(
        "sql_generator",
        schema_json="[]",
        metric_specs_json="[]",
        slots_json="{}",
        question="q",
    )
    assert "SQL" in sql["system"]

    repair = render(
        "sql_repairer",
        question="q",
        sql="select 1",
        error="e",
        schema_json="{}",
    )
    assert "SQL" in repair["system"]

    chart = render("chart_planner", payload_json="{}")
    assert "type" in chart["system"]

    ans = render("answer_composer", question="q", result_json="{}")
    assert "中文" in ans["system"] or "不得编造" in ans["system"]

    title = render("session_title", max_chars=10, question="q", summary="s")
    assert "10" in title["system"]
