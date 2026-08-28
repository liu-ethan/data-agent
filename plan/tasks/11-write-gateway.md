# Task 11: 写入网关、操作注册表、InnoDB 事务 Tool

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T3 T4 · 交给：T12 · 里程碑：M4

## Boundary

| | |
| --- | --- |
| **Owns** | 写入操作白名单、模板 AST 同构检查、InnoDB 事务执行（回执+业务+审计同事务）、`request_hash`。 |
| **In** | `seeds/write_ops.yaml`、`skills/write/registry.py`、`gateway/write_policy.py`、`mysql/execute_write.py`、`tests/test_write_gateway.py`、`tests/test_execute_write.py`。 |
| **Out** | 写入 Skill 图、`interrupt()`、Coordinator、预览文案、补偿/回滚操作。 |
| **Must not** | 注册第三类写入或补偿操作；`must_hitl=false` 生效；用 Checkpoint 当提交证据；事务步骤调换顺序；同内容不同请求复用 `operation_id`；让 LLM 选表或写 SQL。 |

**Files:**
- Create: `seeds/write_ops.yaml`
- Create: `backend/app/skills/write/registry.py`
- Create: `backend/app/gateway/write_policy.py`
- Create: `backend/app/mysql/execute_write.py`
- Create: `tests/test_write_gateway.py`
- Create: `tests/test_execute_write.py`

**默认两类操作（用户可在配置清单中改名）：**

1. `update_sku_status`：按 `sku_id` 列表改 `dim_sku.status`（`on_sale|off_sale`），`version_predicate` 用 `row_version`。
2. `adjust_sku_inventory`：按 `sku_id` 改库存数量，事务内 `SELECT ... FOR UPDATE` 有限主键。

`write_ops.yaml` 每项：允许表/字段、参数化模板、必填条件、权限、`max_affected_rows=100`、`version_predicate` 或 `locking_read`、`must_hitl=true`（代码忽略 false）。

**Interfaces:**
- `build_command(plan: WritePlan) -> PreparedCommand`（LLM 不选表、不写 SQL）
- `check_write_sql(sql, params, op_def) -> GatewayDecision`：必须与模板 AST 同构，仅参数不同
- `execute_write(cmd, ctx) -> WriteReceipt`
- `request_hash(plan, version_snapshots) -> str`：算法见 Locked Decision 8

事务顺序（不可调换）：

1. `INSERT` 回执（`operation_id` 唯一键），冲突则按 ID 读原回执。
2. 版本条件 UPDATE 或对有限 PK `SELECT ... FOR UPDATE` 复验。
3. 业务变更 + `INSERT da_write_audit`。
4. 更新回执 `committed` + `affected_rows` + `audit_id`。
5. `COMMIT`。

版本冲突：`ROLLBACK`（回执未提交），调用方换新 `operation_id` 重新预览。确认瞬间行数 >100 → `WRITE_SCOPE_TOO_LARGE`，不提交。

断线协议：

```text
主库有相同 operation_id 且 request_hash 一致 → 返回原结果
主库确认不存在 → 同一 operation_id 重试
主库状态无法确认 → status=unknown，转人工
```

写入是否提交只查 MySQL 主库回执。

- [ ] **Step 1:** 并发两次同 `operation_id` 只变更一次；非白名单 SQL 拒绝；101 行预检拒绝。
