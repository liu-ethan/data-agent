# Spec 05：记忆、Interrupt 与结果制品

状态：`Draft`

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
  "type": "FIELD_LIST",
  "source_ref": "obj_orders",
  "items": [
    {"ordinal": 1, "field": "orders.order_id"}
  ],
  "permission_snapshot": "policy_v18",
  "source_version": "orders_v3",
  "expires_at": "2026-09-01T00:00:00+08:00"
}
```

## 6. Interrupt 契约

```json
{
  "status": "WAITING_FOR_USER",
  "reason": "AMBIGUOUS_METRIC",
  "question": "退款率指订单退款率还是金额退款率？",
  "candidates": ["订单退款率", "金额退款率"],
  "resume_node": "agent_node",
  "checkpoint_id": "ckpt_..."
}
```

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

## 9. 验收标准

- 15 条多轮任务得到正确、可解释的指代结果。
- 进程重启后可恢复未完成会话。
- HITL 前后不重复执行已完成步骤。
- 完整结果集不进入 Prompt。
- 表格、CSV 和图表都引用真实 `result_id`。
- 长期偏好不会覆盖本轮明确条件。

## 10. 测试证据

- Checkpoint 恢复测试。
- 同线程并发乐观锁测试。
- Artifact 指代测试。
- Artifact 过期和权限变化测试。
- 滚动摘要 JSON Schema 测试。
- 长期记忆写入与召回测试。
- CSV 和 ECharts DSL 契约测试。

