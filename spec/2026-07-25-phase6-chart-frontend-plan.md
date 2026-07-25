# Phase 6 Chart + Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 ChartPlanner（轻量 LLM + 启发式降级）、SSE `chart`/`write_result`、Recharts 三图型，并增量打磨工作台 Trace / 角色徽章 / admin 写提示与登录页品牌层次。

**Architecture:** 共享尾环在 SQLExecutor 成功后插入 ChartPlanner（直调 `plan_chart`，不经 Registry）；`render_chart` Tool 复用同一函数；前端消费 `chart` 与 `write_result` 渐进渲染。

**Tech Stack:** Python 3.12（conda `python3.12`）· FastAPI · LangGraph · openai SDK · pytest · React · Vite · TypeScript · Tailwind · Recharts

## Global Constraints

- 规格：`spec/2026-07-25-phase6-chart-frontend-design.md`；产品：`docs/06` Phase 6、`docs/03` §2.9、`docs/04` SSE/工作台
- 主路径 ChartPlanner **直调** `plan_chart`；显式 Tool 调用才有 Registry 审计
- 仅成功 SELECT 且有行才调 LLM；写/空/失败 → `chart=None`
- LLM 失败不写 pipeline `error`；校验失败 → 启发式 → `table`
- 前端增量打磨，不大改布局；图表库仅 Recharts
- 配置仅用根目录 `config.yaml`；禁止 `.env`
- **Python（强制）**：`/home/user/miniconda3/envs/python3.12/bin/python` 与同目录 `pip`；下文记为 `PY`
- **禁止** git worktree / `.worktrees/`；只在本仓库工作区改代码
- **Git commit：仅当用户明确要求时执行**；本计划 Commit 步骤默认跳过
- TDD：核心逻辑先写失败测试再实现
- 前端无单测脚手架：用 `npm run build` 做类型/编译验收，不新增 vitest

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/app/agent/chart_planner.py` | `plan_chart` / 校验 / 启发式 |
| `backend/app/agent/nodes/chart_planner.py` | ChartPlanner 节点 |
| `backend/app/agent/state.py` | `chart` / `is_write` / `affected_rows` |
| `backend/app/agent/nodes/sql_executor_node.py` | 写结果字段写入 state |
| `backend/app/agent/graph.py` | Executor → ChartPlanner → AnswerComposer |
| `backend/app/agent/pipeline.py` | `chart` / `write_result` / `done.repaired` |
| `backend/app/agent/answer_composer.py` | 写成功确定性文案 |
| `backend/app/agent/nodes/answer_composer_node.py` | 传入 is_write / affected_rows |
| `backend/app/tools/builtins.py` | `render_chart` → `plan_chart` |
| `backend/tests/test_chart_planner.py` | planner + 节点 |
| `backend/tests/test_phase6_pipeline.py` | SSE chart / write_result |
| `backend/tests/test_builtin_tools.py` | render_chart 行为 |
| `backend/tests/test_agent_state.py` | 新字段 |
| `backend/tests/test_graph_compile.py` | 图含 ChartPlanner |
| `frontend/package.json` | + recharts |
| `frontend/src/components/ResultChart.tsx` | 三图型 |
| `frontend/src/pages/AppWorkbench.tsx` | chart / write / trace / role |
| `frontend/src/pages/LoginPage.tsx` | 品牌层次小幅加强 |
| `docs/03-Agent设计.md` / `docs/04-接口与前端.md` / `README.md` | 行为同步 |

工作目录：仓库根。跑测：

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
PY=/home/user/miniconda3/envs/python3.12/bin/python
```

