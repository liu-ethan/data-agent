# Phase 5 Repair / Router / Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 SQLRepairer、ComplexityRouter（规则可覆盖）、ReAct/Coordinator 双模式子图（共用 Tool/Guardrail）、以及主图两端的 Session 槽位 + 偏好 JSON/最近摘要记忆。

**Architecture:** MemoryLoad → Intent → SlotMerge → Clarify → ComplexityRouter 分流；react 走 LLM Tool-calling 子图（禁止 `execute_sql`，用 `propose_sql` 产出 SQL）；coordinator 走 Schema→SQLGenerator；两模式汇入共享尾环 Guardrail→Executor→Repair×1→Answer→MemorySave。

**Tech Stack:** Python 3.12（conda `python3.12`）· FastAPI · LangGraph · openai SDK · pytest · SQLite

## Global Constraints

- 规格：`spec/2026-07-25-phase5-repair-router-memory-design.md`；产品：`docs/03` §2.3/§2.8/§5、`docs/02` §0.3–0.5、`docs/06` Phase 5
- 默认 chat SQL **必须**经 Guardrail 再进沙箱；ReAct **禁止**调用 `execute_sql`
- 不上 ChartPlanner / 前端图表 / 向量记忆 / MCP / LangChain `create_react_agent`
- 配置仅用根目录 `config.yaml`；禁止 `.env`
- **Python（强制）**：`/home/user/miniconda3/envs/python3.12/bin/python` 与同目录 `pip`；下文记为 `PY`
- **禁止** git worktree / `.worktrees/` 做功能开发；只在本仓库工作区改代码
- **Git commit：仅当用户明确要求时执行**；本计划 Commit 步骤默认跳过
- TDD：核心逻辑先写失败测试再实现

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/app/agent/memory/merge.py` | `merge_slots` 确定性浅合并 |
| `backend/app/agent/memory/summarize.py` | 模板摘要、偏好浅合并、敏感剥离 |
| `backend/app/agent/memory/store.py` | session / turns / preferences / summaries CRUD + 上限 |
| `backend/app/agent/memory/__init__.py` | 导出 |
| `backend/app/agent/nodes/complexity_router.py` | `decide_route` + 节点 |
| `backend/app/agent/nodes/sql_repairer.py` | SQLRepairer（最多 1 次） |
| `backend/app/agent/nodes/memory_load.py` | MemoryLoad |
| `backend/app/agent/nodes/slot_merge.py` | SlotMerge |
| `backend/app/agent/nodes/memory_save.py` | MemorySave |
| `backend/app/agent/nodes/react_agent.py` | ReAct LLM 步 |
| `backend/app/agent/nodes/react_tools.py` | Tool 调用 + `propose_sql` |
| `backend/app/agent/react_subgraph.py` | 编译 ReAct 子图 |
| `backend/app/agent/state.py` | 增量字段 |
| `backend/app/agent/graph.py` | 主图重接 |
| `backend/app/agent/pipeline.py` | ComplexityRouter / Repair SSE |
| `backend/app/agent/llm.py` | `chat_completion_with_tools` |
| `backend/app/agent/nodes/intent_analyzer.py` | 注入短记忆上下文 |
| `backend/app/agent/nodes/route_emit.py` | 删除或不再被主图引用 |
| `README.md` | Phase 1–5 状态 |
| `backend/tests/test_*.py` | 见各 Task |

工作目录：仓库根。跑测：

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
PY=/home/user/miniconda3/envs/python3.12/bin/python
```

---

### Task 1: `merge_slots`

**Files:**
- Create: `backend/app/agent/memory/__init__.py`
- Create: `backend/app/agent/memory/merge.py`
- Create: `backend/tests/test_slot_merge.py`

**Interfaces:**
- Produces: `merge_slots(prev: dict | None, curr: dict | None, preferences: dict | None = None) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_slot_merge.py
from app.agent.memory.merge import merge_slots


def test_empty_curr_inherits_prev():
    prev = {
        "metrics": ["gmv"],
        "time_range": "last_30d",
        "group_by": ["channel"],
        "top_n": 5,
        "filters": {"province": "华东"},
        "write_intent": False,
    }
    curr = {
        "metrics": [],
        "time_range": None,
        "group_by": ["city"],
        "top_n": None,
        "filters": None,
        "write_intent": False,
    }
    out = merge_slots(prev, curr)
    assert out["metrics"] == ["gmv"]
    assert out["time_range"] == "last_30d"
    assert out["group_by"] == ["city"]
    assert out["top_n"] == 5
    assert out["filters"] == {"province": "华东"}


def test_nonempty_curr_overrides():
    prev = {"metrics": ["gmv"], "time_range": "last_30d", "group_by": ["channel"]}
    curr = {"metrics": ["order_count"], "time_range": "last_7d", "group_by": ["city"]}
    out = merge_slots(prev, curr)
    assert out["metrics"] == ["order_count"]
    assert out["time_range"] == "last_7d"
    assert out["group_by"] == ["city"]


def test_preferences_default_time_range_when_no_prev():
    out = merge_slots(
        None,
        {"metrics": ["gmv"], "time_range": None, "group_by": []},
        {"default_time_range": "last_30d"},
    )
    assert out["time_range"] == "last_30d"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
$PY -m pytest tests/test_slot_merge.py -v
```

Expected: FAIL（`merge` 模块不存在）

- [ ] **Step 3: Implement**

