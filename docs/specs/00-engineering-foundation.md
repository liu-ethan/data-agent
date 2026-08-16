# Spec 00：工程基线

状态：`Draft`

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

必须先定义以下模型的最小字段，后续 spec 可以扩展：

```text
TaskFrame
ContextFrame
PermissionContext
GroundedContext
CoverageResult
SchemaGap
QuerySpec
QueryPlan
ResultObservation
ArtifactSpec
AgentState
TraceContext
AppError
```

## 5. 配置边界

- 所有密钥只通过环境变量读取。
- 示例配置只能放占位值。
- reader、writer、migration 使用不同配置项。
- 环境名至少支持 `local`、`test`、`prod`。

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

不得记录数据库密码、JWT 原文、手机号、身份证或完整结果集。

## 8. 验收标准

- 一个命令可以启动 MySQL、后端和前端开发环境。
- `/health` 返回服务、数据库连接和版本信息。
- 空测试集或最小测试集可以稳定运行。
- 任意 API 日志都能看到同一个 `trace_id`。
- 配置文件中不存在真实密码。

## 9. 测试证据

- 后端 health check 测试。
- 配置缺失时的错误结构测试。
- Trace ID middleware 测试。
- 前端基础构建或类型检查。