前端：

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/frontend
```

---

### Task 1: `plan_chart` 核心（启发式 + LLM 校验）

**Files:**
- Create: `backend/app/agent/chart_planner.py`
- Create: `backend/tests/test_chart_planner.py`

**Interfaces:**
- Produces:
  - `plan_chart(question: str, columns: list[str], rows: list[dict], *, slots: dict | None = None, title_hint: str = "") -> dict | None`
  - 成功时 dict keys: `type`, `x`, `y`, `title`；`type ∈ {line,bar,pie,table}`
  - 空 columns/rows → `None`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_chart_planner.py
from unittest.mock import patch

import pytest

from app.agent.chart_planner import plan_chart


def test_empty_rows_returns_none():
    assert plan_chart("q", ["a", "b"], []) is None


def test_empty_columns_returns_none():
    assert plan_chart("q", [], [{"a": 1}]) is None


def test_llm_valid_bar_accepted():
    cols = ["channel", "gmv"]
    rows = [{"channel": "app", "gmv": 10}, {"channel": "web", "gmv": 5}]
    with patch(
        "app.agent.chart_planner.chat_completion",
        return_value='{"type":"bar","x":"channel","y":"gmv","title":"渠道 GMV"}',
    ) as m:
        out = plan_chart("各渠道 GMV", cols, rows)
    assert out == {
        "type": "bar",
        "x": "channel",
        "y": "gmv",
        "title": "渠道 GMV",
    }
    m.assert_called_once()


def test_llm_invalid_falls_back_to_heuristic_bar():
    cols = ["channel", "gmv"]
    rows = [{"channel": "app", "gmv": 10}, {"channel": "web", "gmv": 5}]
    with patch(
        "app.agent.chart_planner.chat_completion",
        return_value='{"type":"zzz","x":"nope","y":"gmv","title":"x"}',
    ):
        out = plan_chart("各渠道 GMV Top5", cols, rows)
    assert out is not None
    assert out["type"] == "bar"
    assert out["x"] == "channel"
    assert out["y"] == "gmv"


def test_llm_exception_falls_back():
    cols = ["order_date", "gmv"]
    rows = [
        {"order_date": "2024-01-01", "gmv": 1},
        {"order_date": "2024-01-02", "gmv": 2},
    ]
    with patch(
        "app.agent.chart_planner.chat_completion",
        side_effect=RuntimeError("boom"),
    ):
        out = plan_chart("最近趋势", cols, rows)
    assert out is not None
    assert out["type"] == "line"
    assert out["x"] == "order_date"
    assert out["y"] == "gmv"


def test_empty_question_skips_llm_uses_heuristic():
    cols = ["channel", "gmv"]
    rows = [{"channel": "app", "gmv": 10}]
    with patch("app.agent.chart_planner.chat_completion") as m:
        out = plan_chart("", cols, rows)
    m.assert_not_called()
    assert out is not None
    assert out["type"] == "bar"


def test_heuristic_pie_for_rate_column():
    cols = ["pay_method", "success_rate"]
    rows = [
        {"pay_method": "alipay", "success_rate": 0.9},
        {"pay_method": "wechat", "success_rate": 0.8},
    ]
    with patch("app.agent.chart_planner.chat_completion") as m:
        out = plan_chart("", cols, rows)
    m.assert_not_called()
    assert out["type"] == "pie"
    assert out["x"] == "pay_method"
    assert out["y"] == "success_rate"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
$PY -m pytest tests/test_chart_planner.py -v
```

Expected: FAIL（`ModuleNotFoundError` 或 import error）

- [ ] **Step 3: Implement `chart_planner.py`**

