# Phase 3：LangGraph 节点拆分 + SSE — Design Spec

> 产品规格以 `docs/` 为准；本文只描述 Phase 3 如何落地。  
> 对齐：`docs/06-开发计划.md` Phase 3、`docs/03-Agent设计.md`、`docs/04-接口与前端.md`。  
> 承接：`spec/2026-07-25-phase2-auth-chat-design.md`（JWT、最小 Guardrail、SSE chat、工作台）。

## 1. 已确认决策

| 决策 | 选择 |
|------|------|
| 主图路径 | IntentAnalyzer → ClarificationChecker → RouteEmit →（澄清则 ClarificationReply→END｜否则 SchemaRetriever → SQLGenerator → SQLGuardrail → SQLExecutor → AnswerComposer） |
| 分流 | 只透传 IntentAnalyzer 的 `route_mode`，`route_source=model`；不上 ComplexityRouter / ReAct / Coordinator 子图 |
| 澄清 UI | 澄清问句作为主 `answer`；`done.need_clarification=true`；不跑 SQL、无 `rows` |
| SchemaRetriever | 纯代码：intent + slots → 相关表/列 + metric 口径映射 |
| 编排 | LangGraph `StateGraph` + 薄 SSE 适配层（`pipeline.py`） |
| SQL 安全 | 继续 Phase 2 最小只读 Guardrail + 受控执行；无旁路；不做 Tool Registry / 完整沙箱 / AuditLog |
| LLM 客户端 | 继续现有 `openai` SDK（`app.agent.llm`）；不改用 LangChain ChatModel 包装（除非适配层必要） |
| 依赖 | 增加 `langgraph` + 必要的 `langchain-core` |

## 2. 范围

### 做

- 扩展 `AgentState`（含 `user_id` / `request_id` / `trace_id` / `route_mode` / `slots` 等）
- 接入 LangGraph 状态图；Phase 5 前单路径执行，仍产出并记录 `route_mode`
- 实现 IntentAnalyzer、ClarificationChecker、SchemaRetriever、SQLGenerator、AnswerComposer（后两者在 Phase 2 基础上改造）
- 代码维护 `METRIC_VOCAB` / `DIMENSION_VOCAB`（`agent/vocab.py`）与 metric 口径表（`agent/metrics.py`）
- `POST /api/chat` SSE 推送 `node_*` / `route_decision` / `sql` / `answer` / `done`（及既有 `rows` / `error`）
- 前端 Trace 展示 `route_decision`；澄清问句出现在回答区
- README 说明 LangGraph 主图、单路径现状、intent≠route_mode、节点职责

### 不做

- ComplexityRouter 规则硬覆盖与双模式子图（Phase 5）
- Memory 读/写、SQLRepairer、ChartPlanner
- Tool Registry、完整 SQL 沙箱、`logs/audit.jsonl`
- admin 受控写 SQL、`tool_*` / `chart` / `token` SSE 事件
- 工作台视觉精修（Phase 6）

## 3. 目录结构（相对 Phase 2 增量）

```text
backend/app/agent/
├── state.py                 # AgentState TypedDict（LangGraph）
├── vocab.py                 # intent 枚举、METRIC/DIMENSION/TIME 词表
├── metrics.py               # metric key → SQL 口径与所需表列
├── graph.py                 # 编译 StateGraph + 条件边
├── pipeline.py              # SSE 适配：graph.stream → 事件序列
├── llm.py                   # 沿用
├── sql_generator.py         # 改为吃裁剪 Schema + metric_specs
├── sql_executor.py          # 沿用（节点包装调用）
├── answer_composer.py       # 沿用；澄清路径由 pipeline/节点写 answer
└── nodes/
    ├── __init__.py
    ├── intent_analyzer.py
    ├── clarification_checker.py
    ├── route_emit.py
    ├── clarification_reply.py   # answer = clarification_question
    ├── schema_retriever.py
    ├── sql_generator_node.py    # 薄包装调用 sql_generator.generate_sql
    ├── sql_guardrail_node.py
    ├── sql_executor_node.py
    └── answer_composer_node.py

backend/tests/
├── test_intent_analyzer.py
├── test_clarification_checker.py
├── test_schema_retriever.py
├── test_vocab_metrics.py
├── test_graph_pipeline.py       # 澄清提前结束、route_decision、mock LLM 全路径
└── test_chat_api.py             # 扩展 SSE 事件断言
```

