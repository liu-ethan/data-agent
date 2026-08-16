# Spec 03：最小 Runtime Graph

状态：`Draft`

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
| `agent_node` | 生成 TaskFrame，选择 `RETRIEVE`、`GENERATE`、`ASK_USER`、`RESPOND` |
| `retrieval_node` | 返回权限过滤后的 GroundedContext 和 Coverage |
| `query_generation_node` | 生成 QuerySpec + CandidateSQL，或返回 SchemaGap |
| `execution_gateway_node` | 调用 ReadGateway，返回 Observation |
| `response_node` | 基于 result_id 和摘要输出回答与表格描述 |

## 5. AgentState 最小字段

```json
{
  "thread_id": "thread_1008",
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
  "pending_interrupt": null,
  "budgets": {
    "iterations_used": 0,
    "retrieval_rounds_used": 0
  },
  "action_history": []
}
```

## 6. 条件边不变量

- Coverage 不是 `SUFFICIENT` 时禁止进入 `GENERATE`。
- SQL 未通过 Gateway 时不能产生成功 `ResultObservation`。
- `GoalChecklist` 未完成时不能直接 END。
- 达到 6 轮、2 次召回或 30 秒预算后必须澄清或返回已完成部分。
- 连续相同 Action 和参数时终止循环。
- 空结果不能自动扩大时间或删除用户过滤。
- 权限失败立即终止。

## 7. API 最小契约

```text
POST /api/chat
GET /api/results/{result_id}
```

`POST /api/chat` 支持创建或继续 `thread_id`，通过 SSE 输出：

- 当前 Node；
- Action；
- 状态摘要；
- 最终回答；
- 错误。

不输出隐藏推理，不输出完整 Prompt。

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

## 10. 测试证据

- Graph 路由单元测试。
- 预算终止测试。
- Gateway 失败后的响应测试。
- SSE 事件契约测试。
- 10 条端到端 Golden Case。