```python
# backend/app/agent/chart_planner.py
from __future__ import annotations

import json
import re
from datetime import datetime

from app.agent.llm import chat_completion

_CHART_TYPES = frozenset({"line", "bar", "pie", "table"})
_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_SAMPLE_ROWS = 12
_DATE_NAME = re.compile(r"(date|time|日|天|_at|_on)", re.IGNORECASE)
_SHARE_NAME = re.compile(r"(rate|ratio|占比|比例|份额|percent)", re.IGNORECASE)
_DATE_FMTS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S")


def plan_chart(
    question: str,
    columns: list[str],
    rows: list[dict],
    *,
    slots: dict | None = None,
    title_hint: str = "",
) -> dict | None:
    if not columns or not rows:
        return None
    sample = rows[:_SAMPLE_ROWS]
    if question.strip():
        try:
            raw = chat_completion(
                _build_messages(question, columns, sample, slots, title_hint)
            )
            parsed = _parse_json(raw)
            validated = _validate_chart(parsed, columns, sample)
            if validated is not None:
                return validated
        except Exception:
            pass
    return _heuristic_chart(question, columns, sample, title_hint)


def _build_messages(
    question: str,
    columns: list[str],
    sample: list[dict],
    slots: dict | None,
    title_hint: str,
) -> list[dict]:
    payload = {
        "question": question,
        "columns": columns,
        "sample_rows": sample,
        "slots": slots,
        "title_hint": title_hint,
    }
    system = (
        "你是图表规划器。根据查询结果选择图表，只输出 JSON 对象，字段："
        'type(line|bar|pie|table), x(列名), y(列名), title(短中文)。'
        "趋势用 line，TopN/分类对比用 bar，占比用 pie，明细用 table。"
        "x/y 必须是 columns 中的列名。"
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _validate_chart(
    data: dict | None, columns: list[str], sample: list[dict]
) -> dict | None:
    if not data:
        return None
    ctype = str(data.get("type") or "").strip().lower()
    if ctype not in _CHART_TYPES:
        return None
    x = str(data.get("x") or "")
    y = str(data.get("y") or "")
    title = str(data.get("title") or "")
    if ctype == "table":
        return {
            "type": "table",
            "x": x if x in columns else (columns[0] if columns else ""),
            "y": y if y in columns else (columns[1] if len(columns) > 1 else ""),
            "title": title,
        }
    if x not in columns or y not in columns:
        return None
    if not _column_mostly_numeric(sample, y):
        return None
    return {"type": ctype, "x": x, "y": y, "title": title}


def _heuristic_chart(
    question: str,
    columns: list[str],
    sample: list[dict],
    title_hint: str,
) -> dict:
    title = title_hint or question[:40]
    date_col = _find_date_column(columns, sample)
    num_cols = [c for c in columns if _column_mostly_numeric(sample, c)]
    cat_cols = [c for c in columns if c not in num_cols]
    if date_col and num_cols:
        y = next((c for c in num_cols if c != date_col), None)
        if y:
            return {"type": "line", "x": date_col, "y": y, "title": title}
    share_cols = [c for c in num_cols if _SHARE_NAME.search(c)]
    if share_cols and cat_cols:
        y = share_cols[0]
        x = next((c for c in cat_cols if c != y), cat_cols[0])
        distinct = {row.get(x) for row in sample}
        ctype = "pie" if len(distinct) <= 12 else "bar"
        return {"type": ctype, "x": x, "y": y, "title": title}
    if (
        _SHARE_NAME.search(question)
        and cat_cols
        and num_cols
        and len({row.get(cat_cols[0]) for row in sample}) <= 12
    ):
        return {
            "type": "pie",
            "x": cat_cols[0],
            "y": num_cols[0],
            "title": title,
        }
    if cat_cols and num_cols:
        return {
            "type": "bar",
            "x": cat_cols[0],
            "y": num_cols[0],
            "title": title,
        }
    return {
        "type": "table",
        "x": columns[0] if columns else "",
        "y": columns[1] if len(columns) > 1 else "",
        "title": title,
    }


def _find_date_column(columns: list[str], sample: list[dict]) -> str | None:
    for c in columns:
        if _DATE_NAME.search(c):
            return c
    for c in columns:
        values = [row.get(c) for row in sample if row.get(c) is not None]
        if values and all(_looks_date(v) for v in values[:5]):
            return c
    return None


def _looks_date(value: object) -> bool:
    if isinstance(value, datetime):
        return True
    if not isinstance(value, str):
        return False
    s = value.strip()
    for fmt in _DATE_FMTS:
        try:
            datetime.strptime(s[: len(datetime.now().strftime(fmt))], fmt)
            return True
        except ValueError:
            continue
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return True
    return False


def _column_mostly_numeric(sample: list[dict], col: str) -> bool:
    vals = [row.get(col) for row in sample if row.get(col) is not None]
    if not vals:
        return False
    ok = sum(1 for v in vals if _is_number(v))
    return ok * 2 >= len(vals)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False
```

注意：`_looks_date` 实现时可简化为优先 `^\d{4}-\d{2}-\d{2}` + 列名规则，避免脆弱的 `strptime` 切片；以测试通过为准。

- [ ] **Step 4: Run tests to verify they pass**

```bash
$PY -m pytest tests/test_chart_planner.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

仅当用户明确要求时：

```bash
git add backend/app/agent/chart_planner.py backend/tests/test_chart_planner.py
git commit -m "$(cat <<'EOF'
feat(phase6): add chart_planner with LLM and heuristic fallback

EOF
)"
```

---

### Task 2: State + Executor 写字段 + ChartPlanner 节点 + Graph

**Files:**
- Modify: `backend/app/agent/state.py`
- Modify: `backend/app/agent/nodes/sql_executor_node.py`
- Create: `backend/app/agent/nodes/chart_planner.py`
- Modify: `backend/app/agent/graph.py`
- Modify: `backend/tests/test_agent_state.py`
- Modify: `backend/tests/test_graph_compile.py`
- Modify: `backend/tests/test_chart_planner.py`（追加节点测）

**Interfaces:**
- Consumes: `plan_chart` from Task 1
- Produces: state fields `chart`, `is_write`, `affected_rows`；节点名 `"ChartPlanner"`；图边 `SQLExecutor ok → ChartPlanner → AnswerComposer`

- [ ] **Step 1: Extend failing tests**

在 `test_agent_state.py` 增加字段赋值断言：

```python
        "chart": {"type": "bar", "x": "channel", "y": "gmv", "title": "t"},
        "is_write": False,
        "affected_rows": None,
