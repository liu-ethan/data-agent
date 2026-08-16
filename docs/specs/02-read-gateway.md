# Spec 02：可信只读执行网关

状态：`Draft`

对应里程碑：M2

## 1. 范围

ReadGateway 是所有只读 SQL 的唯一执行入口。它接收 `QueryPlan` 中的候选 SQL，但不信任候选 SQL，必须完成 AST、权限、语义、成本、执行和结果契约检查。

## 2. In Scope

- SQLGlot MySQL AST 解析。
- 单语句 `SELECT` / `WITH` 白名单。
- 表、列、函数、敏感字段和系统库校验。
- 行级权限注入，并对改写 SQL 再次解析。
- 指标、时间过滤、Join、Group By 语义检查。
- `EXPLAIN FORMAT=JSON` 成本检查。
- 只读账号执行、超时、最大返回行数。
- ResultRepository 写入和 `ResultObservation` 返回。
- Trace 和审计字段。

## 3. Out of Scope

- 写入执行，见 Spec 06。
- 任意 Python 分析代码执行。
- 自动修复 SQL。
- 执行未经 QuerySpec 描述的临时 SQL。

## 4. 输入输出

输入：

```json
{
  "query_plan_id": "query_1042",
  "query_spec": {},
  "candidate_sql": "SELECT ...",
  "permission_context_ref": "perm_...",
  "catalog_version": "catalog_v18"
}
```

输出：

```json
{
  "status": "SUCCESS",
  "result_id": "result_101",
  "summary": {
    "row_count": 12,
    "columns": ["category_name", "gmv"],
    "empty": false
  },
  "trace": {
    "original_sql_hash": "...",
    "rewritten_sql_hash": "...",
    "estimated_cost": 123,
    "duration_ms": 321
  }
}
```

拒绝输出：

```json
{
  "status": "REJECTED",
  "error_code": "SQL_FORBIDDEN_OPERATION",
  "message": "Only single SELECT/WITH statements are allowed",
  "retryable": false
}
```

## 5. 必须拒绝

- `INSERT`、`UPDATE`、`DELETE`。
- `CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`RENAME`。
- `GRANT`、`REVOKE`、`SET`、`USE`。
- `INTO OUTFILE`、`INTO DUMPFILE`。
- 多语句。
- 系统库和未授权库表。
- `SELECT *` 命中敏感列或未授权列。
- 无法安全注入行级权限的 SQL。
- 缺少事实表时间过滤的大表查询。
- EXPLAIN 超过成本阈值或可能扫描过大。

## 6. 不变量

- Gateway 内部选择 `agent_reader`，模型和 Node 不知道连接信息。
- RLS 注入后必须重新解析 AST。
- `LIMIT` 不能替代 EXPLAIN、时间过滤和超时。
- 空结果标记为 `EMPTY`，不能回答成数值 0。
- 结果集不能直接放入 AgentState，只保存 `result_id` 和摘要。

## 7. Trace 字段

至少记录：

- `query_plan_id`
- `permission_policy_version`
- `catalog_version`
- `original_sql_hash`
- `rewritten_sql_hash`
- `tables`
- `columns`
- `rls_injected`
- `explain_cost`
- `row_count`
- `duration_ms`
- `error_code`

## 8. 验收标准

- 30/30 危险 SQL 被预期规则拦截。
- 20 条 Golden SQL 全部通过 Gateway 并得到 Golden Result。
- 代码库不存在绕过 ReadGateway 的分析 SQL 执行入口。
- Trace 可解释每次拒绝或执行。

## 9. 测试证据

- SQL AST 单元测试。
- 权限和敏感字段测试。
- RLS 注入回归测试。
- EXPLAIN 阈值测试。
- Golden SQL 集成测试。
- 危险 SQL 安全测试。

