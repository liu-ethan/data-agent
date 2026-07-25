from pathlib import Path

from app.config import REPO_ROOT
from app.tools.audit import append_audit, audit_log_path
from app.tools.registry import ToolRegistry, get_registry
from app.tools.schemas import ToolContext, ToolResult, ToolSpec


def test_audit_log_path_is_repo_logs():
    assert audit_log_path() == REPO_ROOT / "logs" / "audit.jsonl"


def test_append_audit_writes_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.tools.audit.audit_log_path", lambda: path)
    append_audit({"event": "tool_end", "tool": "validate_sql", "status": "ok"})
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "validate_sql" in lines[0]


def test_registry_deny_does_not_run_handler(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.tools.audit.audit_log_path", lambda: path)
    reg = ToolRegistry()
    called = {"n": 0}

    def handler(args, ctx):
        called["n"] += 1
        return ToolResult(ok=True, data={})

    reg.register(
        ToolSpec(
            name="blocked",
            description="x",
            risk_level="high",
            permission_policy="deny",
        ),
        handler,
    )
    ctx = ToolContext(
        request_id="r",
        trace_id="t",
        session_id="s",
        user_id="u",
        user_role="analyst",
        node="Test",
    )
    result = reg.invoke("blocked", {}, context=ctx)
    assert result.ok is False
    assert called["n"] == 0
    assert any(e["event"] == "tool_end" or e["event"] == "permission_deny" for e in result.events) or result.error
    assert path.exists()


def test_registry_invoke_allow_runs_handler_and_emits_tool_events(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.tools.audit.audit_log_path", lambda: path)
    reg = ToolRegistry()

    def handler(args, ctx):
        return ToolResult(ok=True, data={"v": args.get("x")})

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
        request_id="r",
        trace_id="t",
        session_id="s",
        user_id="u",
        user_role="analyst",
        node="Test",
    )
    result = reg.invoke("echo", {"x": 1}, context=ctx)
    assert result.ok is True
    assert result.data == {"v": 1}
    names = [e["event"] for e in result.events]
    assert names[0] == "tool_start"
    assert names[-1] == "tool_end"