```

在 `test_chart_planner.py` 追加：

```python
from unittest.mock import patch

from app.agent.nodes.chart_planner import chart_planner_node


def test_node_skips_on_write():
    with patch("app.agent.nodes.chart_planner.plan_chart") as m:
        out = chart_planner_node(
            {
                "question": "update",
                "is_write": True,
                "columns": [],
                "rows": [],
            }
        )
    m.assert_not_called()
    assert out == {"chart": None}


def test_node_skips_on_empty_rows():
    with patch("app.agent.nodes.chart_planner.plan_chart") as m:
        out = chart_planner_node(
            {
                "question": "q",
                "is_write": False,
                "columns": ["a"],
                "rows": [],
            }
        )
    m.assert_not_called()
    assert out == {"chart": None}


def test_node_calls_plan_chart():
    chart = {"type": "bar", "x": "a", "y": "b", "title": "t"}
    with patch(
        "app.agent.nodes.chart_planner.plan_chart",
        return_value=chart,
    ) as m:
        out = chart_planner_node(
            {
                "question": "q",
                "is_write": False,
                "columns": ["a", "b"],
                "rows": [{"a": "x", "b": 1}],
                "slots": {"metrics": ["gmv"]},
            }
        )
    m.assert_called_once()
    assert out == {"chart": chart}
```

在 `test_graph_compile.py` 追加：

```python
def test_graph_has_chart_planner_node():
    g = build_graph()
    # langgraph CompiledGraph exposes nodes via get_graph
    names = set(g.get_graph().nodes)
    assert "ChartPlanner" in names
```

若 `get_graph().nodes` API 与版本不符，改为 stream 一条 mock 路径断言 `node_end` 含 ChartPlanner（见 Task 3）。以实际 LangGraph API 为准。

- [ ] **Step 2: Run tests to verify they fail**

```bash
$PY -m pytest tests/test_chart_planner.py tests/test_agent_state.py tests/test_graph_compile.py -v
```

Expected: 节点/图相关 FAIL

- [ ] **Step 3: Update state**

`backend/app/agent/state.py` 在结果区增加：

```python
    chart: dict | None
    is_write: bool
    affected_rows: int | None
```

- [ ] **Step 4: Update SQLExecutor node**

```python
# backend/app/agent/nodes/sql_executor_node.py 成功分支
    data = result.data or {}
    if data.get("is_write"):
        out.update(
            {
                "columns": [],
                "rows": [],
                "is_write": True,
                "affected_rows": data.get("affected_rows"),
                "error": None,
            }
        )
    else:
        out.update(
            {
                "columns": data.get("columns") or [],
                "rows": data.get("rows") or [],
                "is_write": False,
                "affected_rows": None,
                "error": None,
            }
        )
```

- [ ] **Step 5: Create chart planner node**

```python
# backend/app/agent/nodes/chart_planner.py
from __future__ import annotations

from app.agent.chart_planner import plan_chart
from app.agent.state import AgentState


def chart_planner_node(state: AgentState) -> dict:
    if state.get("error") or state.get("is_write"):
        return {"chart": None}
    columns = state.get("columns") or []
    rows = state.get("rows") or []
    if not columns or not rows:
        return {"chart": None}
    chart = plan_chart(
        state.get("question") or "",
        columns,
        rows,
        slots=state.get("slots"),
    )
    return {"chart": chart}
```

- [ ] **Step 6: Rewire graph**

在 `graph.py`：

1. import `chart_planner_node`
2. `g.add_node("ChartPlanner", chart_planner_node)`
3. `_after_executor` 成功时返回 `"ChartPlanner"`（原 `"AnswerComposer"`）
4. conditional map 增加 `"ChartPlanner": "ChartPlanner"`
5. `g.add_edge("ChartPlanner", "AnswerComposer")`
6. 保留 `g.add_edge("AnswerComposer", "MemorySave")`

- [ ] **Step 7: Run tests**

```bash
$PY -m pytest tests/test_chart_planner.py tests/test_agent_state.py tests/test_graph_compile.py -v
```

Expected: PASS

- [ ] **Step 8: Commit（默认跳过）**

---

### Task 3: Pipeline SSE（`chart` / `write_result` / `done.repaired`）

**Files:**
- Modify: `backend/app/agent/pipeline.py`
- Create: `backend/tests/test_phase6_pipeline.py`

**Interfaces:**
- Consumes: graph ChartPlanner + Executor `is_write`/`affected_rows`
- Produces: SSE events `chart`, `write_result`；`done` 含 `repaired`

- [ ] **Step 1: Write failing pipeline tests**

```python
# backend/tests/test_phase6_pipeline.py
import json
from unittest.mock import patch