说明：

- Guardrail 模块仍在 `security/sql_guardrail.py`；节点只调用，不内嵌权限逻辑
- 现有顶层 `sql_generator.py` / `answer_composer.py` 可保留为可测纯函数，节点文件做薄包装
- 删除或收缩「线性直连全库 Schema」的旧管线路径；默认 chat 只走图

## 4. AgentState

使用单一 `TypedDict`（`total=False` 可选字段）供 LangGraph；`api/chat.py` 构造初始必填字段。

```python
class AgentState(TypedDict, total=False):
    # 必填（入口注入）
    question: str
    session_id: str
    user_id: str
    user_role: str
    request_id: str
    trace_id: str

    # 意图与分流
    intent: str | None
    intent_confidence: float | None
    intent_summary: str | None
    route_mode: str | None          # "react" | "coordinator"
    route_source: str | None        # Phase 3 恒为 "model"
    slots: dict | None

    # 澄清
    need_clarification: bool
    clarification_question: str | None

    # Schema / SQL / 结果
    relevant_tables: list[str]
    relevant_columns: dict
    metric_specs: list[dict]        # [{key, expression, tables, notes}, ...]
    generated_sql: str | None
    columns: list[str]
    rows: list[dict]
    answer: str | None
    error: str | None

    agent_trace: list[dict]
    latency_ms: int
    repaired: bool                  # Phase 3 恒 False；预留
```

约定：

- `trace_id` 可与 `request_id` 相同（与 Phase 2 一致），字段分开保留
- 记忆字段（`session_slots` / `user_preferences` / `recent_summaries`）本阶段不写入
- `agent_trace` 可由适配层根据 SSE 事件追加，节点可不直接推前端

## 5. 主图流转

```text
START
  → IntentAnalyzer
  → ClarificationChecker
  → RouteEmit                     # 补全 route_mode；route_source="model"
       ├─ need_clarification      → ClarificationReply（answer=澄清问句）→ END
       └─ else
            → SchemaRetriever
            → SQLGenerator
            → SQLGuardrail
                 ├─ reject → END（error 已写入）
                 └─ ok → SQLExecutor → AnswerComposer → END
```

条件边：

1. `after_route_emit`：`need_clarification` → `ClarificationReply` → END；否则 → SchemaRetriever
2. `after_guardrail`：失败 → END；成功 → SQLExecutor

异常：节点抛错时适配层捕获，发 `error` + `done`，并尽量补 `node_end(summary=failed)`。

## 6. 节点设计

### 6.1 IntentAnalyzer

- 一次轻量 LLM 调用，要求严格 JSON 输出（可用 prompt 约束 + `json.loads`；失败走兜底）
- Prompt **只含**：intent 枚举说明、slots 词表、route_mode 提示、输出 schema、用户问题
- **禁止**：全量业务表字段、样例行
- 成功字段：`intent`, `intent_confidence`, `intent_summary`, `route_mode`, `slots`, `need_clarification`, `clarification_question`
- 失败 / 非法枚举：`intent=unknown`, `route_mode=react`, `slots` 空或可解析子集, `route_source` 稍后由 route_emit 设为 `model`

intent 封闭枚举（与 docs/03 一致）：

`sales_analysis` | `product_analysis` | `user_analysis` | `channel_analysis` | `refund_analysis` | `conversion_analysis` | `payment_analysis` | `write_op` | `unknown`

### 6.2 ClarificationChecker（确定性）

