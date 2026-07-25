# Phase 4 Tool Registry / Sandbox / AuditLog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地轻量 Tool Registry、角色化 SQLGuardrail、SQL 沙箱与 `logs/audit.jsonl`，使默认 chat 的 SQL 路径经 Registry 执行，并推送 `tool_*` SSE。

**Architecture:** Guardrail / Sandbox 为确定性安全模块；5 个内置 Tool 挂到自研 Registry（Pre/Post → AuditLog）；Phase 3 图节点名不变，`SQLGuardrail`/`SQLExecutor` 只 `registry.invoke`；pipeline 投影 `tool_events` 为 SSE；前端 Trace 展示。

**Tech Stack:** Python 3.12（conda `python3.12`）· FastAPI · LangGraph · pytest · React · TypeScript

## Global Constraints

- 规格：`spec/2026-07-25-phase4-tool-registry-sandbox-audit-design.md`；产品：`docs/03`、`docs/04`、`docs/06` Phase 4
- 默认 chat SQL **必须**经 Guardrail 再进沙箱；禁止节点旁路直连 DB
- 不上 ReAct/Coordinator、Memory、SQLRepairer、主图 `render_chart`、前端画图、MCP/Manifest、admin NL→写 SQL
- 配置仅用根目录 `config.yaml`；禁止 `.env`
- **Python（强制）**：`/home/user/miniconda3/envs/python3.12/bin/python` 与同目录 `pip`；下文记为 `PY` / `PIP`
- **禁止** git worktree / `.worktrees/` 做功能开发；只在本仓库工作区改代码
- **Git commit：仅当用户明确要求时执行**；本计划 Commit 步骤默认跳过
- TDD：Guardrail / Sandbox / Registry / Tools / graph·chat 先写失败测试再实现

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/app/security/sql_guardrail.py` | 角色化读写校验 |
| `backend/app/security/sql_sandbox.py` | 分角色连接、LIMIT、写上限、超时 |
| `backend/app/tools/schemas.py` | ToolSpec / ToolContext / ToolResult |
| `backend/app/tools/audit.py` | `logs/audit.jsonl` 追加 + 脱敏 |
| `backend/app/tools/registry.py` | register / invoke + Pre/Post |
| `backend/app/tools/builtins.py` | 5 个内置 Tool + `ensure_builtins_registered` |
| `backend/app/tools/__init__.py` | 导出 get_registry |
| `backend/app/agent/nodes/sql_guardrail_node.py` | invoke validate_sql |
| `backend/app/agent/nodes/sql_executor_node.py` | invoke execute_sql |
| `backend/app/agent/pipeline.py` | 投影 tool_* SSE |
| `backend/app/agent/sql_executor.py` | 删除公开旁路（或薄委托仅供非 chat 测试，节点不得 import） |
| `backend/app/agent/state.py` | 可选 `tool_events` 字段 |
| `frontend/src/pages/AppWorkbench.tsx` | Trace 展示 tool_* |
| `.gitignore` | `logs/` |
| `README.md` | Phase 1–4 状态 |
| `backend/tests/test_*.py` | 见各 Task |

---

### Task 1: 升级 SQLGuardrail（admin 受控写）

**Files:**
- Modify: `backend/app/security/sql_guardrail.py`
- Modify: `backend/tests/test_sql_guardrail.py`
- Test: `backend/tests/test_sql_guardrail.py`

**Interfaces:**
- Consumes: 现有 `check_sql(sql, *, user_role) -> GuardrailResult`
- Produces: 同签名；analyst 只读；admin 允许以 `INSERT|UPDATE|DELETE` 开头的单语句（业务表）；两边仍禁 DDL / 多语句 / app 表 / `REPLACE` / `PRAGMA`

- [ ] **Step 1: 改写失败测试（替换「admin 也拒写」断言）**

在 `test_sql_guardrail.py`：

1. 将 `test_rejects_forbidden_statement_types` 的参数列表改为仅 DDL/危险类（去掉 INSERT/UPDATE/DELETE）：

```python
@pytest.mark.parametrize(
    "keyword",
    [
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "ATTACH",
        "DETACH",
        "REPLACE",
        "PRAGMA",
    ],
)
def test_rejects_forbidden_statement_types(keyword):
    sql = f"{keyword} TABLE orders" if keyword != "PRAGMA" else "PRAGMA table_info(orders)"
    assert not check_sql(sql, user_role="admin").ok
