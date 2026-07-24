# Agent 设计

> 返回 [需求文档索引](./需求文档.md)

## 1. Agent 主架构

### 1.1 架构选择

本项目采用：

```text
LangGraph 状态图
  ├─ ComplexityRouter（规则优先）
  ├─ 简单问题 → 单 Agent ReAct
  │     观察 → 选 Tool → 执行 → 修复 → 回答
  └─ 复杂问题 → Coordinator 多 Agent
        ├─ Schema / Metric Agent
        ├─ SQL Agent（生成 → Guardrail → 沙箱 → Repair）
        ├─ Chart / Insight Agent
        └─ Memory Agent（读写 Session + 用户长期记忆）
```

分流原则：

- 简单问题：单指标、单时间窗、路径清晰 → ReAct，降低延迟与轨迹噪音
- 复杂问题：多指标对比、多步归因、跨域 JOIN、需多工具协作 → Coordinator
- ComplexityRouter 以规则为主（关键词、intent、槽位完整度）；不确定时再轻量用模型判定
- SQL 权限校验与沙箱执行为确定性独立模块，不交给模型仲裁，也不使用黑盒 SQL Agent

技术选型：LangGraph 负责编排与状态流转，LangChain 负责 LLM / Tools / Memory 抽象。

### 1.2 Agent 状态

定义统一状态对象 `AgentState`。

字段至少包含：

```python
class AgentState:
    question: str
    session_id: str
    user_role: str

    intent: str | None
    relevant_tables: list[str]
    relevant_columns: dict

    generated_sql: str | None
    checked_sql: str | None

    columns: list[str]
    rows: list[dict]

    chart: dict | None
    answer: str | None

    error: str | None
    repaired: bool
    need_clarification: bool
    clarification_question: str | None

    agent_trace: list[dict]
    latency_ms: int
```

---

## 2. Agent 节点设计

### 2.1 IntentAnalyzer

作用：识别用户问题类型。

支持 intent：

- sales_analysis
- product_analysis
- user_analysis
- channel_analysis
- refund_analysis
- conversion_analysis
- payment_analysis
- unknown

输出：

- intent
- confidence
- summary

实现建议：

- 可以用规则 + LLM
- 第一版可以先用规则，后续再接 LLM

### 2.2 ClarificationChecker

作用：判断问题是否存在明显歧义。

需要澄清的情况：

- “表现最好”没有说明指标
- “最近”没有具体时间范围
- “转化率”没有说明口径
- “用户质量”这类概念没有定义

注意：

- GMV 默认定义为 `orders.pay_amount`
- 常见指标有默认口径时不必频繁反问
- 只有会明显影响 SQL 的歧义才反问

输出示例：

```json
{
  "need_clarification": true,
  "clarification_question": "你想按订单金额还是支付金额统计 GMV？"
}
```

### 2.3 SchemaRetriever

作用：根据问题选择相关表和字段。

要求：

- 不把全量 Schema 全部塞给模型
- 根据 intent、关键词、指标口径选择相关表
- 输出 relevant_tables 和 relevant_columns
- 支持指标口径映射

指标口径：

- GMV = sum(orders.pay_amount)
- 订单量 = count(orders.id)
- 客单价 = sum(orders.pay_amount) / count(orders.id)
- 退款率 = count(refunds.id) / count(orders.id)
- 支付成功率 = success_payments / total_payments
- 转化率 = conversion_sessions / total_sessions
- 利润 = sum(order_items.unit_price - products.cost)

### 2.4 SQLGenerator

作用：生成 SQLite SQL。

要求：

- 只生成 SQL，不生成解释文本
- 优先使用相关 Schema
- 时间范围要明确
- 聚合查询要有清晰别名
- 按 `user_role` 约束生成语句类型：
  - `analyst`：只生成 `SELECT` / `WITH`，避免敏感字段
  - `admin`：可生成 `SELECT` / `WITH` / `INSERT` / `UPDATE` / `DELETE`；写操作须有明确用户意图
- 不生成 DDL（`DROP` / `ALTER` / `TRUNCATE` / `CREATE`）

### 2.5 SQLGuardrail

作用：SQL 安全与权限校验。

公共规则：

- 禁止多语句执行
- 禁止 `DROP`、`ALTER`、`TRUNCATE`、`CREATE` 及访问系统表（如 `sqlite_master`）
- 禁止改写应用账号表 `app_users`
- 明细 `SELECT` 自动追加 `LIMIT 100`；写操作限制影响行数上限
- SQL 不安全时直接阻断，不进入执行阶段
- `user_role` 来自鉴权用户，不信任请求体自报角色

