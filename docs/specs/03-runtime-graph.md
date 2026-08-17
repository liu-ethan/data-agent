# Spec 03：最小 Runtime Graph

状态：`Implemented`

对应里程碑：M3

## 1. 范围

实现五个顶层 LangGraph Node 和条件边，先使用可替换的固定检索服务，验证端到端状态流转、预算控制、错误恢复和前端最小展示。

## 2. In Scope

- 五个顶层 LangGraph Node。
- AgentState 最小结构。
- 条件边和预算终止规则。
- 可替换的固定 CatalogRetrievalService。
- `POST /api/chat` 和结果读取接口。
- SSE 运行状态输出。
- 最小 React 对话、Trace 和结果表格。
- 10 条单轮 Golden Case 端到端验证。

## 3. Out of Scope

- 真实 Milvus 检索和 Reranker，见 Spec 04。
- 多轮 Checkpoint、Artifact 指代和长期记忆，见 Spec 05。
- Admin 写入，见 Spec 06。
- 完整图表和 CSV 制品，见 Spec 05。
- 任意绕过 ReadGateway 的 SQL 执行。

## 4. 顶层 Node

| Node | 职责 |
| --- | --- |
| `agent_node` | 生成 TaskFrame，选择 `RETRIEVE`、`GENERATE`、`EXECUTE`、`ASK_USER`、`RESPOND` |
| `retrieval_node` | 返回权限过滤后的 GroundedContext 和 Coverage |
| `query_generation_node` | 生成 QuerySpec + CandidateSQL，或返回 SchemaGap |
| `execution_gateway_node` | 调用 ReadGateway，返回 Observation |
| `response_node` | 基于 result_id 和摘要输出回答与表格描述 |

统一 Action 枚举为：`RETRIEVE`、`GENERATE`、`EXECUTE`、`ASK_USER`、`RESPOND`、`END`、`FAIL`。`EXECUTE` 是 Graph 的可观测 Action，即使实际执行由独立 ReadGateway 完成，也必须出现在状态和 SSE 中。

## 5. AgentState 最小字段

```json
{
  "thread_id": "thread_1008",
  "request_id": "req_1008",
  "user_id": "u_east_user",
  "status": "RUNNING",
  "task_frame": null,
  "previous_task_frame": null,
  "context_frame": null,
  "grounded_context_id": null,
  "grounded_context": null,
  "coverage": "UNKNOWN",
  "schema_gap": null,
  "query_plan_id": null,
  "query_plan": null,
  "result_ids": [],
  "artifact_ids": [],
  "latest_observation": null,
  "previous_query_error": null,
  "next_action": "RETRIEVE",
  "goal_checklist": {},
  "budgets": {
    "iterations_used": 0,
    "retrieval_rounds_used": 0,
    "query_retries_used": 0,
    "max_iterations": 6,
    "max_retrieval_rounds": 2
  },
  "action_history": [],
  "last_action_fingerprint": null,
  "pending_interrupt": null,
  "messages": [],
  "trace_id": null,
  "model_traces": [],
  "schema_version": "agent_state_v1"
}
```

`status` 为 `RUNNING`、`WAITING_FOR_USER`、`SUCCEEDED`、`FAILED`、`REJECTED` 或 `TIMEOUT`。`pending_interrupt` 在 M3 必须为 `null`；M5 才允许持久化和恢复 Interrupt。`model_traces` 记录每次 LLM 调用的输入 token、输出 token、模型名称和耗时,只在前端调试面板可见。`last_action_fingerprint` 只用于检测无进展的重复 Action，不进入 Prompt 或 SSE。

## 6. 条件边不变量

- Coverage 不是 `SUFFICIENT` 时禁止进入 `GENERATE`。`query_generation_node` 在 Coverage 非 `SUFFICIENT` 时必须拒绝，不能只依赖 `agent_node`。
- SQL 未通过 Gateway 时不能产生成功 `ResultObservation`。
- `GoalChecklist` 未完成时不能直接 END。`DATA_QUERY` 在 `query_executed` 为假时，`response_node` 不得进入 `SUCCEEDED` / `END`。
- 达到 6 轮、2 次召回或 30 秒预算后必须澄清或返回已完成部分。
- 连续相同 Action 和参数时终止循环。参数指纹为 Action + Coverage + `query_plan_id` + SchemaGap 概念 + Observation 状态；无进展的重复 `RETRIEVE` / `GENERATE` / `EXECUTE` 立即 `FAIL`。
- 空结果不能自动扩大时间或删除用户过滤；`EMPTY` 只能进入 `RESPOND`。
- 权限失败立即终止。`PERMISSION_DENIED`、`SQL_FORBIDDEN_OPERATION`、`SQL_OBJECT_NOT_ALLOWED` 和 Reader 账号错误不得进入 GENERATE 重试。

固定路由：

| 当前条件 | 下一 Action |
| --- | --- |
| 首次请求且未完成 TaskFrame | `RETRIEVE` |
| Coverage 为 `PARTIAL`、`AMBIGUOUS` 或 `UNKNOWN` | `RETRIEVE` 或 `ASK_USER` |
| Coverage 为 `SUFFICIENT` 且没有 QueryPlan | `GENERATE` |
| QueryPlan 通过结构校验 | `EXECUTE` |
| Gateway 返回 `SUCCESS` 或 `EMPTY` | `RESPOND` |
| Gateway 返回可重试错误且预算未耗尽 | `GENERATE`，最多重试一次 |
| 权限拒绝、不可恢复错误或预算耗尽 | `FAIL` 或 `RESPOND` |
| 已输出最终回答 | `END` |