```

2. 新增：

```python
def test_analyst_rejects_writes():
    assert not check_sql(
        "UPDATE campaigns SET budget = 1 WHERE id = 1",
        user_role="analyst",
    ).ok
    assert not check_sql(
        "INSERT INTO campaigns (id, name) VALUES (999, 'x')",
        user_role="analyst",
    ).ok
    assert not check_sql(
        "DELETE FROM campaigns WHERE id = 1",
        user_role="analyst",
    ).ok


def test_admin_allows_dml_on_business_tables():
    assert check_sql(
        "UPDATE campaigns SET budget = 1 WHERE id = 1",
        user_role="admin",
    ).ok
    assert check_sql(
        "INSERT INTO campaigns (id, name, channel, budget) VALUES (9999, 't', 'app', 1)",
        user_role="admin",
    ).ok
    assert check_sql(
        "DELETE FROM campaigns WHERE id = 9999",
        user_role="admin",
    ).ok


def test_admin_rejects_dml_on_app_tables():
    assert not check_sql(
        "DELETE FROM app_users WHERE id = 1",
        user_role="admin",
    ).ok
```

保留现有 analyst 敏感列 / app 表 / 多语句用例不变。

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && PY=/home/user/miniconda3/envs/python3.12/bin/python && $PY -m pytest tests/test_sql_guardrail.py::test_admin_allows_dml_on_business_tables tests/test_sql_guardrail.py::test_analyst_rejects_writes -v`  
Expected: FAIL（当前 admin 写被拒或新测试不存在）

- [ ] **Step 3: 实现角色化 `check_sql`**

核心改动思路（保持现有注释剥离 / 引号 / 表源 / 敏感列逻辑）：

```python
_DDL_OR_DANGEROUS = (
    "DROP", "ALTER", "TRUNCATE", "CREATE", "ATTACH", "DETACH", "REPLACE", "PRAGMA",
)
_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE")

def check_sql(sql: str, *, user_role: str) -> GuardrailResult:
    # ... empty / role / multi-statement 同现有 ...
    query_start = _LEADING_COMMENT_RE.sub("", normalized)
    if user_role == "analyst":
        if not re.match(r"(?:SELECT|WITH)\b", query_start):
            return _reject("Only SELECT or WITH queries are allowed")
        for keyword in _DDL_OR_DANGEROUS + _WRITE_KEYWORDS:
            if re.search(rf"\b{keyword}\b", normalized):
                return _reject(f"{keyword} is not allowed")
        # ... existing analyst sensitive checks ...
        return GuardrailResult(ok=True, reason=None)

    # admin
    if not re.match(r"(?:SELECT|WITH|INSERT|UPDATE|DELETE)\b", query_start):
        return _reject("Only SELECT/WITH/INSERT/UPDATE/DELETE are allowed")
    for keyword in _DDL_OR_DANGEROUS:
        if re.search(rf"\b{keyword}\b", normalized):
            return _reject(f"{keyword} is not allowed")
    # blocked tables on sources — 对 SELECT 用 FROM/JOIN；对写语句额外扫表名
    # 至少覆盖：_sources() + 对 INSERT INTO / UPDATE / DELETE FROM 的表名提取
    ...
    return GuardrailResult(ok=True, reason=None)
```

写语句表名提取（最小实现）：

```python
_WRITE_TABLE_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(" + _IDENTIFIER + r")",
    re.IGNORECASE,
)
```

将匹配表名并入 `blocked_tables` 检查（与 app 表 / sqlite_master 同一集合）。

删除或收窄旧的「全员禁止 INSERT/UPDATE/DELETE」循环。

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_sql_guardrail.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 2: SQLSandboxExecutor

**Files:**
- Create: `backend/app/security/sql_sandbox.py`
- Modify: `backend/app/agent/sql_executor.py`（改为禁止旁路：删除 `execute_sql` 公开实现，或改为调用 sandbox 并更新所有引用到 sandbox / Registry）
- Create: `backend/tests/test_sql_sandbox.py`
- Modify: 任何仍 import `app.agent.sql_executor.execute_sql` 的测试/节点 → 改走 sandbox 或 Registry（节点改动在 Task 5；本 Task 先让旧 executor 测试改测 sandbox）