在 Intent 结果上二次判定，可确认或收紧：

| 条件 | 动作 |
|------|------|
| `slots.metrics` 为空，且问题含「最好 / 表现 / 不错」等模糊评价 | 需澄清（指标） |
| `time_range` 为空，且仅有「最近」类模糊时间、无默认可用 | 需澄清（时间） |
| metric 落在词表外且无法映射 | 需澄清 |
| 常见指标（如 gmv）有默认口径 | **不**因口径细节澄清 |

输出：更新 `need_clarification` / `clarification_question`（问句点名缺失项）。

澄清为真时：不进入 Schema/SQL；`answer` = 澄清问句。

### 6.3 route_emit

- 若 `route_mode` 缺失 → `react`
- `route_source = "model"`（本阶段不覆盖）
- SSE 适配层在此节点结束后发 `route_decision`

### 6.4 SchemaRetriever（纯代码）

输入：`intent`, `slots`, `user_role`。

输出：

- `relevant_tables` / `relevant_columns`：按 intent 默认表集 ∪ metric 所需表 ∪ dimension 映射列；**不含应用表**；analyst 隐藏敏感列元数据（与 `/api/schema` 一致）
- `metric_specs`：slots.metrics → 口径（对齐 docs/03 §2.4）

指标口径（第一版）：

| key | expression（概念） |
|-----|-------------------|
| gmv | `sum(orders.pay_amount)`（默认已支付口径在 notes 注明） |
| order_count | `count(distinct orders.id)` |
| aov | gmv / order_count |
| refund_rate | 退款订单数 / 订单数（同窗） |
| payment_success_rate | 成功支付 / 全部支付 |
| conversion_rate | traffic_logs 转化口径 |
| profit / profit_rate | order_items ⋈ products |

dimension → 列：`channel`→`orders.channel`（或 users.channel 按 intent）、`province`/`city`→orders、`category`/`brand`→products、`payment_method`→payments。

无 metric 且未澄清（边界情况）：按 intent 给最小默认表集（如 `orders`），由 SQLGenerator 尽力生成；优先仍应被 Clarification 拦住模糊问法。

### 6.5 SQLGenerator

- 输入：问题 + 裁剪 schema + `metric_specs` + `slots` + `user_role`
- 只输出一条 `SELECT`/`WITH`；不解释
- **不再**注入全库 8 表完整字段列表

### 6.6 SQLGuardrail / SQLExecutor / AnswerComposer

- 行为与 Phase 2 相同：Guardrail 失败不执行；执行最多 100 行；Answer 基于结果，失败模板兜底
- 挂为图节点，便于 Trace 与 Phase 5 接入

## 7. SSE 适配

`api/chat.py` 仍构造初始 state，调用 `iter_pipeline_events(state)`。

`pipeline.py`：

1. `yield run_start`
2. `graph.stream(state, stream_mode="updates")`（或等价），对每个节点：
   - `node_start` → 执行（由 stream 边界推断）→ `node_end`（summary 由状态摘要）
3. 副作用事件：
   - `route_emit` 后 → `route_decision`
   - 有 `generated_sql` 且新产生 → `sql`
   - Guardrail 失败 → `error`
   - 有 `rows` → `rows`
   - 有 `answer` → `answer`
4. 计算 `latency_ms` → `done`（含 `need_clarification` / `clarification_question`）

实现细节允许用「节点前后手动 yield」包装 `invoke` 单步，只要事件序与下表一致、可测即可；不必强绑某种 LangGraph callback API。

### 7.1 事件表（Phase 3）

| event | data |
|-------|------|
| `run_start` | `request_id`, `trace_id`, `session_id` |
| `node_start` / `node_end` | `node` 为节点名；`node_end` 可带 `summary` |
| `route_decision` | `route_mode`, `route_source` |
| `sql` | `sql`, `repaired`（恒 false） |
| `rows` | `columns`, `rows` |
| `answer` | `text`（结论或澄清问句） |
| `error` | `message`（脱敏） |
| `done` | `latency_ms`, `need_clarification`, `clarification_question` |