from app.agent.pipeline import iter_pipeline_events
from app.db.init_db import init_database


def _state(request_id: str, question: str, **extra):
    base = {
        "question": question,
        "session_id": "default",
        "user_id": "1",
        "user_role": "analyst",
        "request_id": request_id,
        "trace_id": request_id,
        "repaired": False,
    }
    base.update(extra)
    return base


def test_read_path_emits_chart(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "sales_analysis",
        "confidence": 0.9,
        "summary": "gmv by channel",
        "route_mode": "coordinator",
        "slots": {"metrics": ["gmv"], "group_by": ["channel"], "top_n": 5},
        "need_clarification": False,
        "clarification_question": None,
    }
    sql = (
        "SELECT channel AS channel, SUM(pay_amount) AS gmv "
        "FROM orders GROUP BY channel ORDER BY gmv DESC LIMIT 5"
    )
    chart = {
        "type": "bar",
        "x": "channel",
        "y": "gmv",
        "title": "渠道 GMV",
    }
    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.nodes.sql_generator_node.generate_sql",
            return_value=sql,
        ),
        patch(
            "app.agent.chart_planner.chat_completion",
            return_value=json.dumps(chart),
        ),
        patch(
            "app.agent.answer_composer.compose_answer",
            return_value="ok",
        ),
    ):
        events = list(
            iter_pipeline_events(_state("req_p6_chart", "各渠道 GMV Top5"))
        )

    assert any(e == "chart" for e, _ in events)
    chart_data = next(d for e, d in events if e == "chart")
    assert chart_data["type"] == "bar"
    assert "ChartPlanner" in [
        d["node"] for e, d in events if e == "node_end"
    ]
    done = next(d for e, d in events if e == "done")
    assert "repaired" in done
    assert done["repaired"] is False


def test_write_path_emits_write_result_no_chart(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "write_op",
        "confidence": 0.9,
        "summary": "update budget",
        "route_mode": "coordinator",
        "slots": {"write_intent": True},
        "need_clarification": False,
        "clarification_question": None,
    }
    # 使用业务表上合法 UPDATE；以仓库种子数据可执行为准
    sql = "UPDATE campaigns SET budget = budget WHERE campaign_id = 1"
    with (
        patch(
            "app.agent.nodes.intent_analyzer.chat_completion",
            return_value=json.dumps(intent_json),
        ),
        patch(
            "app.agent.nodes.sql_generator_node.generate_sql",
            return_value=sql,
        ),
        patch(
            "app.agent.answer_composer.compose_answer",
            return_value="写操作完成",
        ),
        patch(
            "app.agent.chart_planner.chat_completion",
        ) as chart_llm,
    ):
        events = list(
            iter_pipeline_events(
                _state(
                    "req_p6_write",
                    "更新活动预算",
                    user_role="admin",
                    user_id="2",
                )
            )
        )

    chart_llm.assert_not_called()
    assert not any(e == "chart" for e, _ in events)
    assert any(e == "write_result" for e, _ in events)
    wr = next(d for e, d in events if e == "write_result")
    assert "affected_rows" in wr
    assert wr.get("sql")
```

若种子表名/列名与 `campaigns` 不符，先查 `backend/app/db` 业务表实际 DDL，改用真实可写 SQL（仍须过 Guardrail）。也可在测试中 patch `sandbox_execute` / Registry `execute_sql` 返回写成功 data，避免依赖具体表结构——**优先 patch Registry 返回**：

```python
    fake_write = type("R", (), {})()  # 或用简单 namespace
```

更稳妥写法（推荐替换上测实现）：

```python
from app.tools.schemas import ToolResult