**Interfaces:**
- Produces:

```python
MAX_WRITE_ROWS = 100
SANDBOX_TIMEOUT_S = 5.0

class SandboxError(Exception):
    """Safe, user-displayable sandbox / guardrail failure."""

@dataclass
class SandboxResult:
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    affected_rows: int | None = None
    is_write: bool = False

def sandbox_execute(sql: str, *, user_role: str) -> SandboxResult: ...
```

- Consumes: `check_sql`；`get_settings().db_path`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_sql_sandbox.py
import pytest
from app.db.init_db import init_database
from app.security.sql_sandbox import SandboxError, sandbox_execute


def test_select_returns_rows(tmp_db_path):
    init_database(reset=True)
    result = sandbox_execute(
        "SELECT COUNT(*) AS c FROM orders",
        user_role="analyst",
    )
    assert result.columns == ["c"]
    assert len(result.rows) == 1
    assert result.affected_rows is None
    assert result.is_write is False


def test_blocked_by_guardrail_does_not_execute(tmp_db_path):
    init_database(reset=True)
    with pytest.raises(SandboxError):
        sandbox_execute("DELETE FROM app_users", user_role="admin")


def test_admin_update_returns_affected_rows(tmp_db_path):
    init_database(reset=True)
    result = sandbox_execute(
        "UPDATE campaigns SET budget = budget WHERE id IN (SELECT id FROM campaigns LIMIT 1)",
        user_role="admin",
    )
    assert result.is_write is True
    assert result.affected_rows is not None
    assert result.affected_rows >= 0


def test_analyst_write_rejected(tmp_db_path):
    init_database(reset=True)
    with pytest.raises(SandboxError):
        sandbox_execute(
            "UPDATE campaigns SET budget = 1 WHERE id = 1",
            user_role="analyst",
        )
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_sql_sandbox.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `sql_sandbox.py`**

```python
# backend/app/security/sql_sandbox.py
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from app.config import get_settings
from app.security.sql_guardrail import check_sql

MAX_WRITE_ROWS = 100
SANDBOX_TIMEOUT_S = 5.0


class SandboxError(Exception):
    pass


@dataclass
class SandboxResult:
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    affected_rows: int | None = None
    is_write: bool = False


def _apply_row_limit(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    if re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
        return stripped
    return f"SELECT * FROM ({stripped}) LIMIT 100"


def _is_write(sql: str) -> bool:
    head = re.sub(r"\A(?:\s*--[^\n]*(?:\n|\Z))*\s*", "", sql.strip())
    return bool(re.match(r"(?:INSERT|UPDATE|DELETE)\b", head, re.IGNORECASE))


def sandbox_execute(sql: str, *, user_role: str) -> SandboxResult:
    gr = check_sql(sql, user_role=user_role)
    if not gr.ok:
        raise SandboxError(gr.reason or "SQL blocked by guardrail")

    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=SANDBOX_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    try:
        if user_role == "analyst":
            conn.execute("PRAGMA query_only=ON")
        if _is_write(sql):
            try:
                conn.execute("BEGIN")
                cur = conn.execute(sql.strip().rstrip(";"))
                affected = conn.total_changes  # 或 cur.rowcount；统一用 changes()
                # SQLite: connection.total_changes 是累计；优先 cur.rowcount
                n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else conn.changes()
                if n > MAX_WRITE_ROWS:
                    conn.rollback()
                    raise SandboxError(f"Write affects more than {MAX_WRITE_ROWS} rows")
                conn.commit()
                return SandboxResult(affected_rows=n, is_write=True)
            except SandboxError:
                raise
            except Exception as exc:
                conn.rollback()
                raise SandboxError(str(exc).splitlines()[0][:200]) from None
        limited = _apply_row_limit(sql)
        cur = conn.execute(limited)
        rows_raw = cur.fetchall()
        columns = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(r) for r in rows_raw]
        return SandboxResult(columns=columns, rows=rows, is_write=False)
    except SandboxError:
        raise
    except Exception as exc:
        raise SandboxError(str(exc).splitlines()[0][:200]) from None
    finally:
        conn.close()
```