敏感字段 denylist（对 analyst）：

- users.name
- users.phone
- users.email
- users.id_card

角色权限：

analyst：

- 可以查询订单、商品、支付、退款、活动、流量等经营数据
- 不可以查询用户姓名等敏感字段
- 只允许 `SELECT` / `WITH`

admin：

- 可以查询全部业务字段（含敏感字段）
- 允许对业务表执行 `INSERT` / `UPDATE` / `DELETE`
- 仍禁止 DDL、多语句、系统表、`app_users`

### 2.6 SQLSandboxExecutor

作用：在受控环境中执行 SQL。

要求：

- 按角色选择连接：`analyst` 只读连接；`admin` 可写连接
- SQL 必须先通过 SQLGuardrail
- 查询最大返回 100 行；写操作返回 `affected_rows`
- 设置查询 / 执行超时
- 捕获错误并返回简化错误信息
- 不暴露后端堆栈
- 写操作必须写入 AuditLog / agent_trace（高风险标记）

说明：

本项目只实现 SQL 沙箱，不实现通用代码沙箱。

### 2.7 SQLRepairer

作用：当 SQL 执行失败时自动修复。

要求：

- 最多修复 1 次
- 修复时输入：
  - 原始问题
  - 原始 SQL
  - 执行错误
  - 相关 Schema
- 修复后的 SQL 必须重新经过 SQLGuardrail
- 修复失败则返回清晰错误说明

### 2.8 ChartPlanner

作用：根据查询结果规划图表。

支持：

- line：时间趋势
- bar：TopN、分类对比
- pie：占比
- table：明细数据

输出：

```json
{
  "type": "bar",
  "x": "channel",
  "y": "gmv",
  "title": "上月各渠道 GMV Top5"
}
```

### 2.9 AnswerComposer

作用：生成中文分析结论。

要求：

- 基于 SQL 查询结果生成
- 包含关键数字、排名、趋势或异常点
- 不编造不存在的数据
- 结果为空时说明可能原因
- 说明 SQL 被修复或权限被拦截的情况

---

## 3. Tool 设计

### 3.1 是否需要 Tool Registry

需要。

第一版先做内置 Tool + 轻量 Registry；后续可扩展 MCPToolProvider 与用户 OpenAPI Tool Manifest。

原因：

- Tool Registry 能体现工具治理和权限管理
- 无论内置、MCP 还是用户声明的 Tool，都走同一套风险等级、权限策略与审计
- SQL 类高风险操作仍必须 `allow_after_validation`

### 3.2 第一版内置 Tools

建议封装 5 个 Tool。

#### query_schema

用途：查询数据库表结构和字段说明。

#### retrieve_metric_definition

用途：查询业务指标口径。

示例：

- GMV
- 客单价
- 退款率
- 转化率
- 支付成功率

#### validate_sql

用途：执行 SQL 安全校验和权限校验。

#### execute_sql

用途：执行通过校验的只读 SQL。

#### render_chart

用途：生成图表配置。

### 3.3 Tool 元数据

每个 Tool 需要定义：

```json
{
  "name": "execute_sql",
  "description": "Execute read-only SQL in sandbox",
  "input_schema": {},
  "risk_level": "medium",
  "permission_policy": "allow_after_validation",
  "enabled": true
}
```

风险等级：

- low：查询 schema、查询指标口径
- medium：执行只读 SQL、生成图表
- high：未来外部 API 调用、大批量导出

权限策略：

- allow：直接允许
- allow_after_validation：校验后允许
- deny：拒绝

不实现 ask 流程，避免 MVP 过度复杂。

---

## 4. Trace Log / AuditLog

参照常见 AI 应用治理做法（结构化审计日志 + Tool 前后钩子），将**可观测轨迹**与**Prompt 上下文**分离。

### 4.1 两类轨迹

| 类型 | 用途 | 是否注入 Prompt |
|------|------|-----------------|
| `agent_trace` | 前端展示、SSE 事件、评测可读轨迹 | 否（仅展示） |
| `AuditLog` | 后端排查、权限审计、写操作追溯 | 否（默认不进 Prompt） |

原则：AuditLog 回答「发生过什么」；`AgentState` / checkpoint 回答「当前状态」；记忆系统不把完整工具日志灌进模型。

