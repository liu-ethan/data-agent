# Spec 00：工程基线

状态：`Implemented`

对应里程碑：M0

## 1. 范围

建立可持续开发的后端、前端、数据库、配置、测试和 Trace 基线。这个阶段不接入 LLM，不实现真实 Agent 决策。

## 2. In Scope

- 后端目录、FastAPI 应用、健康检查和统一错误结构。
- 前端 React + TypeScript 骨架和基础页面。
- Docker Compose 启动 MySQL。
- Alembic 或等价 migration 基线。
- `.env.example`，区分 migration、reader、writer 账号配置。
- Ruff、类型检查、后端测试、前端检查命令。
- Trace ID 从 API 请求贯穿到日志和 Repository 调用。
- 第一版核心 Pydantic 模型包。

## 3. Out of Scope

- Milvus 或真实向量检索。
- LLM 调用。
- LangGraph 完整编排。
- Admin 写入能力。
- 前端复杂交互。

## 4. 核心数据结构

以下结构是跨 spec 的唯一契约。字段名、枚举值和空值语义先固定；后续扩展只能新增可选字段或升级 `schema_version`，不能静默改变已有字段含义。

### 4.1 请求和权限上下文

| 模型 | 必填字段 | 规则 |
| --- | --- | --- |
| `TaskFrame` | `task_id`, `user_id`, `question`, `intent`, `metric_ids`, `dimension_ids`, `filters`, `time_range`, `timezone`, `explicit_conditions`, `schema_version` | `intent` 为 `DATA_QUERY`、`SCHEMA_LOOKUP`、`CLARIFICATION` 或 `UNSUPPORTED`；`time_range` 使用半开区间 `[start, end)`；`user_id` 是应用操作者，不是业务买家 ID |
| `ContextFrame` | `context_id`, `catalog_version`, `permission_policy_version`, `object_ids`, `field_ids`, `metric_ids`, `join_paths`, `created_at`, `schema_version` | 只保存目录引用，不保存完整结果集 |
| `PermissionContext` | `user_id`, `roles`, `scope_mode`, `allowed_shop_ids`, `denied_classifications`, `policy_version`, `expires_at`, `schema_version` | `scope_mode` 为 `ALL`、`ALLOWLIST` 或 `NONE`；`ALLOWLIST` 为空时按 `NONE` 处理；默认拒绝 |

### 4.2 检索和查询上下文

| 模型 | 必填字段 | 规则 |
| --- | --- | --- |
| `GroundedContext` | `context_id`, `catalog_version`, `objects`, `fields`, `metrics`, `join_paths`, `coverage`, `token_count`, `schema_version` | `objects`、`fields` 和 `join_paths` 均必须经过 `PermissionContext` 过滤；`token_count` 是序列化后实际计算值 |
| `CoverageResult` | `status`, `covered`, `missing`, `ambiguous`, `confidence_notes`, `schema_gap`, `schema_version` | `status` 为 `SUFFICIENT`、`PARTIAL`、`AMBIGUOUS`、`UNSUPPORTED`；只有 `SUFFICIENT` 才允许生成 SQL |
| `SchemaGap` | `gap_id`, `missing_concepts`, `candidate_object_ids`, `narrow_query`, `reason`, `retrieval_round`, `schema_version` | `candidate_object_ids` 必须来自已授权候选；补检不能扩大全量数据源 |
| `QuerySpec` | `query_id`, `metric_refs`, `dimension_refs`, `filters`, `time_range`, `join_path_refs`, `allowed_object_ids`, `expected_columns`, `max_rows`, `schema_version` | 这是 SQL 的语义约束；模型不能通过新增 SQL 字段绕过它 |
| `QueryPlan` | `query_plan_id`, `query_spec`, `candidate_sql`, `parameters`, `catalog_version`, `permission_policy_version`, `generator`, `schema_version` | `candidate_sql` 只允许使用 `parameters` 中的命名参数；执行前必须经过 ReadGateway |

### 4.3 结果、Graph 和错误