注意：若 `conn.changes()` 在所用 Python/SQLite 绑定不可用，用 `SELECT changes()`。实现时选一种并让测试稳定。

同步处理旧模块：

- 将 `backend/tests/test_sql_executor.py` 改为测 `sandbox_execute`（或删除并依赖本文件）
- `backend/app/agent/sql_executor.py`：删除 `execute_sql`，或留下 deprecated 包装仅 `raise RuntimeError("use tools/registry")` —— **节点不得再 import 它**

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_sql_sandbox.py tests/test_sql_guardrail.py -v`  
Expected: PASS（若改了 test_sql_executor，一并跑过）

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 3: Tool schemas + AuditLog + Registry

**Files:**
- Create: `backend/app/tools/__init__.py`
- Create: `backend/app/tools/schemas.py`
- Create: `backend/app/tools/audit.py`
- Create: `backend/app/tools/registry.py`
- Create: `backend/tests/test_tool_registry.py`
- Modify: `.gitignore`（增加 `logs/`）

**Interfaces:**
- Produces:

```python
# schemas.py
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk_level: str  # low|medium|high
    permission_policy: str  # allow|allow_after_validation|deny
    enabled: bool = True
    input_schema: dict = field(default_factory=dict)

@dataclass(frozen=True)
class ToolContext:
    request_id: str
    trace_id: str
    session_id: str
    user_id: str
    user_role: str
    node: str

@dataclass
class ToolResult:
    ok: bool
    data: dict | None = None
    error: str | None = None
    events: list[dict] = field(default_factory=list)  # {event, data}

# audit.py
def audit_log_path() -> Path:  # REPO_ROOT / "logs" / "audit.jsonl"
def append_audit(record: dict) -> None:  # best-effort

# registry.py
class ToolRegistry:
    def register(self, spec: ToolSpec, handler: Callable[[dict, ToolContext], ToolResult]) -> None
    def invoke(self, name: str, args: dict, *, context: ToolContext) -> ToolResult
    def list_tools(self) -> list[ToolSpec]

def get_registry() -> ToolRegistry:  # process singleton
```

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_tool_registry.py
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
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_tool_registry.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现 schemas / audit / registry**

`audit.py` 要点：

- `append_audit`：确保父目录存在；`json.dumps(..., ensure_ascii=False)` + `\n`；异常吞掉并 `log_event("WARNING", "audit_write_failed", ...)`（用现有 `app.log.logging.log_event`）
- 记录里补 `ts` ISO UTC（若调用方未传）

`registry.invoke` 要点：

```python
def invoke(self, name, args, *, context: ToolContext) -> ToolResult:
    events = []
    t0 = time.monotonic()
    spec = self._tools.get(name)
    if spec is None or not spec.enabled:
        # permission_deny / not found
        ...
    events.append({"event": "tool_start", "data": {"tool": name, "risk_level": spec.risk_level, "node": context.node}})
    append_audit({..., "event": "tool_start", ...})
    if spec.permission_policy == "deny":
        # events tool_end / permission_deny; return ok=False
        ...
    try:
        raw = self._handlers[name](args, context)
    except Exception as exc:
        raw = ToolResult(ok=False, error=str(exc).splitlines()[0][:200])
    # merge events: start + handler.events(optional) + end
    latency = int((time.monotonic() - t0) * 1000)
    events.append({"event": "tool_end", "data": {"tool": name, "status": "ok" if raw.ok else "error", "latency_ms": latency, "node": context.node}})
    append_audit({..., "event": "tool_end", "status": ..., "latency_ms": latency, "detail": {...}})
    raw.events = events
    return raw