def test_write_path_emits_write_result_no_chart(tmp_db_path, monkeypatch):
    init_database(reset=True)
    # ... intent + generate_sql patches ...
    from app.tools import builtins as builtins_mod

    def fake_invoke(name, args, context=None):
        if name == "validate_sql":
            return ToolResult(ok=True, data={"ok": True}, events=[])
        if name == "execute_sql":
            return ToolResult(
                ok=True,
                data={
                    "affected_rows": 3,
                    "is_write": True,
                    "risk_level": "high",
                },
                events=[
                    {
                        "event": "tool_start",
                        "data": {"tool": "execute_sql", "risk_level": "high"},
                    },
                    {
                        "event": "tool_end",
                        "data": {
                            "tool": "execute_sql",
                            "status": "ok",
                            "risk_level": "high",
                        },
                    },
                ],
            )
        raise AssertionError(name)

    with patch(
        "app.agent.nodes.sql_executor_node.ensure_builtins_registered"
    ) as reg_factory, patch(
        "app.agent.nodes.sql_guardrail_node.ensure_builtins_registered",
        reg_factory,
    ):
        reg = reg_factory.return_value
        reg.invoke.side_effect = fake_invoke
        # 同时保留 intent/sql_generator/answer patches...
```

实现时以「能稳定绿」为准；若 guardrail 节点也走 registry，需一并 mock。可参考 `test_phase5_pipeline.py` 现有 admin 写测（若有）复用模式。

- [ ] **Step 2: Run to verify fail**

```bash
$PY -m pytest tests/test_phase6_pipeline.py -v
```

Expected: FAIL（无 `chart` / `write_result` / `repaired` in done）

- [ ] **Step 3: Update pipeline**

在 `_summarize` 增加：

```python
    if node == "ChartPlanner":
        ch = state.get("chart")
        return "skipped" if not ch else str(ch.get("type") or "table")
```

在 `iter_pipeline_events` 的节点循环中，于 `SQLExecutor` / `ChartPlanner` 处理后增加：

```python
                if node == "SQLExecutor" and merged.get("is_write"):
                    yield (
                        "write_result",
                        {
                            "affected_rows": merged.get("affected_rows"),
                            "sql": merged.get("generated_sql") or "",
                        },
                    )
                if node == "SQLExecutor" and merged.get("rows") is not None:
                    # 保持现有 rows 事件；写路径 rows=[] 也可推，前端可忽略空表
                    ...
                if node == "ChartPlanner" and merged.get("chart"):
                    yield ("chart", dict(merged["chart"]))
```

注意：现有代码在 `SQLExecutor` 且 `rows is not None` 时推 `rows`。写成功后 `rows=[]` 仍会推空 rows——可接受；或改为 `if not merged.get("is_write") and merged.get("rows") is not None`。**推荐写路径不推 `rows`**：

```python
                if (
                    node == "SQLExecutor"
                    and not merged.get("is_write")
                    and merged.get("rows") is not None
                ):
                    yield (
                        "rows",
                        {
                            "columns": merged.get("columns") or [],
                            "rows": merged.get("rows") or [],
                        },
                    )
```

`done` payload：

```python
        {
            "latency_ms": latency,
            "need_clarification": bool(merged.get("need_clarification")),
            "clarification_question": merged.get("clarification_question"),
            "repaired": bool(merged.get("repaired")),
        },
```

- [ ] **Step 4: Run tests**

```bash
$PY -m pytest tests/test_phase6_pipeline.py tests/test_phase5_pipeline.py tests/test_graph_pipeline.py -v
```

Expected: 全部 PASS（修复任何因边变更导致的旧测）

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 4: AnswerComposer 写操作文案 + `render_chart` 复用

**Files:**
- Modify: `backend/app/agent/answer_composer.py`
- Modify: `backend/app/agent/nodes/answer_composer_node.py`
- Modify: `backend/app/tools/builtins.py`
- Modify: `backend/tests/test_builtin_tools.py`
- Create or modify: `backend/tests/test_answer_composer.py`（若无则新建）

**Interfaces:**
- Consumes: `plan_chart`
- Produces: 写成功时确定性中文答案；Tool 输出与 planner 同形

- [ ] **Step 1: Failing tests**

```python
# backend/tests/test_answer_composer.py
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
```

更新 `test_render_chart_returns_config`：无 question 时仍 ok；可选断言不调用 LLM：

```python
def test_render_chart_uses_plan_chart_heuristic():
    from unittest.mock import patch
    from app.tools.builtins import ensure_builtins_registered

    reg = ensure_builtins_registered()
    with patch("app.agent.chart_planner.chat_completion") as m:
        out = reg.invoke(
            "render_chart",
            {
                "columns": ["channel", "gmv"],
                "rows": [{"channel": "app", "gmv": 1}],
                "title": "渠道 GMV",
            },
            context=_ctx(),
        )
    m.assert_not_called()
    assert out.ok
    assert out.data["type"] == "bar"
