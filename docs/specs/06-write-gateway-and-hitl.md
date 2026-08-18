# Spec 06：WriteGateway 与审批 HITL

状态：`Implemented`

对应里程碑：M6

## 1. 范围

实现 Admin 受控 `INSERT/UPDATE`。写入是独立 Gateway 能力，不允许模型生成原始 DML 后直接执行。

## 2. In Scope

- Admin 权限检查。
- 白名单表和字段。
- `MutationSpec`。
- before 值查询和 `MutationPreview`。
- 审批型 LangGraph Interrupt。
- 恢复后重新校验权限和数据版本。
- 后端生成参数化 SQL。
- 事务执行。
- 审计日志。
- 幂等和重放保护。

## 3. Out of Scope

- User 写入。
- `DELETE`。
- DDL、复制表、重命名表。
- FILE、GRANT。
- 执行模型生成的任意 DML。
- 批量无界更新。

## 4. MutationSpec 契约

```json
{
  "operation": "UPDATE",
  "table": "products",
  "filters": {
    "product_id": 1001
  },
  "changes": {
    "product_name": "新商品名称"
  },
  "user_reason": "修正商品名称"
}
```

`filters` 必须只包含一个已登记的主键或唯一键等值条件；不允许空条件、范围条件或模型拼接 SQL。`changes` 只能包含白名单字段，值按目录字段类型校验。MutationSpec 必须带 `request_id`、`user_id`、`permission_policy_version`、`data_version` 和 `idempotency_key`。

## 5. MutationPreview 契约

```json
{
  "preview_id": "preview_001",
  "operation": "UPDATE",
  "target": "products.product_id=1001",
  "diff": {
    "product_name": {
      "before": "旧商品名称",
      "after": "新商品名称"
    }
  },
  "estimated_affected_rows": 1,
  "risk_level": "MEDIUM",
  "expires_at": "2026-08-16T12:30:00+08:00",
  "data_version": "products_v18",
  "permission_policy_version": "policy_v18",
  "schema_version": "mutation_preview_v1"
}
```

Preview 必须保存完整的参数化 MutationSpec 和版本快照，不能只保存展示用 diff。确认时重新读取目标行并比较 `data_version`；版本变化必须使 preview 失效。

## 6. 必须拒绝

- 非 Admin。
- 不在白名单内的表或字段。
- 没有主键或唯一键过滤的 `UPDATE`。
- 预计影响行数超过阈值。
- 类型不匹配。
- 权限或数据版本变化后的旧审批。
- 重放已提交的 Checkpoint。

## 7. 不变量

- 用户确认不能覆盖权限拒绝。
- HITL 等待期间不持有数据库事务。
- 恢复执行前重新读取 PermissionContext。
- 审计能还原操作者、请求、前后值、审批和执行结果。
- 同一审批只能提交一次。
- 写入连接只能使用 `agent_writer`，migration 账号不能出现在运行时写入路径。
- 所有变化都通过参数绑定执行；事务提交前后记录 affected rows 和数据版本。

## 8. 验收标准

- User 写入在审批前被拒绝。
- Admin 禁止操作直接拒绝，不进入审批。
- Preview 展示 before、after 和预计影响行数。
- 确认后权限或数据版本变化会使旧批准失效。
- 相同 Checkpoint 重放不会重复提交。
- 审计记录完整。

## 9. 测试证据

- 权限拒绝、白名单、Preview diff、HITL resume、幂等提交和审计回放：`tests/test_write_gateway_spec06.py`
- 评测 HITL 用例：`tests/eval_cases/deferred_hitl.json`（Admin 商品名更新走审批；禁止操作在审批前拒绝）