### 4.2 关联字段

每次 `/api/chat` 生成：

- `request_id`：单次 HTTP / SSE 请求
- `trace_id`：一次 Agent 运行（可与 request_id 相同或一对多）
- `session_id` / `user_id` / `user_role`

所有节点日志、Tool 日志、SSE 事件、AuditLog 行共用上述 ID，便于串联。

### 4.3 结构化日志格式

后端使用 JSON 行日志（stdout + 可选 `logs/audit.jsonl` 追加写）。

单条字段建议：

```json
{
  "ts": "2026-07-25T00:00:00Z",
  "level": "INFO",
  "request_id": "req_xxx",
  "trace_id": "tr_xxx",
  "session_id": "default",
  "user_id": "u_1",
  "user_role": "admin",
  "event": "tool_end",
  "node": "SQLSandboxExecutor",
  "tool": "execute_sql",
  "status": "ok",
  "latency_ms": 42,
  "detail": {
    "sql_fingerprint": "update_campaigns_budget",
    "affected_rows": 1,
    "risk_level": "high"
  }
}
```

事件类型至少覆盖：

- `run_start` / `run_end`
- `node_start` / `node_end`
- `tool_start` / `tool_end`（对齐 PreToolUse / PostToolUse 审计时机）
- `guardrail_deny`
- `sql_repair`
- `auth_fail` / `permission_deny`

### 4.4 Tool 钩子

在 Tool Registry 调用路径上固定：

1. **PreToolUse**：记录 tool 名、入参摘要、风险等级；若策略为 deny 则阻断并打 `permission_deny`
2. **执行 Tool**
3. **PostToolUse**：记录状态、耗时、出参摘要 / `affected_rows`；失败只记脱敏错误

写 SQL（`INSERT` / `UPDATE` / `DELETE`）必须落 AuditLog；写入失败只告警，不作为业务状态唯一来源（状态以执行结果与 `AgentState` 为准）。

### 4.5 脱敏

日志与 SSE error 中禁止输出：

- 密码、JWT、邀请码、API Key
- analyst 场景下的敏感字段明文（姓名、手机、邮箱、身份证等）
- 完整后端堆栈

SQL 可记 fingerprint 或截断文本；大结果集只记行列数与样例行数。

### 4.6 与 SSE / 前端关系

- SSE 的 `node_*` / `tool_*` 事件是 `agent_trace` 的实时投影
- AuditLog 可比前端展示更全，但字段需脱敏
- 前端 Trace 面板不直接读磁盘日志，只消费 SSE / 最终汇总

---

## 5. 记忆管理

实现分层记忆：**Session（工作记忆）+ 跨 Session 结构化长期记忆**。不做向量库 / embedding 语义检索。

### 5.1 Session Memory（工作记忆）

用途：

- 支持多轮追问
- 保存最近分析上下文与槽位

示例：

第一轮：

> 最近 30 天各渠道 GMV 怎么样？

第二轮：

> 那只看华东地区呢？

Agent 应继承上一轮的指标、时间范围和分组方式，并增加地区过滤条件。

Session 保存字段：

- last_question
- last_intent
- last_sql
- last_metrics
- last_time_range
- last_filters
- last_group_by
- last_result_summary

要求：

- 按 `session_id` 隔离
- 每个 session 保留最近 N 轮（建议默认 10）
- 可用内存缓存 + SQLite session 表持久化

### 5.2 User Long-term Memory（跨 Session，结构化）

用途：跨会话复用用户偏好与历史分析结论，避免每次从零开始。

持久化内容（SQLite，按 `user_id`）：

- 用户偏好：默认时间窗、常用维度、常用图表类型、角色
- 常用口径：用户确认或高频使用的指标定义（如 GMV 口径）
- 历史分析摘要：问题摘要、关键结论、所用指标/过滤条件、对应 session 引用

读写约定：

- 追问：先合并当前 Session 槽位；需要时再读取该用户的偏好与近期摘要增强上下文
- 一轮分析结束后：写回/更新摘要与偏好（可异步）
- 检索方式：按 `user_id` + 字段/关键词匹配（intent、metric、时间范围等），**不做 embedding 向量检索**
- 敏感字段（姓名、手机、邮箱、身份证等）不得写入长期记忆
- 按 `user_id` / `session_id` 隔离；支持清理与上限（例如每用户保留最近 M 条摘要）
