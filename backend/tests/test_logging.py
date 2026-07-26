import json
import re
from unittest.mock import MagicMock, patch

from app.config import get_settings
from app.log.logging import (
    format_log_line,
    get_request_id,
    log_event,
    resolve_app_log_path,
    set_request_id,
)

_LINE_RE = re.compile(
    r"^(?P<level>[A-Z]+) (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) :     (?P<msg>.+)$"
)


def _parse_line(line: str) -> dict:
    m = _LINE_RE.match(line.strip())
    assert m, f"bad log line: {line!r}"
    return m.groupdict()


def test_format_log_line_matches_requested_style():
    line = format_log_line(
        "INFO",
        "Waiting for application startup.",
        ts="2026-07-26 11:01:00",
    )
    assert line == "INFO 2026-07-26 11:01:00 :     Waiting for application startup."


def test_log_event_expands_newlines_in_detail(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "app.log.logging._today_str", lambda: "2026-07-26"
    )
    set_request_id("req_nl")
    sql = "WITH t AS (\n  SELECT 1\n)\nSELECT * FROM t;"
    log_event(
        "INFO",
        "tool_end",
        tool="execute_sql",
        detail={"args": {"sql": sql}, "ok": True},
    )
    out = capsys.readouterr().out
    file_text = (tmp_path / "2026-07-26.log").read_text(encoding="utf-8")
    for text in (out, file_text):
        assert "tool_end" in text
        assert "WITH t AS (" in text
        assert "\n  SELECT 1\n" in text
        assert "SELECT * FROM t;" in text
        # Should not keep the whole SQL as a single escaped \\n blob
        assert "WITH t AS (\\n  SELECT 1\\n)" not in text


def test_log_event_emits_text_line(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    set_request_id("req_test_1")
    log_event("INFO", "schema_served", detail={"tables": 8})
    line = capsys.readouterr().out.strip().splitlines()[-1]
    parsed = _parse_line(line)
    assert parsed["level"] == "INFO"
    assert "schema_served" in parsed["msg"]
    assert "request_id=req_test_1" in parsed["msg"]
    assert "tables" in parsed["msg"]
    assert get_request_id() == "req_test_1"


def test_log_event_also_writes_daily_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "app.log.logging._today_str", lambda: "2026-07-26"
    )
    set_request_id("req_file_1")
    log_event("INFO", "request_start", path="/api/chat", method="POST")

    log_file = tmp_path / "2026-07-26.log"
    assert log_file.is_file()
    parsed = _parse_line(log_file.read_text(encoding="utf-8").strip())
    assert parsed["level"] == "INFO"
    assert "request_start" in parsed["msg"]
    assert "request_id=req_file_1" in parsed["msg"]
    assert "path=/api/chat" in parsed["msg"]


def test_resolve_app_log_path_uses_suffix_when_base_full(tmp_path, tmp_db_path):
    max_bytes = get_settings().logging_max_bytes
    base = tmp_path / "2026-07-26.log"
    base.write_bytes(b"x" * max_bytes)
    path = resolve_app_log_path(day="2026-07-26", log_dir=tmp_path)
    assert path == tmp_path / "2026-07-26_1.log"


def test_log_event_rotates_when_file_exceeds_10m(tmp_path, monkeypatch, tmp_db_path):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "app.log.logging._today_str", lambda: "2026-07-26"
    )
    max_bytes = get_settings().logging_max_bytes
    base = tmp_path / "2026-07-26.log"
    base.write_bytes(b"x" * max_bytes)

    log_event("INFO", "request_end", status=200, latency_ms=3)

    rotated = tmp_path / "2026-07-26_1.log"
    assert rotated.is_file()
    parsed = _parse_line(rotated.read_text(encoding="utf-8").strip())
    assert "request_end" in parsed["msg"]
    assert "status=200" in parsed["msg"]


def test_resolve_uses_next_index_when_suffix_full(tmp_path, tmp_db_path):
    max_bytes = get_settings().logging_max_bytes
    (tmp_path / "2026-07-26.log").write_bytes(b"x" * max_bytes)
    (tmp_path / "2026-07-26_1.log").write_bytes(b"x" * max_bytes)
    path = resolve_app_log_path(day="2026-07-26", log_dir=tmp_path)
    assert path == tmp_path / "2026-07-26_2.log"


