# Spec 05：记忆、Interrupt 与结果制品

状态：`Implemented`

对应里程碑：M5

## 1. 范围

实现可恢复多轮对话、Artifact 指代、澄清 Interrupt、滚动摘要、长期偏好和结果制品。核心目标是让“刚才”“第一个字段”“再加退款率”等追问可解释、可恢复、不污染权限。

## 2. 三层状态

| 层级 | 生命周期 | 保存内容 |
| --- | --- | --- |
| Working State | 当前 Graph 运行 | TaskFrame、Coverage、Observation、预算、next_action |
| Short-term Memory | 同一 thread | 消息、Checkpoint、滚动摘要、Artifact/result 引用、Interrupt |
| Long-term Memory | 跨 thread | 用户确认的稳定偏好，如时区、默认店铺、图表偏好 |

Schema、指标、Join 和 Verified SQL 属于共享语义目录，不属于长期记忆。

## 3. In Scope

- MySQL Checkpointer。
- conversation messages、summaries、artifacts。
- 每个 super-step 后保存 Checkpoint。
- Interrupt 前强制保存状态。
- ReferenceResolver。
- PromptContextBuilder。
- 结构化滚动摘要。
- Key-based UserMemoryStore。
- React Data Table、CSV、ECharts DSL。

## 4. Out of Scope

- 把完整结果集写入 Prompt。
- 用户长期记忆向量召回。
- 任意 Python 图表代码。
- 自动把本轮查询条件写成长期偏好。

## 5. Artifact 契约

```json
{
  "artifact_id": "schema_list_023",
  "conversation_id": "conv_1008",
  "owner_user_id": "u_east_user",
  "type": "FIELD_LIST",
  "source_ref": "obj_orders",
  "items": [
    {"ordinal": 1, "field": "orders.order_id"}
  ],
  "permission_snapshot": "policy_v18",
  "source_version": "orders_v3",
  "created_at": "2026-08-16T10:00:00Z",
  "expires_at": "2026-09-01T00:00:00Z",
  "payload_ref": "artifact_payload_023",
  "schema_version": "artifact_v1"
}
```

`owner_user_id`、当前 `PermissionContext`、`permission_snapshot`、`catalog_version` 和 `expires_at` 必须在每次读取制品时重新校验。过期、权限变化或目录版本不兼容时返回 `ARTIFACT_STALE`，不能返回旧内容。

## 6. Interrupt 契约

```json
{
  "status": "WAITING_FOR_USER",
  "reason": "AMBIGUOUS_METRIC",
  "question": "退款率指订单退款率还是金额退款率？",
  "candidates": ["订单退款率", "金额退款率"],
  "resume_node": "agent_node",
  "checkpoint_id": "ckpt_...",
  "interrupt_id": "interrupt_001",
  "expires_at": "2026-08-16T10:15:00Z",
  "schema_version": "interrupt_v1"
}
```

恢复接口：

```text
POST /api/threads/{thread_id}/interrupts/{interrupt_id}/resume
```

请求必须包含 `user_id`、`answer`、`client_request_id` 和 `expected_state_version`。只有状态为 `WAITING_FOR_USER`、操作者匹配且版本未变化时才允许恢复；重复提交同一 `client_request_id` 返回第一次结果，不重复执行已完成步骤。

## 7. 长期记忆写入规则

只在以下情况写入：

- 用户明确说“以后默认……”；
- 用户确认系统询问；
- 产品设置页修改。

不能写入：

- 权限、Schema、业务指标口径；
- Prompt Injection 内容；
- “这次只看华东”这类一次性查询条件；
- 敏感原始字段。

## 8. 不变量

- 每次请求重新读取 PermissionContext。
- Artifact 恢复时重新校验用户、权限快照、Schema 版本和 TTL。
- 摘要区分 `USER_CONFIRMED`、`SYSTEM_OBSERVED`、`MODEL_INFERRED`。
- 当前用户明确条件优先于长期偏好。
- 待审批 MutationSpec 不能只保留摘要。
- Checkpoint 采用乐观锁；写入条件为 `thread_id + state_version`，冲突返回 `CHECKPOINT_CONFLICT`。
- 每个具有外部副作用的步骤都必须有 `idempotency_key` 和完成标记。

Checkpoint 最小字段为：`checkpoint_id`、`thread_id`、`state_version`、`parent_checkpoint_id`、`status`、`serialized_state_ref`、`idempotency_key`、`created_at`、`updated_at`。Checkpoint 不直接保存密钥或完整结果集。

长期记忆最小字段为：`memory_id`、`user_id`、`memory_key`、`value`、`source`、`version`、`confirmed_at`、`expires_at`、`created_at`。同一用户和 `memory_key` 只保留一个当前版本；删除和撤回必须可审计。

## 9. 验收标准

- 15 条多轮任务得到正确、可解释的指代结果。
- 进程重启后可恢复未完成会话。
- HITL 前后不重复执行已完成步骤。
- 完整结果集不进入 Prompt。
- 表格、CSV 和图表都引用真实 `result_id`。
- 长期偏好不会覆盖本轮明确条件。
- Interrupt 过期、权限变化、版本冲突和重复 resume 都有明确错误响应。

## 10. 测试证据

- Checkpoint 恢复测试。
- 同线程并发乐观锁测试。
- Artifact 指代测试。
- Artifact 过期和权限变化测试。
- 滚动摘要 JSON Schema 测试。
- 长期记忆写入与召回测试。
- CSV 和 ECharts DSL 契约测试。