| 模型 | 必填字段 | 规则 |
| --- | --- | --- |
| `ResultObservation` | `status`, `result_id`, `summary`, `error_code`, `query_plan_id`, `catalog_version`, `permission_policy_version`, `schema_version` | `status` 为 `SUCCESS`、`EMPTY`、`REJECTED`、`FAILED` 或 `TIMEOUT`；`EMPTY` 表示零行，不等于数值 0；失败时 `result_id` 为 `null` |
| `ArtifactSpec` | `artifact_id`, `type`, `owner_user_id`, `source_result_ids`, `permission_policy_version`, `catalog_version`, `created_at`, `expires_at`, `payload_ref`, `schema_version` | `type` 为 `FIELD_LIST`、`RESULT_TABLE`、`CSV` 或 `CHART_DSL`；大对象只能通过 `payload_ref` 读取 |
| `AgentState` | `thread_id`, `request_id`, `user_id`, `status`, `task_frame`, `grounded_context_id`, `coverage`, `schema_gap`, `query_plan_id`, `result_ids`, `artifact_ids`, `next_action`, `goal_checklist`, `budgets`, `action_history`, `pending_interrupt`, `schema_version` | `next_action` 为 `RETRIEVE`、`GENERATE`、`EXECUTE`、`ASK_USER`、`RESPOND`、`END` 或 `FAIL` |
| `TraceContext` | `trace_id`, `request_id`, `thread_id`, `user_id`, `route`, `started_at` | `trace_id` 全链路不变；日志中禁止密钥、JWT 原文、敏感原始值和完整结果集 |
| `AppError` | `error_code`, `message`, `trace_id`, `retryable`, `details`, `schema_version` | 错误码必须来自注册表，不能直接把异常类名返回给客户端 |

所有模型使用 UTC 存储时间；面向用户展示时使用 `TaskFrame.timezone`。未知字段默认拒绝，避免模型输出未定义结构被静默接受。

## 5. 配置边界

- 所有密钥只通过环境变量或本地未跟踪的 secret 文件读取；业务配置和 secret 分离。
- 示例配置只能放占位值，不能包含真实 API key、密码或 JWT secret。
- reader、writer、migration 使用不同配置项。
- 环境名至少支持 `local`、`test`、`prod`。
- 配置优先级固定为：显式环境变量 > 本地 secret 文件 > 非敏感 YAML 默认值。
- LLM 配置必须包含 `provider`、`protocol`、`base_url`、`model` 和 secret 引用；M0 只校验配置，不调用 LLM。
- CORS 配置必须包含精确 `cors_origins`、允许方法、允许请求头、credentials 策略和预检缓存时间；不能使用 `*`。

## 6. 错误语义

统一错误至少包含：

```json
{
  "error_code": "CONFIG_MISSING",
  "message": "human readable message",
  "trace_id": "trace_...",
  "retryable": false,
  "details": {}
}
```

## 7. Trace 要求

每个请求至少记录：

- `trace_id`
- `request_id`
- `user_id`
- `route`
- `duration_ms`
- `error_code`

Gateway、Graph 和 Repository 额外必须传递 `catalog_version`、`permission_policy_version` 和 `schema_version`。

不得记录数据库密码、JWT 原文、手机号、身份证或完整结果集。

## 8. 验收标准

- 一个命令可以启动 MySQL、后端和前端开发环境；Milvus 可在 M4 之前关闭。
- `/health` 返回服务、数据库连接和版本信息。
- 空测试集或最小测试集可以稳定运行。
- 任意 API 日志都能看到同一个 `trace_id`。
- 仓库中的示例和模板配置中不存在真实密码、API key 或 JWT secret。
- 核心模型有 Pydantic schema、JSON schema 导出和未知字段拒绝测试。

## 9. 测试证据

- 后端 health check 测试。
- 配置缺失时的错误结构测试。
- 核心模型必填、枚举、版本和未知字段测试。
- Trace ID middleware 测试。
- 前端基础构建或类型检查。