```

- [ ] **Step 2: Run to fail**

```bash
$PY -m pytest tests/test_answer_composer.py tests/test_builtin_tools.py::test_render_chart_uses_plan_chart_heuristic -v
```

- [ ] **Step 3: Implement**

`compose_answer` 签名扩展：

```python
def compose_answer(
    question: str,
    columns: list[str],
    rows: list[dict],
    *,
    is_write: bool = False,
    affected_rows: int | None = None,
) -> str:
    if is_write:
        n = affected_rows if affected_rows is not None else 0
        return f"写操作已成功执行，影响 {n} 行。"
    # 现有 LLM / fallback
```

`answer_composer_node`：

```python
    answer = answer_composer.compose_answer(
        state.get("question") or "",
        state.get("columns") or [],
        state.get("rows") or [],
        is_write=bool(state.get("is_write")),
        affected_rows=state.get("affected_rows"),
    )
```

`builtins._handle_render_chart`：

```python
def _handle_render_chart(args: dict, _context: ToolContext) -> ToolResult:
    from app.agent.chart_planner import plan_chart

    columns = list(args.get("columns") or [])
    rows = list(args.get("rows") or [])
    title = str(args.get("title") or "")
    question = str(args.get("question") or "")
    chart = plan_chart(question, columns, rows, title_hint=title)
    if chart is None:
        chart = {
            "type": "table",
            "x": columns[0] if columns else "",
            "y": columns[1] if len(columns) > 1 else "",
            "title": title,
        }
    elif title and not chart.get("title"):
        chart = {**chart, "title": title}
    return ToolResult(ok=True, data=chart)
```

可删除 builtins 内仅供旧 heuristic 使用的 `_looks_numeric`（若已无引用）。

- [ ] **Step 4: Run tests**

```bash
$PY -m pytest tests/test_answer_composer.py tests/test_builtin_tools.py tests/test_phase6_pipeline.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 5: 前端 Recharts + 工作台（图表 / 写提示 / Trace）

**Files:**
- Modify: `frontend/package.json`（及 lockfile）
- Create: `frontend/src/components/ResultChart.tsx`
- Modify: `frontend/src/pages/AppWorkbench.tsx`

**Interfaces:**
- Consumes: SSE `chart` `{type,x,y,title}`；`write_result` `{affected_rows,sql}`；现有 `rows`
- Produces: line/bar/pie 可视化；写操作横幅；Trace 条目

- [ ] **Step 1: Install recharts**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/frontend
npm install recharts@2.15.0
```

- [ ] **Step 2: Create `ResultChart.tsx`**

```tsx
// frontend/src/components/ResultChart.tsx
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

export type ChartConfig = {
  type: string
  x: string
  y: string
  title?: string
}

const PIE_COLORS = ['#0F766E', '#B45309', '#1D4ED8', '#BE123C', '#4338CA', '#047857']

type Props = {
  chart: ChartConfig | null
  rows: Record<string, unknown>[]
}