```python
# backend/app/agent/memory/__init__.py
from app.agent.memory.merge import merge_slots

__all__ = ["merge_slots"]
```

```python
# backend/app/agent/memory/merge.py
from __future__ import annotations


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def merge_slots(
    prev: dict | None,
    curr: dict | None,
    preferences: dict | None = None,
) -> dict:
    prev = dict(prev or {})
    curr = dict(curr or {})
    keys = set(prev) | set(curr) | {
        "metrics",
        "time_range",
        "group_by",
        "top_n",
        "filters",
        "write_intent",
    }
    out: dict = {}
    for key in keys:
        c = curr.get(key, None) if key in curr else None
        p = prev.get(key, None)
        if key in curr and not _is_empty(c):
            out[key] = c
        elif not _is_empty(p):
            out[key] = p
        else:
            out[key] = c if key in curr else p
    prefs = preferences or {}
    if _is_empty(out.get("time_range")) and prefs.get("default_time_range"):
        out["time_range"] = prefs["default_time_range"]
    if "metrics" not in out or out["metrics"] is None:
        out["metrics"] = []
    if "group_by" not in out or out["group_by"] is None:
        out["group_by"] = []
    return out
```

- [ ] **Step 4: Run test to verify it passes**

```bash
$PY -m pytest tests/test_slot_merge.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 2: Memory store + summarize

**Files:**
- Create: `backend/app/agent/memory/summarize.py`
- Create: `backend/app/agent/memory/store.py`
- Create: `backend/tests/test_memory_store.py`
- Modify: `backend/app/agent/memory/__init__.py`

**Interfaces:**
- Produces:
  - `ensure_session(session_id: str, user_id: str) -> None`（不存在则创建；存在但 user 不匹配则 raise `MemoryError`）
  - `load_last_turn_slots(session_id: str, user_id: str) -> dict | None`
  - `load_preferences(user_id: str) -> dict`
  - `load_recent_summaries(user_id: str, *, limit: int = 5) -> list[dict]`
  - `save_turn(...)` / `update_preferences_from_slots(...)` / `append_summary(...)`
  - `build_result_summary(...)` / `merge_preferences(...)` / `strip_sensitive(...)`
- Constants: `MAX_TURNS_PER_SESSION = 10`, `MAX_SUMMARIES_PER_USER = 20`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_memory_store.py
import pytest

from app.agent.memory import store
from app.db.init_db import init_database


def test_ensure_session_and_isolation(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    with pytest.raises(store.MemoryError):
        store.ensure_session("s1", "2")


def test_save_turn_and_load_slots(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    store.save_turn(
        session_id="s1",
        user_id="1",
        question="各渠道 GMV",
        intent="channel_analysis",
        sql_text="SELECT 1",
        slots={
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "filters": {},
        },
        result_summary="ok",
    )
    slots = store.load_last_turn_slots("s1", "1")
    assert slots is not None
    assert slots["metrics"] == ["gmv"]
    assert slots["time_range"] == "last_30d"
    assert slots["group_by"] == ["channel"]


def test_turn_cap_keeps_latest_n(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    for i in range(12):
        store.save_turn(
            session_id="s1",
            user_id="1",
            question=f"q{i}",
            intent="sales_analysis",
            sql_text=None,
            slots={"metrics": ["gmv"], "time_range": "last_7d", "group_by": []},
            result_summary=f"r{i}",
        )
    from app.db.database import get_connection

    with get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM session_turns WHERE session_id = ?",
            ("s1",),
        ).fetchone()["c"]
    assert n == store.MAX_TURNS_PER_SESSION


def test_preferences_and_summaries(tmp_db_path):
    init_database(reset=True)
    store.ensure_session("s1", "1")
    store.update_preferences_from_slots(
        "1",
        {"time_range": "last_30d", "group_by": ["channel"]},
    )
    prefs = store.load_preferences("1")
    assert prefs.get("default_time_range") == "last_30d"
    assert "channel" in (prefs.get("preferred_dimensions") or [])
    store.append_summary(
        user_id="1",
        session_id="s1",
        question_summary="各渠道 GMV",
        answer_summary="渠道 A 最高",
        metrics=["gmv"],
        filters={},
    )
    rows = store.load_recent_summaries("1", limit=5)
    assert len(rows) == 1
    assert rows[0]["question_summary"] == "各渠道 GMV"


def test_strip_sensitive():
    from app.agent.memory.summarize import strip_sensitive

    text = "用户张三手机13800138000邮箱a@b.com身份证110101199001011234"
    out = strip_sensitive(text)
    assert "13800138000" not in out
    assert "110101199001011234" not in out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
$PY -m pytest tests/test_memory_store.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement `summarize.py` and `store.py`**

`summarize.py` 要点：

```python
import re