def test_pipeline_logs_node_start_and_end(tmp_path, monkeypatch):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "app.log.logging._today_str", lambda: "2026-07-26"
    )

    from app.agent.pipeline import iter_pipeline_events

    state = {
        "request_id": "req_n1",
        "trace_id": "tr_n1",
        "session_id": "s1",
        "user_id": "u1",
        "user_role": "analyst",
        "question": "hello",
    }

    class _FakeGraph:
        def stream(self, _merged, stream_mode="updates"):
            yield {"IntentAnalyzer": {"intent": "metric_query"}}

    with patch("app.agent.pipeline.build_graph", return_value=_FakeGraph()):
        events = list(iter_pipeline_events(state))

    assert ("node_start", {"node": "IntentAnalyzer"}) in events
    text = (tmp_path / "2026-07-26.log").read_text(encoding="utf-8")
    msgs = [_parse_line(line)["msg"] for line in text.splitlines() if line.strip()]
    assert any(m.startswith("run_start") for m in msgs)
    assert any("node_start" in m and "node=IntentAnalyzer" in m for m in msgs)
    assert any("node_end" in m and "node=IntentAnalyzer" in m for m in msgs)
    assert any("request_id=req_n1" in m and "trace_id=tr_n1" in m for m in msgs)


def test_chat_completion_logs_full_prompt_io(tmp_path, monkeypatch):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "app.log.logging._today_str", lambda: "2026-07-26"
    )

    messages = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "ping"},
    ]
    fake_msg = MagicMock()
    fake_msg.content = "pong"
    fake_choice = MagicMock()
    fake_choice.message = fake_msg
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with (
        patch("app.agent.llm.get_settings") as gs,
        patch("app.agent.llm.OpenAI", return_value=fake_client),
    ):
        gs.return_value = MagicMock(
            openai_api_key="sk-test",
            openai_base_url="",
            openai_model="gpt-test",
        )
        from app.agent.llm import chat_completion

        out = chat_completion(messages)

    assert out == "pong"
    text = (tmp_path / "2026-07-26.log").read_text(encoding="utf-8")
    assert "prompt_input" in text
    assert "you are helpful" in text
    assert '"content": "ping"' in text or "'content': 'ping'" in text or "ping" in text
    assert "prompt_output" in text
    assert "pong" in text


def test_chat_completion_with_tools_logs_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "app.log.logging._today_str", lambda: "2026-07-26"
    )

    messages = [{"role": "user", "content": "query gmv"}]
    tools = [
        {
            "type": "function",
            "function": {"name": "execute_sql", "parameters": {"type": "object"}},
        }
    ]
    tool_call = MagicMock()
    tool_call.id = "call_1"
    tool_call.function.name = "execute_sql"
    tool_call.function.arguments = json.dumps({"sql": "SELECT 1"})
    fake_msg = MagicMock()
    fake_msg.content = None
    fake_msg.tool_calls = [tool_call]
    fake_choice = MagicMock()
    fake_choice.message = fake_msg
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with (
        patch("app.agent.llm.get_settings") as gs,
        patch("app.agent.llm.OpenAI", return_value=fake_client),
    ):
        gs.return_value = MagicMock(
            openai_api_key="sk-test",
            openai_base_url="",
            openai_model="gpt-test",
        )
        from app.agent.llm import chat_completion_with_tools

        out = chat_completion_with_tools(messages, tools)

    assert out["tool_calls"][0]["name"] == "execute_sql"
    text = (tmp_path / "2026-07-26.log").read_text(encoding="utf-8")
    assert "prompt_input" in text
    assert "execute_sql" in text
    assert "SELECT 1" in text
    assert "tool_calls" in text


def test_registry_logs_full_tool_call(tmp_path, monkeypatch):
    monkeypatch.setattr("app.log.logging.app_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "app.log.logging._today_str", lambda: "2026-07-26"
    )
    monkeypatch.setattr(
        "app.tools.audit.audit_log_path", lambda: tmp_path / "audit.jsonl"
    )

    from app.tools.registry import ToolRegistry
    from app.tools.schemas import ToolContext, ToolResult, ToolSpec

    reg = ToolRegistry()

    def handler(args, ctx):
        return ToolResult(ok=True, data={"v": args.get("x"), "rows": [{"a": 1}]})

    reg.register(
        ToolSpec(
            name="echo",
            description="echo",
            risk_level="low",
            permission_policy="allow",
        ),
        handler,
    )
    ctx = ToolContext(
        request_id="r1",
        trace_id="t1",
        session_id="s1",
        user_id="u1",
        user_role="analyst",
        node="ReActTools",
    )
    result = reg.invoke("echo", {"x": 1}, context=ctx)
    assert result.ok is True

    text = (tmp_path / "2026-07-26.log").read_text(encoding="utf-8")
    assert "tool_start" in text
    assert "tool_end" in text
    assert "echo" in text
    assert '"x": 1' in text or "x=1" in text
    assert "rows" in text