```

`.gitignore` 增加一行：`logs/`

`get_registry()`：模块级单例；Task 4 在 builtins 注册。

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_tool_registry.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 4: 五个内置 Tool

**Files:**
- Create: `backend/app/tools/builtins.py`
- Create: `backend/tests/test_builtin_tools.py`
- Modify: `backend/app/tools/__init__.py`（`ensure_builtins_registered` on import or lazy）

**Interfaces:**
- Consumes: `build_schema_tables`（`app.api.schema`）、`get_metric_spec` / `is_known_metric`、`check_sql`、`sandbox_execute`
- Produces: `ensure_builtins_registered() -> ToolRegistry` 注册齐 5 名

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_builtin_tools.py
from app.db.init_db import init_database
from app.tools.builtins import ensure_builtins_registered
from app.tools.schemas import ToolContext


def _ctx(role="analyst", node="Test"):
    return ToolContext(
        request_id="r",
        trace_id="t",
        session_id="s",
        user_id="1",
        user_role=role,
        node=node,
    )


def test_five_tools_registered():
    reg = ensure_builtins_registered()
    names = {t.name for t in reg.list_tools()}
    assert names >= {
        "query_schema",
        "retrieve_metric_definition",
        "validate_sql",
        "execute_sql",
        "render_chart",
    }


def test_query_schema_hides_sensitive_for_analyst(tmp_db_path):
    init_database(reset=True)
    reg = ensure_builtins_registered()
    out = reg.invoke("query_schema", {}, context=_ctx("analyst"))
    assert out.ok
    users = next(t for t in out.data["tables"] if t["name"] == "users")
    col_names = {c["name"] for c in users["columns"]}
    assert "phone" not in col_names


def test_retrieve_metric_definition(tmp_db_path):
    reg = ensure_builtins_registered()
    ok = reg.invoke(
        "retrieve_metric_definition",
        {"metric": "gmv"},
        context=_ctx(),
    )
    assert ok.ok
    assert "expression" in ok.data
    bad = reg.invoke(
        "retrieve_metric_definition",
        {"metric": "not_a_metric"},
        context=_ctx(),
    )
    assert bad.ok is False


def test_validate_and_execute_sql_read(tmp_db_path, monkeypatch, tmp_path):
    init_database(reset=True)
    monkeypatch.setattr(
        "app.tools.audit.audit_log_path", lambda: tmp_path / "audit.jsonl"
    )
    reg = ensure_builtins_registered()
    sql = "SELECT COUNT(*) AS c FROM orders"
    v = reg.invoke("validate_sql", {"sql": sql}, context=_ctx(node="SQLGuardrail"))
    assert v.ok and v.data["ok"] is True
    ex = reg.invoke("execute_sql", {"sql": sql}, context=_ctx(node="SQLExecutor"))
    assert ex.ok
    assert ex.data["columns"] == ["c"]
    assert (tmp_path / "audit.jsonl").exists()


def test_execute_sql_admin_write_audited(tmp_db_path, monkeypatch, tmp_path):
    init_database(reset=True)
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.tools.audit.audit_log_path", lambda: log)
    reg = ensure_builtins_registered()
    sql = "UPDATE campaigns SET budget = budget WHERE id IN (SELECT id FROM campaigns LIMIT 1)"
    ex = reg.invoke("execute_sql", {"sql": sql}, context=_ctx("admin", "SQLExecutor"))
    assert ex.ok
    assert ex.data.get("affected_rows") is not None
    text = log.read_text(encoding="utf-8")
    assert "execute_sql" in text
    assert "high" in text or "affected_rows" in text


def test_render_chart_returns_config():
    reg = ensure_builtins_registered()
    out = reg.invoke(
        "render_chart",
        {
            "columns": ["channel", "gmv"],
            "rows": [{"channel": "app", "gmv": 1}],
            "title": "渠道 GMV",
        },
        context=_ctx(),
    )
    assert out.ok
    assert out.data["type"] in {"bar", "line", "pie", "table"}
    assert "x" in out.data and "y" in out.data
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_builtin_tools.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现 `builtins.py`**

```python
def ensure_builtins_registered() -> ToolRegistry:
    reg = get_registry()
    if getattr(reg, "_builtins_ready", False):
        return reg
    # register each tool once
    ...
    reg._builtins_ready = True
    return reg