_PHONE = re.compile(r"\b1\d{10}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ID = re.compile(r"\b\d{17}[\dXx]\b")


def strip_sensitive(text: str) -> str:
    text = _PHONE.sub("[phone]", text or "")
    text = _EMAIL.sub("[email]", text)
    text = _ID.sub("[id_card]", text)
    return text


def merge_preferences(existing: dict, slots: dict) -> dict:
    out = dict(existing or {})
    tr = slots.get("time_range")
    if tr:
        out["default_time_range"] = tr
    dims = list(slots.get("group_by") or [])
    if dims:
        pref = list(out.get("preferred_dimensions") or [])
        for d in dims:
            if d not in pref:
                pref.append(d)
        out["preferred_dimensions"] = pref[:8]
    return out


def build_result_summary(*, answer: str | None, error: str | None, clarification: str | None) -> str:
    if clarification:
        return strip_sensitive(f"clarification: {clarification}")[:240]
    if error:
        return strip_sensitive(f"error: {error}")[:240]
    return strip_sensitive((answer or "")[:240])
```

`store.py` 要点：

- 用 `app.db.database.get_connection`
- `user_id` 统一 `str(user_id)`，写入 SQLite 时 `int(user_id)`（与 `app_users.id` / `chat_sessions.user_id` 一致）
- `ensure_session`：`SELECT` by `id`；无则 INSERT；有则校验 `user_id`
- `save_turn`：查 `MAX(turn_index)+1`；INSERT；若 COUNT>10 删除最旧（按 `turn_index` ASC LIMIT 超额）
- JSON 字段用 `json.dumps` / `json.loads`
- `load_last_turn_slots`：先 `ensure` 校验归属；`ORDER BY turn_index DESC LIMIT 1`；映射为 `{metrics, time_range, group_by, filters, last_sql, last_question, last_intent}`
- `append_summary`：INSERT 后删超额最旧
- 所有写路径对文本跑 `strip_sensitive`

- [ ] **Step 4: Run tests**

```bash
$PY -m pytest tests/test_memory_store.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 3: ComplexityRouter

**Files:**
- Create: `backend/app/agent/nodes/complexity_router.py`
- Create: `backend/tests/test_complexity_router.py`

**Interfaces:**
- Produces: `decide_route(question: str, slots: dict | None, model_route: str | None) -> tuple[str, str]`
- Produces: `complexity_router(state) -> dict` 写入 `route_mode` / `route_source`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_complexity_router.py
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


def test_keep_model_when_no_rule():
    mode, src = decide_route(
        "帮我看看销售情况",
        {"metrics": ["gmv"], "time_range": None, "group_by": []},
        "coordinator",
    )
    # 无强规则时保留模型；若实现把「单指标无复杂词」也强制 react，则允许 rule_override→react
    assert mode in ("react", "coordinator")
    assert src in ("model", "rule_override")
```

说明：`test_keep_model_when_no_rule` 若与强制 react 规则冲突，改为断言「单指标+时间窗+无复杂词 → react」。实现时优先：**复杂规则优先于简单规则**；两者都不命中才 `model`。

更精确的替代断言（推荐实现）：

```python
def test_keep_model_when_ambiguous():
    mode, src = decide_route(
        "帮我看看数据",
        {"metrics": [], "time_range": None, "group_by": []},
        "coordinator",
    )
    assert mode == "coordinator"
    assert src == "model"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
$PY -m pytest tests/test_complexity_router.py -v
```

- [ ] **Step 3: Implement**

```python
# backend/app/agent/nodes/complexity_router.py
from __future__ import annotations

import re

from app.agent.state import AgentState

_COMPLEX_RE = re.compile(
    r"(对比|同比|环比|归因|并且|以及|同时看|多指标)",
)


def decide_route(
    question: str,
    slots: dict | None,
    model_route: str | None,
) -> tuple[str, str]:
    slots = slots or {}
    metrics = list(slots.get("metrics") or [])
    time_range = slots.get("time_range")
    group_by = list(slots.get("group_by") or [])
    top_n = slots.get("top_n")
    q = question or ""

    complex_hit = len(metrics) >= 2 or bool(_COMPLEX_RE.search(q))
    if complex_hit:
        return "coordinator", "rule_override"

    simple_hit = (
        len(metrics) == 1
        and bool(time_range)
        and not complex_hit
        and (top_n is not None or len(group_by) <= 1)
    )
    if simple_hit:
        return "react", "rule_override"

    mode = model_route if model_route in ("react", "coordinator") else "react"
    return mode, "model"


def complexity_router(state: AgentState) -> dict:
    mode, src = decide_route(
        state.get("question") or "",
        state.get("slots"),
        state.get("route_mode"),
    )
    return {"route_mode": mode, "route_source": src}
```

- [ ] **Step 4: Run — expect PASS**

```bash
$PY -m pytest tests/test_complexity_router.py -v
```

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 4: SQLRepairer（单元）

**Files:**
- Create: `backend/app/agent/nodes/sql_repairer.py`
- Create: `backend/tests/test_sql_repairer.py`

**Interfaces:**
- Produces: `sql_repairer(state) -> dict`：设 `repaired=True`；调 LLM；写新 `generated_sql`；成功则 `error=None`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sql_repairer.py
from unittest.mock import patch

from app.agent.nodes.sql_repairer import sql_repairer


def test_repair_sets_flag_and_sql():
    state = {
        "question": "各渠道 GMV",
        "generated_sql": "SELECT pay_ammount FROM orders",
        "error": "no such column: pay_ammount",
        "relevant_tables": ["orders"],
        "relevant_columns": {"orders": ["id", "pay_amount", "channel"]},
        "metric_specs": [],
        "repaired": False,
    }
    with patch(
        "app.agent.nodes.sql_repairer.chat_completion",
        return_value="SELECT channel, SUM(pay_amount) AS gmv FROM orders GROUP BY channel",
    ):
        out = sql_repairer(state)
    assert out["repaired"] is True
    assert out["error"] is None
    assert "pay_amount" in out["generated_sql"]
    assert "pay_ammount" not in out["generated_sql"]


def test_repair_failure_keeps_error():
    state = {
        "question": "x",
        "generated_sql": "SELECT 1",
        "error": "boom",
        "relevant_tables": [],
        "relevant_columns": {},
        "repaired": False,
    }
    with patch(
        "app.agent.nodes.sql_repairer.chat_completion",
        side_effect=ValueError("no key"),
    ):
        out = sql_repairer(state)
    assert out["repaired"] is True
    assert out.get("error")
```

- [ ] **Step 2: Run — expect FAIL**

```bash
$PY -m pytest tests/test_sql_repairer.py -v
```

- [ ] **Step 3: Implement**

```python
# backend/app/agent/nodes/sql_repairer.py
from __future__ import annotations

import json
import re

from app.agent.llm import chat_completion
from app.agent.state import AgentState

_FENCE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.I)


def _extract_sql(text: str) -> str:
    raw = (text or "").strip()
    m = _FENCE.search(raw)
    if m:
        return m.group(1).strip().rstrip(";")
    return raw.strip().rstrip(";")


def sql_repairer(state: AgentState) -> dict:
    question = state.get("question") or ""
    sql = state.get("generated_sql") or ""
    err = state.get("error") or ""
    schema = {
        "tables": state.get("relevant_tables") or [],
        "columns": state.get("relevant_columns") or {},
        "metrics": state.get("metric_specs") or [],
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是 SQLite SQL 修复器。根据错误修复 SQL。"
                "只输出一条 SQL，不要解释。不要 DDL。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\nSQL:\n{sql}\n\nError:\n{err}\n\n"
                f"Schema:\n{json.dumps(schema, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        fixed = _extract_sql(chat_completion(messages))
        if not fixed:
            return {
                "repaired": True,
                "error": err or "SQL repair produced empty SQL",
            }
        return {"repaired": True, "generated_sql": fixed, "error": None}
    except Exception:
        return {
            "repaired": True,
            "error": err or "SQL repair failed",
        }
```

- [ ] **Step 4: Run — expect PASS**

```bash
$PY -m pytest tests/test_sql_repairer.py -v
```

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 5: AgentState + Memory 节点

**Files:**
- Modify: `backend/app/agent/state.py`
- Create: `backend/app/agent/nodes/memory_load.py`
- Create: `backend/app/agent/nodes/slot_merge.py`
- Create: `backend/app/agent/nodes/memory_save.py`
- Modify: `backend/tests/test_agent_state.py`（若有字段断言则扩展）
- Modify: `backend/tests/test_memory_store.py` 或新建轻量节点测试（可选）；至少在后续集成测覆盖

**Interfaces:**
- `memory_load(state) -> dict`：`session_slots` / `user_preferences` / `recent_summaries`；session 冲突 → `error`
- `slot_merge(state) -> dict`：覆盖 `slots`
- `memory_save(state) -> dict`：按设计表写库；返回 `{}` 或空 delta

- [ ] **Step 1: Extend state**

```python
# 在 AgentState 增加（total=False）:
session_slots: dict | None
user_preferences: dict | None
recent_summaries: list[dict] | None
react_messages: list[dict] | None
react_step: int
```

- [ ] **Step 2: Implement nodes**

`memory_load.py`：

```python
from app.agent.memory import store
from app.agent.state import AgentState


def memory_load(state: AgentState) -> dict:
    sid = state["session_id"]
    uid = str(state["user_id"])
    try:
        store.ensure_session(sid, uid)
    except store.MemoryError as exc:
        return {"error": str(exc)}
    return {
        "session_slots": store.load_last_turn_slots(sid, uid),
        "user_preferences": store.load_preferences(uid),
        "recent_summaries": store.load_recent_summaries(uid, limit=5),
        "react_step": 0,
        "repaired": bool(state.get("repaired", False)),
    }
```

`slot_merge.py`：

```python
from app.agent.memory.merge import merge_slots
from app.agent.state import AgentState


def slot_merge(state: AgentState) -> dict:
    merged = merge_slots(
        state.get("session_slots"),
        state.get("slots"),
        state.get("user_preferences"),
    )
    return {"slots": merged}
```

`memory_save.py`：按设计 §9.3：

- 始终 `save_turn`（若 `error` 为 session 归属错误且未 ensure 成功，可跳过）
- 仅当 `answer` 存在且无 `error` 且非澄清：`update_preferences_from_slots` + `append_summary`
- 澄清：`need_clarification` 为真 → 只写 turn，不改长期记忆

- [ ] **Step 3: Smoke test via store already covered；可选**

```python
def test_slot_merge_node_uses_session_slots(tmp_db_path):
    from app.agent.nodes.slot_merge import slot_merge
    out = slot_merge({
        "session_slots": {"metrics": ["gmv"], "time_range": "last_30d", "group_by": ["channel"]},
        "slots": {"metrics": [], "time_range": None, "group_by": ["city"]},
        "user_preferences": {},
    })
    assert out["slots"]["metrics"] == ["gmv"]
    assert out["slots"]["group_by"] == ["city"]
```

可放在 `test_slot_merge.py`。

- [ ] **Step 4: Run**

```bash
$PY -m pytest tests/test_slot_merge.py tests/test_memory_store.py tests/test_agent_state.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 6: 主图重接（Memory + Router + 共享 Repair 尾环；仍只挂 Coordinator 链）

本 Task **先不接 ReAct 子图**：ComplexityRouter 后若 `react` 也暂时走 Coordinator 链（或直接连 Schema），以便先绿尾环；Task 8 再接真 ReAct。  
**更推荐**：本 Task 的条件边对 `react`/`coordinator` 都指向 `SchemaRetriever`（临时），并在测试里只断言 Repair/Memory；Task 8 改边。

**Files:**
- Modify: `backend/app/agent/graph.py`
- Modify: `backend/app/agent/pipeline.py`
- Modify: `backend/tests/test_graph_compile.py`
- Modify: `backend/tests/test_graph_pipeline.py`

**Interfaces:**
- `_after_router`: clarification → ClarificationReply；else → SchemaRetriever（临时）
- `_after_guardrail`: error → MemorySave；else SQLExecutor
- `_after_executor`: ok → AnswerComposer；fail & !repaired → SQLRepairer；fail & repaired → MemorySave
- ClarificationReply / AnswerComposer → MemorySave → END
- SQLRepairer → SQLGuardrail

- [ ] **Step 1: Update failing pipeline expectations**

在 `test_graph_pipeline.py`：

- 所有 `_events` 初始 state 保持；`init_database(reset=True)` 已有
- 将断言/summarize 中的 `RouteEmit` 改为 `ComplexityRouter`
- 新增 Repair 集成（mock）：

```python
def test_repair_then_guardrail_and_success(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "gmv",
        "route_mode": "coordinator",
        "slots": {"metrics": ["gmv"], "time_range": "last_30d", "group_by": ["channel"], "top_n": 5},
        "need_clarification": False,
        "clarification_question": None,
    }
    bad_sql = "SELECT channel, SUM(pay_ammount) AS gmv FROM orders GROUP BY channel"
    good_sql = (
        "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
        "WHERE pay_status = 'paid' GROUP BY channel LIMIT 5"
    )

    with (
        patch("app.agent.nodes.intent_analyzer.chat_completion", return_value=json.dumps(intent_json)),
        patch("app.agent.nodes.sql_generator_node.generate_sql", return_value=bad_sql),
        patch("app.agent.nodes.sql_repairer.chat_completion", return_value=good_sql),
        patch(
            "app.agent.nodes.answer_composer_node.compose_answer",
            return_value="ok",
        ),
    ):
        # 若 generate_sql 在模块路径不同，按实际 patch sql_generator.generate_sql
        events = _events({
            "question": "各渠道 GMV Top5",
            "session_id": "s_repair",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_r",
            "trace_id": "req_r",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })
    names = [e for e, _ in events]
    assert "route_decision" in names
    sql_events = [d for e, d in events if e == "sql"]
    assert any(d.get("repaired") for d in sql_events)
```

注意：`user_id` 需存在于 `app_users` 时才写 FK——当前 `chat_sessions.user_id` 无 FK 约束则可直接用 `"1"`。若 `ensure_session` 要求用户存在，测试里先插入用户或放宽 store。

- [ ] **Step 2: Rewrite `graph.py`**

```python
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.answer_composer_node import answer_composer_node
from app.agent.nodes.clarification_checker import clarification_checker
from app.agent.nodes.clarification_reply import clarification_reply
from app.agent.nodes.complexity_router import complexity_router
from app.agent.nodes.intent_analyzer import intent_analyzer
from app.agent.nodes.memory_load import memory_load
from app.agent.nodes.memory_save import memory_save
from app.agent.nodes.schema_retriever import schema_retriever
from app.agent.nodes.slot_merge import slot_merge
from app.agent.nodes.sql_executor_node import sql_executor_node
from app.agent.nodes.sql_generator_node import sql_generator_node
from app.agent.nodes.sql_guardrail_node import sql_guardrail_node
from app.agent.nodes.sql_repairer import sql_repairer
from app.agent.state import AgentState


def _after_router(state: AgentState) -> str:
    if state.get("need_clarification"):
        return "ClarificationReply"
    # Task 8 将 react → ReActSubgraph
    return "SchemaRetriever"


def _after_guardrail(state: AgentState) -> str:
    if state.get("error"):
        return "MemorySave"
    return "SQLExecutor"


def _after_executor(state: AgentState) -> str:
    if not state.get("error"):
        return "AnswerComposer"
    if state.get("repaired"):
        return "MemorySave"
    return "SQLRepairer"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("MemoryLoad", memory_load)
    g.add_node("IntentAnalyzer", intent_analyzer)
    g.add_node("SlotMerge", slot_merge)
    g.add_node("ClarificationChecker", clarification_checker)
    g.add_node("ComplexityRouter", complexity_router)
    g.add_node("ClarificationReply", clarification_reply)
    g.add_node("SchemaRetriever", schema_retriever)
    g.add_node("SQLGenerator", sql_generator_node)
    g.add_node("SQLGuardrail", sql_guardrail_node)
    g.add_node("SQLExecutor", sql_executor_node)
    g.add_node("SQLRepairer", sql_repairer)
    g.add_node("AnswerComposer", answer_composer_node)
    g.add_node("MemorySave", memory_save)

    g.add_edge(START, "MemoryLoad")
    g.add_edge("MemoryLoad", "IntentAnalyzer")
    g.add_edge("IntentAnalyzer", "SlotMerge")
    g.add_edge("SlotMerge", "ClarificationChecker")
    g.add_edge("ClarificationChecker", "ComplexityRouter")
    g.add_conditional_edges(
        "ComplexityRouter",
        _after_router,
        {
            "ClarificationReply": "ClarificationReply",
            "SchemaRetriever": "SchemaRetriever",
            # Task 8 增加 "ReActSubgraph"
        },
    )
    g.add_edge("ClarificationReply", "MemorySave")
    g.add_edge("SchemaRetriever", "SQLGenerator")
    g.add_edge("SQLGenerator", "SQLGuardrail")
    g.add_conditional_edges(
        "SQLGuardrail",
        _after_guardrail,
        {"SQLExecutor": "SQLExecutor", "MemorySave": "MemorySave"},
    )
    g.add_conditional_edges(
        "SQLExecutor",
        _after_executor,
        {
            "AnswerComposer": "AnswerComposer",
            "SQLRepairer": "SQLRepairer",
            "MemorySave": "MemorySave",
        },
    )
    g.add_edge("SQLRepairer", "SQLGuardrail")
    g.add_edge("AnswerComposer", "MemorySave")
    g.add_edge("MemorySave", END)
    return g.compile()
```

若 `MemoryLoad` 在 Intent 前且 `error`（session 冲突），应短路：可在 MemoryLoad 后加条件边；第一版可简化——冲突极少，集成测不覆盖跨用户撞 session。

- [ ] **Step 3: Update `pipeline.py`**

- `_summarize`：`ComplexityRouter` → `route_mode`；增加 `MemoryLoad`/`MemorySave`/`SlotMerge`/`SQLRepairer`
- `route_decision`：节点名从 `RouteEmit` 改为 `ComplexityRouter`
- `sql` 事件：
  - `SQLGenerator`：`repaired: False`
  - `SQLRepairer`：若有 `generated_sql` → `repaired: True`
- 失败路径：MemorySave 后仍应已 yield 过 `error`（Executor/Guardrail 时）

- [ ] **Step 4: Fix `test_graph_compile.py`**

删除/改写 `test_route_emit_defaults` → `test_complexity_router_defaults`。

- [ ] **Step 5: Run**

```bash
$PY -m pytest tests/test_graph_compile.py tests/test_graph_pipeline.py tests/test_chat_api.py -v
```

Expected: PASS（按实际 patch 路径微调）

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 7: LLM tools helper + ReAct 工具节点

**Files:**
- Modify: `backend/app/agent/llm.py`
- Create: `backend/app/agent/nodes/react_tools.py`
- Create: `backend/app/agent/nodes/react_agent.py`
- Create: `backend/tests/test_react_subgraph.py`（本 Task 先写工具层测试）

**Interfaces:**
- `chat_completion_with_tools(messages, tools, *, temperature=0) -> dict`  
  返回 `{"content": str | None, "tool_calls": list[{"id","name","arguments": dict}]}`
- `REACT_TOOL_NAMES = ("query_schema", "retrieve_metric_definition", "validate_sql", "propose_sql")`
- `build_react_openai_tools() -> list`（OpenAI tools JSON；前三个来自 Registry spec + input_schema，`propose_sql` 本地定义）
- `react_agent(state) -> dict`：调 LLM；追加 assistant message；若有 tool_calls 写入 state；若无 tool_calls 且无 sql → 可尝试从 content 抽 SQL
- `react_tools_node(state) -> dict`：执行 tool_calls；`propose_sql` 写 `generated_sql`；Registry 工具合并 `tool_events`；`react_step += 1`

- [ ] **Step 1: Failing tests**

```python
# backend/tests/test_react_subgraph.py
from app.agent.nodes.react_tools import REACT_TOOL_NAMES, apply_propose_sql, build_react_openai_tools


def test_react_tools_exclude_execute_sql():
    names = {t["function"]["name"] for t in build_react_openai_tools()}
    assert "execute_sql" not in names
    assert "render_chart" not in names
    assert set(REACT_TOOL_NAMES) == names


def test_propose_sql_writes_state():
    out = apply_propose_sql({"sql": "SELECT 1"})
    assert out["generated_sql"] == "SELECT 1"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

`llm.py` 追加：

```python
def chat_completion_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    temperature: float = 0,
) -> dict:
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise ValueError(
            "LLM api_key is not configured; set llm.api_key in config.yaml"
        )
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        tools=tools,
        temperature=temperature,
    )
    msg = response.choices[0].message
    tool_calls = []
    for tc in msg.tool_calls or []:
        import json
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(
            {"id": tc.id, "name": tc.function.name, "arguments": args}
        )
    return {"content": msg.content, "tool_calls": tool_calls}
```

`react_tools.py`：为 Registry 三个工具补齐 `input_schema`（若 builtins 未写，在 `build_react_openai_tools` 内硬编码参数 schema）：

```python
PROPOSE_SQL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
}
```

`react_agent.py`：构造 system prompt（问题、merged slots、可用工具说明、必须 `propose_sql`）；维护 `react_messages`。

退出信号：state 已有 `generated_sql` 时，子图条件边结束（见 Task 8）。

- [ ] **Step 4: Run**

```bash
$PY -m pytest tests/test_react_subgraph.py -v
```

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 8: ReAct 子图挂主图

**Files:**
- Create: `backend/app/agent/react_subgraph.py`
- Modify: `backend/app/agent/graph.py`
- Modify: `backend/tests/test_react_subgraph.py`
- Modify: `backend/tests/test_phase5_pipeline.py`（本 Task 创建）

**Interfaces:**
- `build_react_subgraph()` 或在主图内联节点 `ReActAgent` / `ReActTools`
- 条件：`_after_react_agent`：有 tool_calls → ReActTools；有 `generated_sql` → 出子图进 SQLGuardrail；`react_step >= 5` 且无 sql → 设 error → MemorySave
- `_after_react_tools`：有 `generated_sql` → SQLGuardrail；else → ReActAgent
- `_after_router`：`react` → ReActAgent；`coordinator` → SchemaRetriever

推荐主图内联（不必 compile 嵌套 subgraph），节点名：`ReActAgent`、`ReActTools`。

- [ ] **Step 1: Test mock ReAct loop**

```python
# backend/tests/test_phase5_pipeline.py
import json
from unittest.mock import patch

from app.agent.pipeline import iter_pipeline_events
from app.db.init_db import init_database


def test_react_route_uses_propose_sql_then_tail(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "gmv",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "top_n": 5,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
        "WHERE pay_status = 'paid' GROUP BY channel LIMIT 5"
    )

    def fake_tools(messages, tools, temperature=0):
        # 第一步直接 propose_sql
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "1",
                    "name": "propose_sql",
                    "arguments": {"sql": sql},
                }
            ],
        }

    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.nodes.react_agent.chat_completion_with_tools",
            side_effect=fake_tools,
        ),
        patch(
            "app.agent.nodes.answer_composer_node.compose_answer",
            return_value="渠道汇总完成",
        ),
    ):
        events = list(
            iter_pipeline_events(
                {
                    "question": "各渠道 GMV Top5",
                    "session_id": "s_react",
                    "user_id": "1",
                    "user_role": "analyst",
                    "request_id": "req_react",
                    "trace_id": "req_react",
                    "need_clarification": False,
                    "repaired": False,
                    "agent_trace": [],
                }
            )
        )
    payload = next(d for e, d in events if e == "route_decision")
    assert payload["route_mode"] == "react"
    assert any(e == "rows" for e, _ in events)
    assert any(e == "answer" for e, _ in events)
```

另加：`test_coordinator_keyword_override` 断言 `route_source=rule_override` 且路径含 `SchemaRetriever`（通过 `node_end` summary）。

- [ ] **Step 2: Implement graph edges for ReAct**

```python
def _after_router(state: AgentState) -> str:
    if state.get("need_clarification"):
        return "ClarificationReply"
    if state.get("route_mode") == "react":
        return "ReActAgent"
    return "SchemaRetriever"


def _after_react_agent(state: AgentState) -> str:
    if state.get("error"):
        return "MemorySave"
    msgs = state.get("react_messages") or []
    # react_agent 应把 pending tool_calls 放 state["pending_tool_calls"]
    if state.get("pending_tool_calls"):
        return "ReActTools"
    if state.get("generated_sql"):
        return "SQLGuardrail"
    if int(state.get("react_step") or 0) >= 5:
        return "MemorySave"
    return "MemorySave"  # 无工具无 SQL


def _after_react_tools(state: AgentState) -> str:
    if state.get("generated_sql"):
        return "SQLGuardrail"
    if int(state.get("react_step") or 0) >= 5:
        if not state.get("generated_sql"):
            # 由节点写 error
            return "MemorySave"
        return "SQLGuardrail"
    return "ReActAgent"
```

`react_agent` 在 `react_step>=5` 且将要调用时直接返回 `error`。

- [ ] **Step 3: pipeline** 为 `ReActTools` 的 `propose_sql` 推送 `sql`（`repaired: false`）——可在 delta 含 `generated_sql` 且节点为 `ReActTools` 时 yield。

- [ ] **Step 4: Run**

```bash
$PY -m pytest tests/test_react_subgraph.py tests/test_phase5_pipeline.py tests/test_graph_pipeline.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 9: 多轮记忆集成 + Intent 上下文

**Files:**
- Modify: `backend/app/agent/nodes/intent_analyzer.py`
- Modify: `backend/tests/test_phase5_pipeline.py`
- Modify: `backend/tests/test_intent_analyzer.py`（prompt 可含可选 context；无 context 时行为不变）

**Interfaces:**
- `build_intent_prompt(question, *, session_slots=None, preferences=None, recent_summaries=None)`
- user message 追加短上下文块（各一行，截断）

- [ ] **Step 1: Failing follow-up test**

```python
def test_followup_inherits_slots(tmp_db_path):
    init_database(reset=True)
    from app.agent.memory import store

    store.ensure_session("s_fu", "1")
    store.save_turn(
        session_id="s_fu",
        user_id="1",
        question="最近30天各渠道GMV",
        intent="channel_analysis",
        sql_text="SELECT 1",
        slots={
            "metrics": ["gmv"],
            "time_range": "last_30d",
            "group_by": ["channel"],
            "filters": {},
        },
        result_summary="ok",
    )
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.8,
        "summary": "按城市拆",
        "route_mode": "react",
        "slots": {
            "metrics": [],
            "time_range": None,
            "group_by": ["city"],
            "top_n": None,
            "filters": None,
            "write_intent": False,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT city, SUM(pay_amount) AS gmv FROM orders o "
        "JOIN users u ON u.id = o.user_id "
        "WHERE pay_status='paid' GROUP BY city LIMIT 100"
    )

    captured = {}

    def capture_intent(messages, temperature=0):
        captured["messages"] = messages
        return json.dumps(intent_json)

    with (
        patch("app.agent.nodes.intent_analyzer.chat_completion", side_effect=capture_intent),
        patch(
            "app.agent.nodes.react_agent.chat_completion_with_tools",
            return_value={
                "content": None,
                "tool_calls": [{"id": "1", "name": "propose_sql", "arguments": {"sql": sql}}],
            },
        ),
        patch(
            "app.agent.nodes.answer_composer_node.compose_answer",
            return_value="按城市拆解完成",
        ),
    ):
        events = list(
            iter_pipeline_events(
                {
                    "question": "那按城市拆一下",
                    "session_id": "s_fu",
                    "user_id": "1",
                    "user_role": "analyst",
                    "request_id": "req_fu",
                    "trace_id": "req_fu",
                    "need_clarification": False,
                    "repaired": False,
                    "agent_trace": [],
                }
            )
        )
    # 合并后不应澄清
    done = next(d for e, d in events if e == "done")
    assert done["need_clarification"] is False
    assert any(e == "rows" for e, _ in events)
```

另测跨 session：`update_preferences_from_slots` 后新 `session_id` 的 MemoryLoad `user_preferences` 非空（可用节点单测或 pipeline mock 后读 store）。

- [ ] **Step 2: Implement intent context**

`intent_analyzer` 读取 state 的 memory 字段，拼进 user content；**禁止**把全库 schema 塞进 prompt。

- [ ] **Step 3: Run**

```bash
$PY -m pytest tests/test_phase5_pipeline.py tests/test_intent_analyzer.py -v
```

- [ ] **Step 4: Commit（默认跳过）**

---

### Task 10: 三类型 Repair 验收 + Guardrail 拒绝不进 Repair

**Files:**
- Modify: `backend/tests/test_sql_repairer.py` 或 `test_phase5_pipeline.py`

- [ ] **Step 1: Add parameterized repair cases**

对以下三类，mock `generate_sql`（coordinator 路径）返回坏 SQL，`sql_repairer.chat_completion` 返回好 SQL，断言出现 `SQLRepairer` 节点且最终无 error 或有 rows：

1. 未知列：`pay_ammount` → `pay_amount`
2. 缺 GROUP BY：`SELECT channel, SUM(pay_amount) FROM orders` → 补 `GROUP BY channel`
3. 错表：`FROM orderz` → `FROM orders`

再测：`generated_sql` 含敏感列、role=analyst → Guardrail 设 error → 事件中 **无** `SQLRepairer` `node_start`。

- [ ] **Step 2: Run**

```bash
$PY -m pytest tests/test_phase5_pipeline.py tests/test_sql_repairer.py -v
```

- [ ] **Step 3: Commit（默认跳过）**

---

### Task 11: 清理 RouteEmit + 全量回归 + README

**Files:**
- Delete or gut: `backend/app/agent/nodes/route_emit.py`（若仍被 import 则改为 re-export `complexity_router` 过渡；推荐删除并修引用）
- Modify: `README.md`（Phase 1–5：双模式、Repair、Memory 形态、`route_source`）
- Modify: 任何仍写「单路径 / RouteEmit / Phase 5 目标」的过时句

- [ ] **Step 1: Full pytest**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
$PY -m pytest -v
```

Expected: 全部 PASS

- [ ] **Step 2: README 关键要点**

- 状态改为 Phase 1–5 已落地  
- 主链路图改为：MemoryLoad → Intent → SlotMerge → Clarify → ComplexityRouter → ReAct | Coordinator → 共享尾环（Guardrail→Execute→Repair×1）→ Answer → MemorySave  
- 记忆节：Session 槽位 + preferences_json + 最近摘要列表（无向量）  
- `route_source`：model / rule_override  
- ReAct 不直接 `execute_sql`  
- Chart UI 仍为 Phase 6

- [ ] **Step 3: Commit（默认跳过）**

---

## Self-Review

**1. Spec coverage**

| Design 项 | Task |
|-----------|------|
| ComplexityRouter + route_source | Task 3, 6, 8 |
| ReAct 真 tool-calling，禁 execute_sql | Task 7–8 |
| Coordinator Schema→SQL | Task 6, 8 |
| 共享 Repair 环，Guardrail 拒绝不修 | Task 4, 6, 10 |
| 3 类可修错误 | Task 10 |
| merge_slots | Task 1, 5, 9 |
| MemoryLoad/Save + 上限隔离 | Task 2, 5, 9 |
| 追问继承 / 跨 session 偏好 | Task 9 |
| pipeline SSE | Task 6, 8 |
| README | Task 11 |

**2. Placeholder scan：** 无 TBD；Commit 明确默认跳过。

**3. Type consistency：** `decide_route` / `merge_slots` / `chat_completion_with_tools` / `pending_tool_calls` 在后续 Task 引用一致；`user_id` 以 `str` 入 AgentState、store 内转 `int`。

---

## Execution Handoff

Plan complete and saved to `spec/2026-07-25-phase5-repair-router-memory-plan.md`.

**Two execution options:**

1. **Subagent-Driven（recommended）** — 每 Task 新开 subagent，Task 间复查  
2. **Inline Execution** — 本会话按 executing-plans 连续做，设检查点  

Which approach?
