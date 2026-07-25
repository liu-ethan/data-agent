# Phase 3 LangGraph 节点拆分 + SSE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 LangGraph 单路径状态图替换 Phase 2 线性管线，落地 Intent / Clarification / SchemaRetriever / route_decision，并保持 SSE chat + 最小 Guardrail 闭环。

**Architecture:** `vocab`/`metrics` 为确定性词表与口径；各节点独立文件挂到 `StateGraph`；`pipeline.py` 将 `graph.stream(updates)` 映射为 SSE；前端 Trace 增加 `route_decision`；澄清走 `ClarificationReply` 提前 END。

**Tech Stack:** Python 3.12（conda `python3.12`）· FastAPI · LangGraph · langchain-core · openai · pytest · React · TypeScript

## Global Constraints

- 规格：`spec/2026-07-25-phase3-langgraph-sse-design.md`；产品：`docs/03`、`docs/04`、`docs/06` Phase 3
- 默认 chat SQL **必须**经 Guardrail；禁止旁路直连
- 不上 ComplexityRouter 子图 / Memory / SQLRepairer / Chart / Tool Registry / AuditLog / admin 写
- 配置仅用根目录 `config.yaml`；禁止 `.env`
- **Python（强制）**：`/home/user/miniconda3/envs/python3.12/bin/python` 与同目录 `pip`；下文记为 `PY` / `PIP`
- **禁止** git worktree / `.worktrees/` 做功能开发；只在本仓库工作区改代码
- **Git commit：仅当用户明确要求时执行**；本计划 Commit 步骤默认跳过
- TDD：vocab/metrics、clarification、schema_retriever、intent、graph/pipeline、chat 先写失败测试再实现

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/requirements.txt` | 增加 langgraph、langchain-core |
| `backend/app/agent/state.py` | AgentState TypedDict |
| `backend/app/agent/vocab.py` | intent / metric / dimension / time 词表 |
| `backend/app/agent/metrics.py` | metric key → 口径与所需表 |
| `backend/app/agent/nodes/*.py` | 各图节点 |
| `backend/app/agent/graph.py` | 编译 StateGraph |
| `backend/app/agent/pipeline.py` | SSE 事件适配 |
| `backend/app/agent/sql_generator.py` | 裁剪 Schema + metric_specs |
| `backend/app/api/chat.py` | 初始 state 改为 dict |
| `backend/app/api/examples.py` | 可选澄清示例 |
| `frontend/src/pages/AppWorkbench.tsx` | route_decision / 澄清提示 |
| `README.md` | Phase 1–3 状态与节点说明 |
| `backend/tests/test_*.py` | 见各 Task |

---

### Task 1: 依赖 + AgentState TypedDict

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/agent/state.py`
- Modify: `backend/app/api/chat.py`（仅改 state 构造为 dict，仍调用现有 pipeline，暂可保持旧管线能跑）
- Test: `backend/tests/test_agent_state.py`

**Interfaces:**
- Produces: `AgentState` TypedDict（字段见 design §4）
- Produces: `initial_state(...)` 辅助函数（可选，可内联在 chat）

- [ ] **Step 1: 更新依赖**

在 `backend/requirements.txt` 追加：

```text
langgraph>=0.2.0
langchain-core>=0.3.0
```

Run: `PIP=/home/user/miniconda3/envs/python3.12/bin/pip && $PIP install -r backend/requirements.txt`

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/test_agent_state.py
from app.agent.state import AgentState


def test_agent_state_accepts_phase3_fields():
    state: AgentState = {
        "question": "q",
        "session_id": "default",
        "user_id": "1",
        "user_role": "analyst",
        "request_id": "req_1",
        "trace_id": "req_1",
        "intent": "channel_analysis",
        "route_mode": "react",
        "route_source": "model",
        "slots": {"metrics": ["gmv"], "time_range": "last_month"},
        "need_clarification": False,
        "relevant_tables": ["orders"],
        "metric_specs": [],
        "repaired": False,
    }
    assert state["route_mode"] == "react"
    assert state["slots"]["metrics"] == ["gmv"]
```

- [ ] **Step 3: Run 确认失败**

Run: `cd backend && PY=/home/user/miniconda3/envs/python3.12/bin/python && $PY -m pytest tests/test_agent_state.py -v`  
Expected: FAIL（TypedDict 字段或导入不匹配）

- [ ] **Step 4: 实现 `state.py`**

用 `TypedDict, total=False` 实现 design §4 全部字段；删除旧 dataclass（调用方改为 dict）。

同步改 `api/chat.py`：

```python
state: AgentState = {
    "question": body.question,
    "session_id": body.session_id,
    "user_id": user["id"],
    "user_role": user["role"],
    "request_id": request_id,
    "trace_id": trace_id,
    "need_clarification": False,
    "repaired": False,
    "agent_trace": [],
}
```

若旧 `pipeline.py` 仍用属性访问，本 Task 末尾把 `pipeline` 改为 `state["question"]` 风格的最小改动，保证现有 chat 测试不炸；完整图替换在 Task 7。

- [ ] **Step 5: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_agent_state.py tests/test_chat_api.py -v`  
Expected: PASS（chat 可能仍走旧逻辑，但 state 为 dict）

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 2: vocab + metrics

**Files:**
- Create: `backend/app/agent/vocab.py`
- Create: `backend/app/agent/metrics.py`
- Create: `backend/tests/test_vocab_metrics.py`

**Interfaces:**
- Produces: `INTENTS: frozenset[str]`
- Produces: `METRIC_VOCAB: frozenset[str]`
- Produces: `DIMENSION_VOCAB: frozenset[str]`
- Produces: `TIME_RANGE_VOCAB: frozenset[str]`
- Produces: `get_metric_spec(key: str) -> dict | None`  
  shape: `{key, expression, tables: list[str], notes: str}`
- Produces: `is_known_metric(key: str) -> bool`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_vocab_metrics.py
from app.agent.metrics import get_metric_spec, is_known_metric
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
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_vocab_metrics.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现 vocab / metrics**

`vocab.py`：

```python
INTENTS = frozenset({
    "sales_analysis", "product_analysis", "user_analysis", "channel_analysis",
    "refund_analysis", "conversion_analysis", "payment_analysis", "write_op", "unknown",
})
METRIC_VOCAB = frozenset({
    "gmv", "order_count", "aov", "refund_rate", "conversion_rate",
    "payment_success_rate", "profit", "profit_rate",
})
DIMENSION_VOCAB = frozenset({
    "channel", "province", "city", "category", "brand", "payment_method",
})
TIME_RANGE_VOCAB = frozenset({
    "last_7d", "last_30d", "last_month", "last_quarter", "this_month", "last_90d",
})
```

`metrics.py`：为每个 METRIC_VOCAB key 提供 `expression` / `tables` / `notes`，对齐 docs/03 §2.4（表达式用可读 SQL 片段字符串即可）。

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_vocab_metrics.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 3: ClarificationChecker

**Files:**
- Create: `backend/app/agent/nodes/__init__.py`
- Create: `backend/app/agent/nodes/clarification_checker.py`
- Create: `backend/tests/test_clarification_checker.py`

**Interfaces:**
- Produces: `clarification_checker(state: AgentState) -> dict`  
  返回至少 `need_clarification: bool`, `clarification_question: str | None`

- [ ] **Step 1: 写失败测试**

```python
from app.agent.nodes.clarification_checker import clarification_checker


def test_vague_best_needs_clarification():
    out = clarification_checker({
        "question": "最近哪个渠道表现最好？",
        "slots": {"metrics": [], "time_range": None, "group_by": ["channel"]},
        "need_clarification": False,
        "clarification_question": None,
    })
    assert out["need_clarification"] is True
    assert out["clarification_question"]
    assert "指标" in out["clarification_question"] or "GMV" in out["clarification_question"]


def test_clear_gmv_no_clarification():
    out = clarification_checker({
        "question": "上个月 GMV 最高的 5 个渠道是什么？",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "top_n": 5,
        },
        "need_clarification": False,
        "clarification_question": None,
    })
    assert out["need_clarification"] is False


def test_unknown_metric_needs_clarification():
    out = clarification_checker({
        "question": "看看用户质量",
        "slots": {"metrics": ["user_quality"], "time_range": "last_30d"},
        "need_clarification": False,
    })
    assert out["need_clarification"] is True
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_clarification_checker.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现规则**

逻辑（确定性）：

1. 若 Intent 已 `need_clarification` 且有问句 → 保留或略收紧
2. `metrics` 为空且问题匹配 `表现|最好|不错|怎么样` → 澄清指标（可附带时间）
3. `time_range` 空且问题含 `最近`（且无 last_* 词）→ 澄清时间
4. 任一 metric 不在 `METRIC_VOCAB` → 澄清
5. 否则 `need_clarification=False`, `clarification_question=None`

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_clarification_checker.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 4: SchemaRetriever

**Files:**
- Create: `backend/app/agent/nodes/schema_retriever.py`
- Create: `backend/tests/test_schema_retriever.py`

**Interfaces:**
- Consumes: `build_schema_tables(role)`（可复用后按表名过滤）
- Produces: `schema_retriever(state: AgentState) -> dict`  
  返回 `relevant_tables`, `relevant_columns`（`dict[str, list[str]]` 或与 design 一致的结构）, `metric_specs`

约定 `relevant_columns` 形状：`{ "orders": ["id", "pay_amount", "channel", ...], ... }`（列名列表）。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_schema_retriever.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现**

- intent → 默认表集（如 channel/sales → `orders`；refund → `orders`,`refunds`；conversion → `traffic_logs`；payment → `payments`,`orders`；product/profit → `products`,`order_items`,`orders`）
- 并集：各 metric 的 `tables` + dimension 映射表
- 从 `build_schema_tables(user_role)` 取列，只保留相关表；再按 dimension 确保关键列在列清单中（若 PRAGMA 有）
- `metric_specs = [get_metric_spec(m) for m in slots.metrics if get_metric_spec(m)]`

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_schema_retriever.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 5: IntentAnalyzer

**Files:**
- Create: `backend/app/agent/nodes/intent_analyzer.py`
- Create: `backend/tests/test_intent_analyzer.py`

**Interfaces:**
- Produces: `intent_analyzer(state: AgentState) -> dict`
- Produces: `build_intent_prompt(question: str) -> list[dict]`（便于测 prompt 不含 Schema）

- [ ] **Step 1: 写失败测试**

```python
import json
from unittest.mock import patch

from app.agent.nodes.intent_analyzer import build_intent_prompt, intent_analyzer


def test_prompt_has_no_full_schema():
    messages = build_intent_prompt("上个月 GMV 最高的渠道？")
    blob = json.dumps(messages, ensure_ascii=False)
    assert "id_card" not in blob
    assert "CREATE TABLE" not in blob
    assert "order_items" not in blob  # 业务表名不应整表灌入 Intent
    assert "METRIC" in blob.upper() or "gmv" in blob
    assert "channel_analysis" in blob


def test_intent_parses_llm_json():
    payload = {
        "intent": "channel_analysis",
        "confidence": 0.9,
        "summary": "渠道 GMV Top",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "top_n": 5,
            "write_intent": False,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(payload, ensure_ascii=False),
    ):
        out = intent_analyzer({"question": "上个月 GMV 最高的 5 个渠道是什么？"})
    assert out["intent"] == "channel_analysis"
    assert out["route_mode"] == "react"
    assert out["slots"]["metrics"] == ["gmv"]
    assert out["intent_confidence"] == 0.9


def test_intent_bad_json_fallback():
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value="not-json",
    ):
        out = intent_analyzer({"question": "随便问问"})
    assert out["intent"] == "unknown"
    assert out["route_mode"] == "react"
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_intent_analyzer.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现**

- `build_intent_prompt`：system 含 intent 枚举、METRIC/DIMENSION/TIME 词表、route_mode 说明、JSON schema；user = question
- `chat_completion` 调用后抽 JSON（允许 ```json fence）
- 校验 intent ∈ INTENTS，否则 `unknown`；route_mode ∈ {react,coordinator}，否则 `react`
- 映射输出字段名到 state：`confidence`→`intent_confidence`，`summary`→`intent_summary`

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_intent_analyzer.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 6: 其余节点 + SQLGenerator 改造 + Graph 编译

**Files:**
- Create: `backend/app/agent/nodes/route_emit.py`
- Create: `backend/app/agent/nodes/clarification_reply.py`
- Create: `backend/app/agent/nodes/sql_generator_node.py`
- Create: `backend/app/agent/nodes/sql_guardrail_node.py`
- Create: `backend/app/agent/nodes/sql_executor_node.py`
- Create: `backend/app/agent/nodes/answer_composer_node.py`
- Modify: `backend/app/agent/sql_generator.py`
- Create: `backend/app/agent/graph.py`
- Create: `backend/tests/test_graph_compile.py`

**Interfaces:**
- Produces: `build_graph() -> CompiledGraph`（`langgraph` compile 结果）
- Produces: 各 `*_node(state) -> dict`
- Modifies: `generate_sql(question, relevant_tables_schema, metric_specs, slots, user_role) -> str`

- [ ] **Step 1: 写失败测试**

```python
from app.agent.graph import build_graph


def test_graph_compiles():
    g = build_graph()
    assert g is not None


def test_route_emit_defaults():
    from app.agent.nodes.route_emit import route_emit
    out = route_emit({"route_mode": None})
    assert out["route_mode"] == "react"
    assert out["route_source"] == "model"


def test_clarification_reply_sets_answer():
    from app.agent.nodes.clarification_reply import clarification_reply
    out = clarification_reply({"clarification_question": "请说明指标"})
    assert out["answer"] == "请说明指标"
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_graph_compile.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现节点与 graph**

`route_emit`：

```python
def route_emit(state):
    mode = state.get("route_mode") or "react"
    if mode not in ("react", "coordinator"):
        mode = "react"
    return {"route_mode": mode, "route_source": "model"}
```

`clarification_reply`：`{"answer": state.get("clarification_question") or "请补充指标与时间范围后再问我。"}`

`sql_generator_node`：把 `relevant_tables`/`relevant_columns` 拼成 schema list，调用改造后的 `generate_sql`。

`sql_guardrail_node`：

```python
def sql_guardrail_node(state):
    from app.security.sql_guardrail import check_sql
    sql = state.get("generated_sql") or ""
    result = check_sql(sql, user_role=state["user_role"])
    if not result.ok:
        return {"error": result.reason or "SQL blocked by guardrail"}
    return {"error": None}
```

`sql_executor_node`：调用 `execute_sql`；成功写 `columns`/`rows`；异常写 `error`。

`answer_composer_node`：调用 `compose_answer`；若已有 `error` 可跳过（图上不应到达）。

改造 `generate_sql` 签名与 prompt：只注入裁剪 schema JSON + `metric_specs` + slots，要求 SELECT/WITH。

`graph.py`：

```python
from langgraph.graph import END, START, StateGraph
from app.agent.state import AgentState
# import all node callables

def _after_route(state: AgentState) -> str:
    if state.get("need_clarification"):
        return "ClarificationReply"
    return "SchemaRetriever"

def _after_guardrail(state: AgentState) -> str:
    if state.get("error"):
        return END
    return "SQLExecutor"

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("IntentAnalyzer", intent_analyzer)
    g.add_node("ClarificationChecker", clarification_checker)
    g.add_node("RouteEmit", route_emit)
    g.add_node("ClarificationReply", clarification_reply)
    g.add_node("SchemaRetriever", schema_retriever)
    g.add_node("SQLGenerator", sql_generator_node)
    g.add_node("SQLGuardrail", sql_guardrail_node)
    g.add_node("SQLExecutor", sql_executor_node)
    g.add_node("AnswerComposer", answer_composer_node)
    g.add_edge(START, "IntentAnalyzer")
    g.add_edge("IntentAnalyzer", "ClarificationChecker")
    g.add_edge("ClarificationChecker", "RouteEmit")
    g.add_conditional_edges("RouteEmit", _after_route, {
        "ClarificationReply": "ClarificationReply",
        "SchemaRetriever": "SchemaRetriever",
    })
    g.add_edge("ClarificationReply", END)
    g.add_edge("SchemaRetriever", "SQLGenerator")
    g.add_edge("SQLGenerator", "SQLGuardrail")
    g.add_conditional_edges("SQLGuardrail", _after_guardrail, {
        "SQLExecutor": "SQLExecutor",
        END: END,
    })
    g.add_edge("SQLExecutor", "AnswerComposer")
    g.add_edge("AnswerComposer", END)
    return g.compile()
```

（若所用 langgraph 版本对 `END` 作 conditional 映射语法不同，按安装版文档微调，保持语义不变。）

- [ ] **Step 4: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_graph_compile.py tests/test_vocab_metrics.py tests/test_clarification_checker.py tests/test_schema_retriever.py tests/test_intent_analyzer.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 7: pipeline SSE 适配 + 图路径测试

**Files:**
- Modify: `backend/app/agent/pipeline.py`
- Create: `backend/tests/test_graph_pipeline.py`
- Modify: `backend/tests/test_chat_api.py`
- Modify: `backend/app/api/chat.py`（若仍需）

**Interfaces:**
- Produces: `iter_pipeline_events(state: AgentState) -> Iterator[tuple[str, dict]]`  
  事件名与 design §7.1 一致

- [ ] **Step 1: 写失败测试 `test_graph_pipeline.py`**

```python
import json
from unittest.mock import patch

from app.agent.pipeline import iter_pipeline_events
from app.db.init_db import init_database


def _events(state):
    return list(iter_pipeline_events(state))


def test_clarification_path_no_sql(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.7,
        "summary": "模糊渠道表现",
        "route_mode": "react",
        "slots": {"metrics": [], "time_range": None, "group_by": ["channel"]},
        "need_clarification": True,
        "clarification_question": "想按 GMV 还是订单量？时间用近 7 天还是 30 天？",
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ):
        events = _events({
            "question": "最近哪个渠道表现最好？",
            "session_id": "default",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_c",
            "trace_id": "req_c",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })
    names = [e for e, _ in events]
    assert "route_decision" in names
    assert "sql" not in names
    assert "rows" not in names
    assert any(e == "answer" for e in names)
    done = next(d for e, d in events if e == "done")
    assert done["need_clarification"] is True


def test_happy_path_events(tmp_db_path):
    init_database(reset=True)
    intent_json = {
        "intent": "channel_analysis",
        "confidence": 0.9,
        "summary": "渠道 GMV",
        "route_mode": "react",
        "slots": {
            "metrics": ["gmv"],
            "time_range": "last_month",
            "group_by": ["channel"],
            "top_n": 5,
            "write_intent": False,
        },
        "need_clarification": False,
        "clarification_question": None,
    }
    with patch(
        "app.agent.nodes.intent_analyzer.chat_completion",
        return_value=json.dumps(intent_json, ensure_ascii=False),
    ), patch(
        "app.agent.sql_generator.generate_sql",
        return_value=(
            "SELECT channel, SUM(pay_amount) AS gmv FROM orders "
            "GROUP BY channel ORDER BY gmv DESC LIMIT 5"
        ),
    ), patch(
        "app.agent.answer_composer.compose_answer",
        return_value="渠道 A 领先",
    ):
        events = _events({
            "question": "上个月 GMV 最高的 5 个渠道是什么？",
            "session_id": "default",
            "user_id": "1",
            "user_role": "analyst",
            "request_id": "req_h",
            "trace_id": "req_h",
            "need_clarification": False,
            "repaired": False,
            "agent_trace": [],
        })
    by = {e: d for e, d in events}
    assert "run_start" in by
    assert by["route_decision"]["route_mode"] == "react"
    assert by["route_decision"]["route_source"] == "model"
    assert "sql" in by
    assert "rows" in by
    assert by["answer"]["text"] == "渠道 A 领先"
    assert by["done"]["need_clarification"] is False
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && $PY -m pytest tests/test_graph_pipeline.py -v`  
Expected: FAIL

- [ ] **Step 3: 重写 `pipeline.py`**

推荐实现（可测、不依赖冷门 callback）：

```python
def iter_pipeline_events(state: AgentState) -> Iterator[tuple[str, dict]]:
    started = time.monotonic()
    yield ("run_start", {
        "request_id": state["request_id"],
        "trace_id": state["trace_id"],
        "session_id": state["session_id"],
    })
    graph = build_graph()
    merged: dict = dict(state)
    try:
        for update in graph.stream(merged, stream_mode="updates"):
            for node, delta in update.items():
                yield ("node_start", {"node": node})
                if isinstance(delta, dict):
                    merged.update(delta)
                summary = _summarize(node, merged)
                yield ("node_end", {"node": node, "summary": summary})
                if node == "RouteEmit":
                    yield ("route_decision", {
                        "route_mode": merged.get("route_mode"),
                        "route_source": merged.get("route_source"),
                    })
                if node == "SQLGenerator" and merged.get("generated_sql"):
                    yield ("sql", {"sql": merged["generated_sql"], "repaired": False})
                if node == "SQLGuardrail" and merged.get("error"):
                    yield ("error", {"message": merged["error"]})
                if node == "SQLExecutor" and merged.get("rows") is not None:
                    yield ("rows", {
                        "columns": merged.get("columns") or [],
                        "rows": merged.get("rows") or [],
                    })
                if node in ("AnswerComposer", "ClarificationReply") and merged.get("answer"):
                    yield ("answer", {"text": merged["answer"]})
    except Exception as exc:
        yield ("error", {"message": str(exc)})
    latency = int((time.monotonic() - started) * 1000)
    merged["latency_ms"] = latency
    yield ("done", {
        "latency_ms": latency,
        "need_clarification": bool(merged.get("need_clarification")),
        "clarification_question": merged.get("clarification_question"),
    })
```

`_summarize`：按节点返回短字符串（如 Intent → intent 名；Guardrail → passed/rejected）。

删除旧「直接 SQLGenerator→Guardrail」线性路径。

- [ ] **Step 4: 更新 `test_chat_api.py`**

快乐路径需 mock `intent_analyzer.chat_completion`（或 `nodes.intent_analyzer.chat_completion`）+ `generate_sql` + `compose_answer`；断言含 `route_decision`。

增加澄清用例：模糊问题 → SSE 含 `need_clarification`、无 `event: sql`。

- [ ] **Step 5: Run 确认通过**

Run: `cd backend && $PY -m pytest tests/test_graph_pipeline.py tests/test_chat_api.py tests/test_sql_guardrail.py tests/test_auth.py -v`  
Expected: PASS

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 8: 前端 Trace + 示例 + README

**Files:**
- Modify: `frontend/src/pages/AppWorkbench.tsx`
- Modify: `backend/app/api/examples.py`（追加 1–2 条澄清向示例）
- Modify: `README.md`
- Optional: `docs/04-接口与前端.md`（若事件表缺 `route_decision` 已描述则只核对）

**Interfaces:**
- Consumes: SSE `route_decision` / `done.need_clarification`

- [ ] **Step 1: 工作台事件处理**

在 `onEvent` switch 增加：

```typescript
case 'route_decision':
  pushTrace(
    event,
    `${String(data.route_mode ?? '')} · ${String(data.route_source ?? '')}`,
  )
  break
```

在 `done` 分支：若 `data.need_clarification === true`，可 `setError(null)` 并设一短状态文案（如 `clarificationHint`），回答区依赖已有 `answer` 事件即可。

- [ ] **Step 2: examples 追加**

在 `QUESTIONS` 末尾加：

```python
"最近哪个渠道表现最好？",
"用户质量怎么样？",
```

（总数仍 ≥15。）

- [ ] **Step 3: README**

更新状态说明为 Phase 1–3：

- 已用 LangGraph 单路径节点图（Intent → Clarification → RouteEmit → Schema → SQL → Guardrail → Executor → Answer）
- `intent` ≠ `route_mode`；本阶段 `route_source=model`，双模式子图 Phase 5
- 模糊问题澄清后不跑 SQL
- 完整沙箱 / AuditLog / 图表仍为后续 Phase

架构文字与「线性管线」表述改为状态图。

- [ ] **Step 4: 前端类型检查 / 构建（若项目有）**

Run: `cd frontend && npm run build`  
Expected: 成功

- [ ] **Step 5: 全量后端回归**

Run: `cd backend && $PY -m pytest -v`  
Expected: PASS

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 9: 验收自检

**Files:** 无新文件（手工 + 测试）

- [ ] **Step 1: 对照 design §11 / docs/06 Phase 3 清单**

| 项 | 验证方式 |
|----|----------|
| 每次请求有 Trace | 前端或 SSE 含多条 `node_*` |
| Intent 产出 intent/slots/route_mode | unit + SSE `route_decision` |
| Intent 无全库 Schema | `test_prompt_has_no_full_schema` |
| SchemaRetriever 映射口径 | `test_channel_gmv_schema` |
| 澄清不跑 SQL | `test_clarification_path_no_sql` |
| README 已更新 | 人工读 README 状态段 |

- [ ] **Step 2: 可选真实 LLM 联调**

配置 `config.yaml` 的 `llm.*` 后启动前后端，跑 ≥5 个示例 + 1 个模糊澄清问题。

- [ ] **Step 3: 向用户汇报**

说明：测试结果、验收对照、建议 commit message（例如 `feat: Phase 3 LangGraph nodes and SSE route_decision`）。**不要**自动 commit。

---

## Self-Review

1. **Spec coverage:** AgentState、vocab/metrics、Intent、Clarification、RouteEmit、ClarificationReply、SchemaRetriever、SQL 路径、SSE、前端、README、测试 — 均有 Task。
2. **Placeholder scan:** 无 TBD；langgraph conditional `END` 语法允许按版本微调但语义固定。
3. **Type consistency:** 节点名字符串与 design §7.1 一致；state 为 TypedDict/dict；`generate_sql` 新签名在 Task 6 统一，Task 7 mock 同一路径。

---

## Execution Handoff

Plan complete and saved to `spec/2026-07-25-phase3-langgraph-sse-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每 Task 派生子代理，Task 间复查  

**2. Inline Execution** — 本会话按 executing-plans 连续做，设检查点  

Which approach?