禁止 `GENERATE -> GENERATE` 无条件循环，也禁止 `EXECUTE` 绕过 ReadGateway。

## 7. API 最小契约

```text
POST /api/chat
GET /api/results/{result_id}
```

请求：

```json
{
  "thread_id": null,
  "message": "昨天各品类 GMV 是多少？",
  "user_id": "u_east_user",
  "timezone": "Asia/Shanghai",
  "request_id": "req_1008"
}
```

响应必须返回 `request_id`、`thread_id`、`status` 和 SSE 地址或事件流。M3 的 `thread_id` 只支持同一请求链路关联，不承诺进程重启后恢复；恢复能力属于 Spec 05。

SSE 事件最小结构：

```json
{
  "event": "node.started",
  "request_id": "req_1008",
  "thread_id": "thread_1008",
  "node": "retrieval_node",
  "action": "RETRIEVE",
  "status": "RUNNING",
  "duration_ms": null,
  "error_code": null
}
```

允许的事件为 `run.started`、`node.started`、`node.completed`、`interrupt.created`、`run.completed` 和 `run.failed`。不得输出隐藏推理、完整 Prompt、原始密钥或完整结果集。

`POST /api/chat` 支持创建请求，GET 结果接口只接受已授权的 `result_id`。

- 当前 Node；
- Action；
- 状态摘要；
- 最终回答；
- 错误。

## 8. 前端最小能力

- 对话消息列表。
- 当前运行状态。
- Trace 折叠区。
- 结果表格分页读取。
- 错误和空结果状态。

## 9. 验收标准

- 10 个单轮 Golden Case 从自然语言端到端完成。
- Trace 能看到真实 Node 循环、预算变化和 Gateway 结果。
- 简单问题不发生无意义二次召回。
- 所有 SQL 都经过 Spec 02 的 ReadGateway。
- Action 序列与评测契约一致，包含 `EXECUTE`。

## 10. 测试证据

- Graph 路由单元测试 (`tests/test_graph.py`)。
- 预算终止测试 (`tests/test_graph.py::test_agent_iteration_budget_is_enforced`)。
- 时间解析和 Driver 占位符测试 (`tests/test_graph.py::test_today_time_range_and_driver_placeholders_are_canonicalized`)。
- Gateway 可重试错误只 GENERATE 一次 (`tests/test_runtime_graph_spec03.py::test_retryable_gateway_error_retries_generate_once`)。
- SSE 事件契约测试 (`tests/test_graph.py::test_sse_runtime_uses_only_the_documented_terminal_event`)。
- 10 条端到端 Golden Case (`tests/eval_cases/core.json`)。
- `tests/test_llm.py`：Anthropic/OpenAI 线协议、thinking 隔离、Token/Cache 用量、超时、429 重试、401 立即失败和结构化响应失败。
- `tests/test_query_grounding.py`：未受信表、字段、指标和不一致结构化草案在 Gateway 之前被拒绝。
- `tests/test_graph.py::test_llm_agent_runs_typed_grounded_query_and_evidence_bound_answer`：TaskFrame、Grounded QueryPlan、ReadGateway 和 result_id 证据回答的完整链路。
- `tests/test_graph.py::test_langgraph_persists_sse_events_before_the_run_finishes`：SSE 事件持久化。
- `tests/test_graph.py::test_waiting_thread_resumes_after_runtime_process_restart`：WAITING_FOR_USER 状态恢复。
- Spec 03 §6 不变量 (`tests/test_runtime_graph_spec03.py`)：Coverage 禁止 GENERATE、GoalChecklist 禁止 END、相同 Action 循环终止、空结果不扩条件、权限失败立即 REJECTED。

## 11. 模块拆分 (M3 重构后)

`backend/app/graph/` 下的模块按职责单一原则拆分:

| 文件 | 行数 | 职责 |
|---|---|---|
| `main_graph.py` | ~280 | RuntimeGraph 类、graph 构建、路由、run 循环、错误处理、终态判定 |
| `state.py` | ~80 | LLM 契约结构 (TaskUnderstanding, QueryDraft, AnswerDraft 等) |
| `_time_parser.py` | ~70 | 确定性相对时间解析 (昨天/今天/最近 N 天/本月) |
| `_sql_canonicalizer.py` | ~30 | Driver 占位符规范化 (%(name)s → :name) |
| `_query_normalizer.py` | ~140 | QueryDraft → QueryPlan 或 SchemaGap,含 metric 名映射和 PAID 过滤注入 |
| `_task_understanding.py` | ~110 | LLM 意图识别 + TaskFrame 组装 |
| `_events.py` | ~80 | SSE 事件发射 + 状态 checkpoint 持久化 |
| `_thread_title.py` | ~80 | 线程标题 fire-and-forget 生成 |
| `nodes/agent.py` | ~130 | 5 个 Action 之间的条件边决策、循环指纹、权限立即终止 |
| `nodes/retrieval.py` | ~57 | SchemaGap 重召回 |
| `nodes/query_generation.py` | ~115 | Grounded QueryPlan + candidate SQL 生成 |
| `nodes/execution_gateway.py` | ~35 | ReadGateway 调用 (唯一执行入口) |
| `nodes/response.py` | ~190 | 结果摘要回答 + artifact 创建；DATA_QUERY 未执行查询不得 END |

每个模块独立可测;LLM 提示词、metric 名映射、SQL 规范化等可独立修改而不影响其它模块。