```

handlers 要点：

- `query_schema`: `{"tables": build_schema_tables(context.user_role)}`
- `retrieve_metric_definition`: args `metric`；未知 → `ToolResult(ok=False, error="Unknown metric")`
- `validate_sql`: `check_sql` → `data={"ok", "reason"}`；若 not ok，`ToolResult.ok` 仍可为 True（表示 tool 调用成功）但 `data.ok=False`；**节点**根据 `data.ok` 写 error。或约定 `ToolResult.ok=False` 当校验失败——**选定：校验失败时 `ToolResult(ok=False, error=reason, data={"ok": False, "reason": reason})`**，并 `append_audit` event 可带 `guardrail_deny`（在 handler 或 registry post 中写一条 detail）
- `execute_sql`: 忽略 args 里的 role；`sandbox_execute(sql, user_role=context.user_role)`；写成功 `data={"affected_rows", "is_write": True}`；读成功 `data={"columns","rows","is_write": False}`；`detail.risk_level` = high if write else medium（audit 由 registry post 从 result.data 取）
- `render_chart`: 若 columns>=2 且第二列数值倾向 → `type=bar`, `x=columns[0]`, `y=columns[1]`；否则 `type=table`

SQL 截断辅助（供 audit detail）：

```python
def _sql_detail(sql: str) -> dict:
    compact = " ".join(sql.split())
    return {
        "sql": compact[:200],
        "sql_fingerprint": compact[:64],
    }
```

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_builtin_tools.py tests/test_tool_registry.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 5: 节点经 Registry + pipeline `tool_*` SSE

**Files:**
- Modify: `backend/app/agent/nodes/sql_guardrail_node.py`
- Modify: `backend/app/agent/nodes/sql_executor_node.py`
- Modify: `backend/app/agent/pipeline.py`
- Modify: `backend/app/agent/state.py`（`tool_events: list[dict]` total=False）
- Modify: `backend/tests/test_graph_pipeline.py`
- Modify: `backend/tests/test_chat_api.py`

**Interfaces:**
- Consumes: `ensure_builtins_registered().invoke`
- Produces: 节点 delta 含 `tool_events`；pipeline 在 `node_start` 后 yield 各 `tool_*`

- [ ] **Step 1: 扩展 pipeline / chat 测试**

在 `test_happy_path_events` 末尾增加：

```python
    names = [e for e, _ in events]
    assert "tool_start" in names
    assert "tool_end" in names
```

在 `test_chat_sse_happy_path` 增加：

```python
    assert "event: tool_start" in text
    assert "event: tool_end" in text
```

可选：澄清路径断言仍无 `event: sql`（已有）。

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_graph_pipeline.py::test_happy_path_events tests/test_chat_api.py::test_chat_sse_happy_path -v`  
Expected: FAIL（无 tool_*）

- [ ] **Step 3: 改节点与 pipeline**

`sql_guardrail_node.py`：

```python
def sql_guardrail_node(state: AgentState) -> dict:
    from app.tools.builtins import ensure_builtins_registered
    from app.tools.schemas import ToolContext

    reg = ensure_builtins_registered()
    ctx = ToolContext(
        request_id=state["request_id"],
        trace_id=state["trace_id"],
        session_id=state["session_id"],
        user_id=state["user_id"],
        user_role=state["user_role"],
        node="SQLGuardrail",
    )
    result = reg.invoke(
        "validate_sql",
        {"sql": state.get("generated_sql") or ""},
        context=ctx,
    )
    out = {"tool_events": result.events}
    if not result.ok:
        out["error"] = result.error or "SQL blocked by guardrail"
    else:
        out["error"] = None
    return out
```

`sql_executor_node.py`：

```python
def sql_executor_node(state: AgentState) -> dict:
    from app.tools.builtins import ensure_builtins_registered
    from app.tools.schemas import ToolContext

    reg = ensure_builtins_registered()
    ctx = ToolContext(
        request_id=state["request_id"],
        trace_id=state["trace_id"],
        session_id=state["session_id"],
        user_id=state["user_id"],
        user_role=state["user_role"],
        node="SQLExecutor",
    )
    result = reg.invoke(
        "execute_sql",
        {"sql": state.get("generated_sql") or ""},
        context=ctx,
    )
    out = {"tool_events": result.events}
    if not result.ok:
        out["error"] = result.error or "SQL execution failed"
        return out
    data = result.data or {}
    if data.get("is_write"):
        out.update(
            {
                "columns": [],
                "rows": [],
                "error": None,
                # affected_rows 可选写入 state；Phase 4 UI 不强制
            }
        )
    else:
        out.update(
            {
                "columns": data.get("columns") or [],
                "rows": data.get("rows") or [],
                "error": None,
            }
        )
    return out
```

