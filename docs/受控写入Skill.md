# 受控写入Skill

## 1. 作用

把`WriteTask`转换为可预览、需确认、可审计的小范围MySQL写入。MVP只支持1～2类审核过的操作，单次最多影响100行。

## 2. 实现流程

```text
输入：WriteTask + Runtime Context
操作类型、业务对象ID、参数、条件、权限
  ↓
[LLM] 生成结构化WritePlan，不选择物理表，不生成写SQL
  ↓
[规则] 操作注册表选择参数化SQL模板
允许的表/字段、必填条件、权限、影响上限、并发策略
  ↓
[公共Tool] Schema校验 + MySQL只读预检
明确目标主键、影响行数和数据版本
  ├─ 越权、条件缺失或超过100行：拒绝
  └─ 通过
       ↓
[prepare_write] 生成operation_id、request_hash和预览
  ↓
[approval] interrupt()等待HITL确认
  ↓
[execute_write] 重新检查权限和批准有效期
  ↓
[公共规则] SQL安全网关确认最终SQL与注册模板一致
  ↓
[公共Tool] MySQL InnoDB事务
唯一回执 → 版本复验 → 业务变更 → 审计 → COMMIT
  ↓
输出：operation_id + 状态 + 影响行数 + audit_id
```

## 3. 白名单写入

每个`operation_type`在操作注册表中配置：

```text
允许的表和字段
参数化SQL模板与参数类型
必填筛选条件和权限
max_affected_rows
version_predicate或locking_read
是否必须HITL
```

LLM只负责把自然语言整理成`WritePlan`。SQL模板选择、参数绑定、范围限制和权限检查全部由规则执行。

最终SQL仍经过公共安全网关，只允许注册操作对应的单条参数化语句、目标表、字段和`WHERE`条件。

写前预览展示业务对象、变更内容、目标主键、预计影响行数和版本快照。批准记录绑定`operation_id`、`request_hash`、批准人和有效期；参数、目标或版本变化后必须重新预览和确认。

## 4. 事务与故障处理

最终事务依次执行：

1. 插入带唯一键的操作回执；
2. 使用版本条件更新，或对有限目标执行`SELECT ... FOR UPDATE`；
3. 执行业务变更并写数据库审计；
4. 更新回执状态、影响行数和`audit_id`；
5. 提交事务。

版本冲突时整个事务回滚，并使用新`operation_id`重新预览。回执、业务表和审计表必须位于同一MySQL实例且使用InnoDB。

连接中断后不能盲目重试：

- 主库存在相同`operation_id`且哈希一致：返回原结果；
- 主库确认不存在：使用同一ID重试；
- 主库状态无法确认：标记`UNKNOWN`并转人工。

`operation_id`防止同一次操作因网络重试、并发提交或LangGraph恢复而重复生效。两个独立请求即使内容相同，也会得到不同ID，系统不会自动判断它们是同一业务操作。

## 5. LangGraph与存储边界

写入拆成`prepare_write`、`approval`、`execute_write`三个节点。LangGraph恢复时会从当前节点开头重放，因此`approval`节点只调用`interrupt()`，不能在它前面生成ID或写数据库。

SQLite只保存Agent状态。MySQL主库回执才是写入是否提交的依据；MySQL已提交但Checkpoint更新失败时，按`operation_id`查询回执并修复状态，不能重新执行业务变更。

## 6. 能力边界

- 只支持注册表中的操作、表和字段；
- 只允许明确主键或可在事务中锁定并复验的有限目标；
- 不支持自由形式写SQL、全表更新、DDL、跨库事务和在线批量写入；
- 超过影响上限直接拒绝，不能自动分页或拆批；
- 已提交操作不能按`operation_id`直接回滚；撤销必须创建新的补偿操作并再次HITL；
- 查询与写入使用不同MySQL账号，写入账号只拥有注册操作需要的权限。
