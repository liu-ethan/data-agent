# Spec 02：可信只读执行网关

状态：`Ready`

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

`query_spec` 不能是任意字典，最小结构如下：

```json
{
  "query_id": "query_1042",
  "metric_refs": ["gmv"],
  "dimension_refs": ["categories.category_name"],
  "filters": [
    {"field": "orders.status", "operator": "=", "value": "PAID", "source": "catalog"}
  ],
  "time_range": {
    "field": "orders.paid_at",
    "start": "2026-08-15T00:00:00+08:00",
    "end": "2026-08-16T00:00:00+08:00",
    "timezone": "Asia/Shanghai"
  },
  "join_path_refs": ["orders_to_order_items", "order_items_to_products"],
  "allowed_object_ids": ["obj_orders", "obj_order_items", "obj_products", "obj_categories"],
  "expected_columns": ["category_name", "gmv"],
  "max_rows": 1000,
  "schema_version": "query_spec_v1"
}
```

Gateway 必须确认 candidate SQL 的表、列、指标、时间字段、Join path、过滤条件和返回列均可映射到 `QuerySpec`。模型可以选择等价 SQL 表达，但不能新增 QuerySpec 未声明的对象或业务条件。

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
- `WITH` 中包含 DML、DDL 或未授权的 CTE。
- QuerySpec 与 SQL AST 的对象、列、指标或时间条件不一致。
- 无法安全注入行级权限的 SQL。
- 缺少事实表时间过滤的大表查询。
- EXPLAIN 超过成本阈值或可能扫描过大。

## 6. 不变量

- Gateway 内部选择 `agent_reader`，模型和 Node 不知道连接信息。
- RLS 注入后必须重新解析 AST。
- `LIMIT` 不能替代 EXPLAIN、时间过滤和超时。
- 空结果标记为 `EMPTY`，不能回答成数值 0。
- 结果集不能直接放入 AgentState，只保存 `result_id` 和摘要。
- 默认 `scope_mode = NONE`；`ALLOWLIST` 必须对每个可达事实表注入 `shop_id IN (:allowed_shop_ids)`。
- 无法把权限条件安全注入所有表别名、子查询、CTE 或 Join 分支时必须拒绝，不允许降级为无权限查询。
- 只允许命名参数；参数类型必须由目录字段类型校验，参数值不能拼接进 SQL 文本。
- 列表参数由 Gateway 展开为固定数量的命名参数；空列表直接返回 `PERMISSION_DENIED`，不能生成 `IN ()` 或取消过滤。
- 查询在只读事务中执行；结果写入 ResultRepository 后才返回 `SUCCESS`。

权限注入规则：

1. `PermissionContext.scope_mode = NONE` 直接返回 `PERMISSION_DENIED`。
2. `scope_mode = ALLOWLIST` 时，所有可达事实表必须有明确 `shop_id` 约束；无法追溯到 `shops` 的对象拒绝。
3. `scope_mode = ALL` 只允许明确拥有 `DATA_ADMIN` 角色的操作者。
4. 权限判断使用请求时重新读取的 `policy_version`，不能信任模型提供的版本。

成本和执行限制：`EXPLAIN FORMAT=JSON` 的 `rows_examined_per_scan` 超过 `max_estimated_rows`，或 cost 超过 `max_cost`，返回 `QUERY_TOO_EXPENSIVE`；执行时间超过 `max_execution_ms` 返回 `QUERY_TIMEOUT`。EXPLAIN 本身失败返回 `EXPLAIN_FAILED`，不能跳过成本检查。

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

错误码至少包括：`SQL_PARSE_ERROR`、`SQL_FORBIDDEN_OPERATION`、`SQL_OBJECT_NOT_ALLOWED`、`PERMISSION_DENIED`、`QUERY_SPEC_MISMATCH`、`MISSING_TIME_FILTER`、`QUERY_TOO_EXPENSIVE`、`EXPLAIN_FAILED`、`QUERY_TIMEOUT`、`RESULT_PERSIST_FAILED`。

## 8. 验收标准

- 30/30 危险 SQL 被预期规则拦截。
- 20 条 Golden SQL 全部通过 Gateway 并得到 Golden Result。
- 代码库不存在绕过 ReadGateway 的分析 SQL 执行入口。
- Trace 可解释每次拒绝或执行。
- 每次允许执行的 SQL 都能回溯到 QuerySpec、目录版本和权限策略版本。

## 9. 测试证据

- SQL AST 单元测试。
- 权限和敏感字段测试。
- RLS 注入回归测试。
- EXPLAIN 阈值测试。
- Golden SQL 集成测试。
- 危险 SQL 安全测试。