`pipeline.py` 在处理每个 node update 时：

```python
yield ("node_start", {"node": node})
if isinstance(delta, dict):
    merged.update(delta)
    for item in delta.get("tool_events") or []:
        # item: {"event": "tool_start"|"tool_end", "data": {...}}
        yield (item["event"], item.get("data") or {})
yield ("node_end", ...)
```

确认节点不再 `from app.agent.sql_executor import execute_sql`。

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_graph_pipeline.py tests/test_chat_api.py tests/test_builtin_tools.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 6: 前端 Trace + README

**Files:**
- Modify: `frontend/src/pages/AppWorkbench.tsx`
- Modify: `README.md`
- Optional: `docs/04-接口与前端.md`（仅当事件表缺 `tool_*` 描述时最小补一行——当前已有则跳过）

**Interfaces:**
- Consumes: SSE `tool_start` / `tool_end`

- [ ] **Step 1: 工作台事件处理**

在 `onEvent` switch 增加（在 `route_decision` 附近）：

```typescript
case 'tool_start':
  pushTrace(
    event,
    `调用 ${String(data.tool ?? '')}`.trim(),
  )
  break
case 'tool_end':
  pushTrace(
    event,
    `${String(data.tool ?? '')}: ${String(data.status ?? 'done')}`,
  )
  break
```

- [ ] **Step 2: README**

更新状态为 Phase 1–4：

- Tool Registry 为 SQL 执行唯一入口（validate_sql / execute_sql）
- analyst 只读 + 敏感列拦截；admin 受控 I/U/D；禁 DDL / 多语句 / 全部应用表
- SQLSandboxExecutor：`PRAGMA query_only` vs 可写；LIMIT 100；写行上限 100
- AuditLog：`logs/audit.jsonl`（脱敏）；与 Prompt 分离
- SSE 含 `tool_start` / `tool_end`
- 仍标明：ReAct/Coordinator、Repair、图表 UI、Memory 为后续 Phase
- `render_chart` 已注册但主图未自动调用

- [ ] **Step 3: 前端 build**

Run: `cd frontend && npm run build`  
Expected: 成功

- [ ] **Step 4: 全量后端回归**

Run: `cd backend && $PY -m pytest -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 7: 验收自检

**Files:** 无新文件（手工 + 测试）

- [ ] **Step 1: 对照 design §11 / docs/06 Phase 4**

| 项 | 验证方式 |
|----|----------|
| analyst 写/敏感/DDL 阻断 | `test_sql_guardrail` + sandbox |
| admin I/U/D；禁 DDL/多语句/app 表 | `test_sql_guardrail` + `test_execute_sql_admin_write_audited` |
| 无旁路 | 节点仅 Registry；旧 executor 无被节点引用（`rg "sql_executor" backend/app/agent/nodes`） |
| Trace + AuditLog | happy path SSE `tool_*`；audit 文件有行 |

- [ ] **Step 2: 可选真实联调**

启动前后端，跑只读示例；用 pytest/admin 路径验证写 SQL（不必 NL）。

- [ ] **Step 3: 向用户汇报**

说明测试结果、验收对照、建议 commit message（例如 `feat: Phase 4 Tool Registry, sandbox, and AuditLog`）。**不要**自动 commit。

---

## Self-Review

1. **Spec coverage:** Guardrail 升级、Sandbox、Registry+Audit、5 Tools、节点收口、SSE tool_*、前端 Trace、README、gitignore、验收 — 均有 Task。`render_chart` 可测但不入主图 — Task 4。admin NL 写 — 明确不做。
2. **Placeholder scan:** 无 TBD；`conn.changes()` 实现时二选一已注明以测试为准。
3. **Type consistency:** `ToolContext` / `ToolResult.events` / 节点 `tool_events` / pipeline yield 三元组一致；`sandbox_execute` 为唯一执行入口。

---

## Execution Handoff

Plan complete and saved to `spec/2026-07-25-phase4-tool-registry-sandbox-audit-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每 Task 派生子代理，Task 间复查  

**2. Inline Execution** — 本会话按 executing-plans 连续做，设检查点  

Which approach?
