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
  "task_frame": {},
  "context_frame": {},
  "grounded_context_id": null,
  "coverage": "UNKNOWN",
  "schema_gap": null,
  "query_plan_id": null,
  "result_ids": [],
  "artifact_ids": [],
  "latest_observation": null,
  "goal_checklist": {},
  "next_action": "RETRIEVE",
  "pending_interrupt": null,
  "budgets": {
    "iterations_used": 0,
    "retrieval_rounds_used": 0
  },
  "action_history": [],
  "schema_version": "agent_state_v1"
}
```

`status` 为 `RUNNING`、`WAITING_FOR_USER`、`SUCCEEDED`、`FAILED`、`REJECTED` 或 `TIMEOUT`。`pending_interrupt` 在 M3 必须为 `null`；M5 才允许持久化和恢复 Interrupt。

## 6. 条件边不变量

- Coverage 不是 `SUFFICIENT` 时禁止进入 `GENERATE`。
- SQL 未通过 Gateway 时不能产生成功 `ResultObservation`。
- `GoalChecklist` 未完成时不能直接 END。
- 达到 6 轮、2 次召回或 30 秒预算后必须澄清或返回已完成部分。
- 连续相同 Action 和参数时终止循环。
- 空结果不能自动扩大时间或删除用户过滤。
- 权限失败立即终止。

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

- Graph 路由单元测试。
- 预算终止测试。
- Gateway 失败后的响应测试。
- SSE 事件契约测试。
- 10 条端到端 Golden Case。
- `tests/test_llm.py`：Anthropic/OpenAI 线协议、thinking 隔离、Token/Cache 用量、超时、429 重试、401 立即失败和结构化响应失败。
- `tests/test_query_grounding.py`：未受信表、字段、指标和不一致结构化草案在 Gateway 之前被拒绝。
- `tests/test_graph.py::test_llm_agent_runs_typed_grounded_query_and_evidence_bound_answer`：TaskFrame、Grounded QueryPlan、ReadGateway 和 result_id 证据回答的完整链路。