节点名字符串（固定）：`IntentAnalyzer` | `ClarificationChecker` | `RouteEmit` | `ClarificationReply` | `SchemaRetriever` | `SQLGenerator` | `SQLGuardrail` | `SQLExecutor` | `AnswerComposer`。

不发：`tool_*` / `chart` / `token`。

### 7.2 澄清路径事件序（示例）

```text
run_start
node_start/end IntentAnalyzer
node_start/end ClarificationChecker
node_start/end RouteEmit
route_decision
node_start/end ClarificationReply
answer          # 澄清问句
done            # need_clarification=true
```

无 `sql` / `rows`。

## 8. 前端

小改 `AppWorkbench.tsx`（及必要时 Trace 文案）：

- 处理 `route_decision`：Trace 显示如 `react · model`
- `done.need_clarification === true`：主回答区已有 `answer` 即可；可选短提示「需要补充信息后继续」
- 不重做布局/主题

可选：`GET /api/examples` 增加 1–2 条易触发澄清的示例（如「最近哪个渠道表现最好？」）。

## 9. 依赖

`backend/requirements.txt` 增加：

- `langgraph`（选型版本：实现时选当前稳定版，写入精确下限如 `langgraph>=0.2`）
- `langchain-core`（若 langgraph 未完整带上）

不引入完整 `langchain` 代理栈、向量库、额外 Agent 框架。

## 10. 测试（TDD）

| 区域 | 用例要点 |
|------|----------|
| vocab / metrics | 词表 key 与口径表对齐；未知 metric 可检测 |
| clarification | 「表现最好」且无 metrics → need_clarification；明确 GMV+时间 → 否 |
| schema_retriever | channel+gmv → 含 orders 与 gmv 口径；无应用表；analyst 无敏感列 |
| intent_analyzer | mock LLM JSON → 正确写入 state；坏 JSON → unknown/react 兜底；prompt 字符串断言不含全表列清单 |
| graph / pipeline | mock LLM：澄清路径无 sql/rows；快乐路径含 route_decision + sql + rows + answer；Guardrail 拒绝有 error |
| chat API | SSE 含 `route_decision`；澄清时 `done.need_clarification=true` |
| 回归 | 既有 auth / guardrail / schema 测试仍通过 |

手工联调：真实 LLM 下 ≥5 个示例成功；至少 1 个模糊问题返回澄清且不执行 SQL。

## 11. 验收对照（docs/06 Phase 3）

| 项 | 标准 |
|----|------|
| Trace | 每次请求有 Agent Trace；前端随 SSE 更新 |
| Intent | 产出 intent、slots、route_mode；分流字段看 route_mode 而非 intent |
| Prompt | Intent 输入不含全量业务表字段列表 |
| Schema | SchemaRetriever 只返回相关字段，并能把 slots.metrics 映射到口径 |
| 澄清 | 模糊问题返回澄清并结束（不跑 SQL） |
| README | 能解释 LangGraph 主图、双模式（目标形态）与当前单路径、节点职责 |

## 12. 文档同步

- README：Phase 状态改为 1–3；说明已用 LangGraph 单路径；intent≠route_mode；澄清行为；双模式子图仍为 Phase 5
- 若 `docs/04` SSE 表与实现事件名有出入，以本文 §7.1 为准做最小同步（不扩写 Phase 4+ 事件）

## 13. 全局约束（实现时默认遵守）

- Python：仅 conda `python3.12`；配置仅 `config.yaml`
- 只在本仓库 main 工作区改代码；不建 worktree 做功能开发
- Agent 不自动 `git commit`；Phase 完成后由用户提交
- 默认 chat 路径 SQL 必须经 Guardrail；禁止节点直连执行旁路
- 一次只改 Phase 3 相关文件；不做无关重构
