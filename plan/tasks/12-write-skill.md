# Task 12: 受控写入 Skill（prepare / execute，无 interrupt）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T11 · 交给：T13 · 里程碑：M4

## Boundary

| | |
| --- | --- |
| **Owns** | `prepare_write`（预览，不写库）与 `execute_write`（确认后提交）。返回 `WriteSkillResult`。 |
| **In** | `skills/write/graph.py`、`preview.py`、`prompt/write_plan.yaml`、`tests/test_write_skill.py`。 |
| **Out** | `interrupt()`、Coordinator HITL 节点、HTTP resume、补偿操作、一键回滚。 |
| **Must not** | 调用 `interrupt()`（源码/AST 不得出现）；预览阶段 INSERT 回执或持锁；analyst 角色写入；W07 冲突时仍用旧 `operation_id` 提交；配置关掉 `must_hitl`。 |

**Files:**
- Create: `backend/app/skills/write/graph.py`
- Create: `backend/app/skills/write/preview.py`
- Create: `backend/app/prompt/write_plan.yaml`
- Create: `tests/test_write_skill.py`

**Interfaces:**
- `prepare_write(task: WriteTask, ctx) -> WriteSkillResult`（`status="preview"`，含 `operation_id`、`request_hash`、预览）
- `execute_write(operation_id, request_hash, ctx) -> WriteSkillResult`
- **禁止** 本图调用 `interrupt()`。HITL 由 T13 Coordinator 执行。

节点（文档 W01–W08，其中 W06 不在本 Skill）：

- W01 LLM → `WritePlan`（不出现物理表名、不出现写 SQL）
- W02 注册表校验
- W03 参数化 SQL
- W04 reader 预检：明确 PK、影响行数、版本快照；>100 或条件缺失 → `WRITE_SCOPE_TOO_LARGE`
- W05 `prepare_write`：生成 `operation_id`、`request_hash` 和预览，**作为返回值**交给 Coordinator（此时尚未 INSERT 回执）
- W07 `execute_write`：reload 权限、批准人（必须同一 operator）、有效期；快照/目标/参数变了 → `VERSION_CONFLICT` + 新预览，不写库
- W08 事务提交（仅 W07 通过后）

预览阶段不持锁。analyst 角色无 `allowed_write_ops` → 直接拒绝。

W07 冲突：不执行，返回 `VERSION_CONFLICT` + 新预览；Coordinator 发新 `operation_id`。旧 ID 尚未插入 MySQL，直接丢弃。

- [ ] **Step 1:** 单测断言写入 Skill 源码/AST 不含 `interrupt`；同一 `operation_id`+`request_hash` 二次 `execute_write` 不重复变更；拒绝路径不写业务表。

- [ ] **Step 5: Commit** `feat: add controlled write skill prepare/execute without interrupt`