export default function ResultChart({ chart, rows }: Props) {
  if (!chart || chart.type === 'table' || !chart.x || !chart.y || rows.length === 0) {
    return null
  }

  const data = rows.map((row) => ({
    ...row,
    [chart.y]: toNumber(row[chart.y]),
  }))

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-xs font-medium uppercase tracking-wider text-muted">
        图表
        {chart.title ? ` · ${chart.title}` : ''}
      </h2>
      <div className="mt-3 h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {chart.type === 'line' ? (
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey={chart.x} tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey={chart.y}
                stroke="#0F766E"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          ) : chart.type === 'pie' ? (
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie
                data={data}
                dataKey={chart.y}
                nameKey={chart.x}
                outerRadius={100}
                label
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          ) : (
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey={chart.x} tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Bar dataKey={chart.y} fill="#0F766E" radius={[4, 4, 0, 0]} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function toNumber(value: unknown): number {
  if (typeof value === 'number') return value
  if (typeof value === 'string') {
    const n = Number(value)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}
```

颜色取现有 CSS 变量接近色即可；若 `--accent` 不同，用 `var` 不便给 Recharts 时保持常量即可。

- [ ] **Step 3: Wire AppWorkbench**

增量改动要点：

1. state：
   - `chart: ChartConfig | null`
   - `writeResult: { affected_rows: number | null; sql: string } | null`
2. `resetResult` 清空二者
3. `onEvent`：
   - `case 'chart':` 设置 chart + `pushTrace('chart', type)`
   - `case 'write_result':` 设置 writeResult + `pushTrace('write_result', \`写操作 · ${affected_rows} 行\`)`
4. 侧栏角色：将 `角色 · {role}` 改为徽章样式，例如：

```tsx
<span className="mt-1 inline-flex rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-accent">
  {user?.role}
</span>
```

不可加角色切换控件。

5. 结果区顺序（在 SQL section 之后）：

```tsx
{writeResult && (
  <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
    写操作已成功执行
    {writeResult.affected_rows != null
      ? ` · 影响 ${writeResult.affected_rows} 行`
      : ''}
  </div>
)}
{/* 现有表格 */}
<ResultChart chart={chart} rows={rows} />
{/* Trace */}
```

6. Trace：`tool_end` 若 `data.risk_level === 'high'`，summary 前缀 `⚠ high · `

7. import `ResultChart`

- [ ] **Step 4: Build verify**

```bash
npm run build
```

Expected: 成功，无 TS 错误

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 6: 登录页增量打磨

**Files:**
- Modify: `frontend/src/pages/LoginPage.tsx`

**Interfaces:**
- 无 API 变更；视觉增量

- [ ] **Step 1: 小幅加强品牌层次（不重做布局）**

在现有结构上调整，例如：

1. 价值主张文案补一句图表能力：`返回结论、明细表与自动图表`
2. 品牌 `h1` 字号在 `sm:` 上略增（如 `sm:text-[3.75rem]`），保持第一视口品牌主导
3. QueryTicker 第三条已是趋势问题，可把成功行改为 `✓ 已生成结果与图表 · {ms}ms`（仅文案）

不要：加卡片墙、stat strip、改路由、引入新字体包（沿用现有 `font-display`）。

- [ ] **Step 2: Build**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/frontend
npm run build
```

Expected: PASS

- [ ] **Step 3: Commit（默认跳过）**

---

### Task 7: Docs / README 同步 + 全量回归

**Files:**
- Modify: `docs/03-Agent设计.md`（共享尾环含 ChartPlanner；§2.9 可补「LLM + 启发式」一句）
- Modify: `docs/04-接口与前端.md`（事件表加 `write_result`；说明 `chart` / `done.repaired`）
- Modify: `README.md`（Phase 1–6；去掉「图表 UI 仍为 Phase 6」）

- [ ] **Step 1: Update docs/04 event table**

在事件表增加：

| `chart` | 图表配置 `{type,x,y,title}` |
| `write_result` | 写操作成功：`affected_rows` + `sql` |

`done` 说明含 `repaired`。

- [ ] **Step 2: Update docs/03**

主架构/尾环文字改为 Executor 成功后经 ChartPlanner 再到 AnswerComposer；§2.9 注明轻量 LLM + 启发式降级、主路径不经 Registry。

- [ ] **Step 3: Update README**

- 开篇 Phase 标记改为 1–6
- 能力列表加入：自动图表（line/bar/pie）、admin 写操作 UI 提示
- 删除「图表 UI 为 Phase 6」类句子

- [ ] **Step 4: Full backend regression**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
$PY -m pytest -v
```

Expected: PASS

- [ ] **Step 5: Frontend build**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/frontend
npm run build
```

Expected: PASS

- [ ] **Step 6: Commit（默认跳过）**

建议 message（用户自行提交时）：

```text
feat(phase6): ChartPlanner, Recharts, and write-result UI
```

---

## Self-Review

**1. Spec coverage**

| Design § | Task |
|----------|------|
| LLM ChartPlanner + heuristic | Task 1 |
| 节点短路写/空 | Task 2 |
| Graph 尾环顺序 | Task 2 |
| SSE chart/write_result/done.repaired | Task 3 |
| AnswerComposer 写文案 | Task 4 |
| render_chart 复用 | Task 4 |
| Recharts 三图型 + 工作台 | Task 5 |
| 登录页增量 | Task 6 |
| docs/README | Task 7 |
| 不做项（多 Y 轴/偏好覆盖/整页重做/Registry 主路径） | 未列入任务 |

**2. Placeholder scan:** 无 TBD；写路径测试允许按真实表或 mock Registry 二选一，已注明以稳定绿为准。

**3. Type consistency:** `chart` 四字段；`is_write`/`affected_rows`；SSE `write_result.affected_rows`；前端 `ChartConfig` 对齐。

---

## Execution Handoff

Plan complete and saved to `spec/2026-07-25-phase6-chart-frontend-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每 Task 新开 subagent，Task 间审查，迭代快

**2. Inline Execution** — 本会话按 executing-plans 连续执行，设检查点

Which approach?
