# Agent 设计

> 返回 [需求文档索引](./需求文档.md) · 一页总览图：[architecture-16x9.html](./architecture-16x9.html)

## 1. Agent 主架构

### 1.1 架构选择

本项目采用 **统一 LangGraph 入口 + 双模式执行**，SQL 安全模块两模式共用：

```text
/api/chat
  → Memory 读入（Session 槽位 + 偏好 JSON / 最近摘要；两模式共用）
  → IntentAnalyzer（轻量模型，结构化输出）
       ├─ intent / slots / need_clarification
       └─ route_mode: react | coordinator
  → ClarificationChecker（若需澄清则 SSE 返回问题并 END）
  → ComplexityRouter（规则可覆盖模型 route_mode）
       ├─ react        → ReAct 子图（单 Agent 选 Tool 循环）
       └─ coordinator  → Coordinator 子图（多步编排，复用同一批 Tool/节点）
            ├─ Schema / Metric 步骤
            ├─ SQL 步骤（生成 → Guardrail → 沙箱 → Repair）
            └─ Chart / Insight 步骤
  → SQLGuardrail → SQLExecutor →（成功且非写）ChartPlanner → AnswerComposer
  → Memory 写回（更新 session_turns；成功则更新偏好 / 追加摘要）
  → END
```

分流原则：

- **简单（react）**：单指标、单时间窗、路径清晰 → ReAct，延迟低、轨迹好读
- **复杂（coordinator）**：多指标对比、多步归因、跨域 JOIN、需多工具协作 → Coordinator 按固定/半固定步骤推进
- **IntentAnalyzer 一次调用同时产出** `intent` 与建议 `route_mode`（见 2.1）；**规则可硬覆盖**（如明显单指标 TopN 强制 react；含「对比 / 归因 / 并且」等多信号强制 coordinator）
- **记忆读写在主图两端、两模式共用**，不是 Coordinator 专属子 Agent
- SQL 权限校验与沙箱为确定性独立模块，不交给模型仲裁，也不使用黑盒 SQL Agent

技术选型：LangGraph 负责编排与状态流转，LangChain 负责 LLM / Tools / Memory 抽象。

### 1.2 主图与 Tool 的关系（避免双路径）

| 概念 | 职责 |
|------|------|
| LangGraph 节点 | 编排步骤（意图、澄清、路由、ReAct 循环体、Coordinator 各步、收尾） |
| Tool Registry | 可调用能力的唯一入口（schema / metric / validate_sql / execute_sql / render_chart） |
| Guardrail + Sandbox | Tool 内部或紧邻校验；**任何模式生成的 SQL 必须经同一路径** |

约束：

1. ReAct 与 Coordinator **不是两套 SQL 实现**：都调用同一批 Tool（及同一 Guardrail / Sandbox）
2. Coordinator 的「子 Agent」= 主图上的**子图节点组**（可绑定更窄的 Tool 子集与 prompt），不是独立进程或旁路 API
3. 图节点可直接调确定性模块（如 ClarificationChecker 规则），也经 Registry 调 Tool；**禁止**节点绕过 Registry 直连执行 SQL
4. `agent_trace` / SSE 记录的是节点与 Tool 事件；AuditLog 挂在 Tool 钩子上

### 1.3 Agent 状态

定义统一状态对象 `AgentState`。

字段至少包含：

```python
class AgentState:
    # 请求与身份（鉴权注入）
    question: str
    session_id: str
    user_id: str
    user_role: str
    request_id: str
    trace_id: str

    # 意图与分流（intent ≠ route_mode）
    intent: str | None              # 业务类别枚举，见 2.1
    intent_confidence: float | None
    intent_summary: str | None
    route_mode: str | None          # "react" | "coordinator"
    route_source: str | None        # "model" | "rule_override"
    slots: dict | None              # 粗槽位（业务词表，非库字段名）

    # Schema / SQL / 结果
    relevant_tables: list[str]
    relevant_columns: dict
    generated_sql: str | None
    checked_sql: str | None
    columns: list[str]
    rows: list[dict]
    affected_rows: int | None

    chart: dict | None
    answer: str | None

    # 澄清与错误
    error: str | None
    repaired: bool
    need_clarification: bool
    clarification_question: str | None

    # 记忆槽位（运行时合并结果）
    session_slots: dict | None
    user_preferences: dict | None
    recent_summaries: list[dict] | None

    agent_trace: list[dict]
    latency_ms: int
```

---

## 2. Agent 节点设计

### 2.1 IntentAnalyzer

作用：**入口轻量结构化理解**——产出业务意图、粗槽位、澄清标记，并**建议**执行模式。

这是主链路第一个模型节点（也可用「规则兜底 + 轻量 LLM」）。

#### 字段分工（勿混用）

| 字段 | 含义 | 是否封闭枚举 |
|------|------|--------------|
| `intent` | **业务问题类别**（渠道分析 / 退款分析…），服务 Schema 裁剪与记忆过滤 | **是**（见下表 + `unknown`） |
| `route_mode` | **编排路径**（`react` / `coordinator`） | 是（仅两值）；由本节点建议，ComplexityRouter 可覆盖 |
| `slots` | **粗槽位**：业务层结构化条件（指标名、时间、维度…），**不是**库表字段名 | 槽位键固定；取值用**受控业务词表**（见下），不是全库列枚举 |
| `need_clarification` | 缺关键信息、会影响 SQL 时先问用户，本轮不跑 SQL | 布尔 |

同一 `intent` 既可以很简单也可以很复杂；**禁止**用 `intent` 枚举值直接决定 ReAct / Coordinator。

#### 主要做什么

1. **业务意图分类**（封闭枚举 + `unknown`）
2. **建议 `route_mode`**（与 intent 分开输出）
3. **抽取粗槽位 `slots`**（便于澄清、多轮合并、给 SchemaRetriever 信号）
4. **标记 `need_clarification`**（细规则可由 ClarificationChecker 二次判定）

#### intent：封闭枚举（不随表增多而膨胀）

意图是粗分类，**不是**「每一种问法一个类型」。表变多时通常仍落在现有类别；新分析域才加枚举项。

- sales_analysis
- product_analysis
- user_analysis
- channel_analysis
- refund_analysis
- conversion_analysis
- payment_analysis
- write_op（admin 受控写意图；analyst 出现时应在后续被权限阻断）
- unknown（落不到类里时使用；默认偏 `react`，除非规则判定复杂）

#### slots：要，但是「薄词表」，不是全库字段

**需要 slots**：没有槽位也能 NL2SQL，但多轮追问、澄清、默认口径会变糊。

slots 枚举的是**经营分析业务概念**，不是数据库列全集。表/列变多时：

- Intent 阶段的 metrics / dimensions 词表**不必**跟着暴涨
- 列级映射放到 SchemaRetriever（`gmv` → `sum(orders.pay_amount)`）
- 词表外说法：`metrics: []` 或无法映射时触发澄清 / 交后续节点处理

第一版受控词表（实现时可放常量文件，随产品少量增补）：

```text
metrics:
  gmv | order_count | aov | refund_rate | conversion_rate |
  payment_success_rate | profit | profit_rate

time_range:
  last_7d | last_30d | last_month | last_quarter | this_month | last_90d

group_by / dimensions:
  channel | province | city | category | brand | payment_method

其他槽位键:
  top_n: int | null
  write_intent: bool
  filters: 可选，仅粗粒度（如 region=华东），不写具体列名
```

#### Prompt 约束（Intent 阶段禁止灌全库 Schema）

IntentAnalyzer 的 prompt **只放**：

- intent 枚举说明
- slots 受控词表与输出 JSON schema
- 分流提示（单指标 TopN → react；多指标/归因 → coordinator）
- 常见默认口径说明（如 GMV 默认支付金额，不必因此澄清）
- 用户问题（及可选：当前 session 槽位摘要）

**禁止**在本阶段放入：全部表结构、全部列名、样例行。全量/相关 Schema 仅出现在 SchemaRetriever / SQLGenerator。

#### 结构化输出示例

用户问题：`上个月 GMV 最高的 5 个渠道是什么？`

```json
{
  "intent": "channel_analysis",
  "confidence": 0.92,
  "summary": "按渠道汇总上月 GMV，取 Top5",
  "route_mode": "react",
  "slots": {
    "metrics": ["gmv"],
    "time_range": "last_month",
    "group_by": ["channel"],
    "top_n": 5,
    "write_intent": false
  },
  "need_clarification": false,
  "clarification_question": null
}
```

歧义示例：`最近哪个渠道表现最好？` → `metrics` 为空、`time_range` 为空、`need_clarification=true`，并给出具体澄清问句（问清指标与时间），不跑 SQL。

#### 实现建议

- 优先：**一次轻量模型调用**产出上表结构（延迟友好）
- 规则覆盖：ComplexityRouter 在模型之后运行，对高置信规则命中可改写 `route_mode`，并设 `route_source=rule_override`
- 模型失败或 `unknown` 时：默认 `react`；明显多步关键词再升为 `coordinator`
- 代码内维护 `METRIC_VOCAB` / `DIMENSION_VOCAB`；与 2.4 口径表通过 metric key 关联，避免 Intent prompt 写死 SQL 表达式

### 2.2 ClarificationChecker

作用：判断问题是否存在明显歧义；为真时请用户**补充/明确缺失信息**（不是笼统「请重新确认」）。

需要澄清的情况：

- “表现最好”没有说明指标（slots.metrics 为空）
- “最近”没有具体时间范围（且无默认可用）
- “转化率”没有说明口径且词表/默认不足以消歧
- “用户质量”这类概念没有定义
- 指标落在词表外且无法可靠映射

注意：

- GMV 等常见指标有默认口径时不必频繁反问
- 只有会明显影响 SQL 的歧义才反问
- 若 IntentAnalyzer 已给出 `need_clarification=true`，本节点可直接确认或按规则收紧/放宽
- 澄清问句应点名缺失项（指标？时间？维度？）

输出示例：

```json
{
  "need_clarification": true,
  "clarification_question": "「表现最好」想按 GMV、订单量还是客单价？时间用最近 7 天还是 30 天？"
}
```

澄清为真时：图直接结束，SSE 推送澄清问题，不进入 SQL 生成。

### 2.3 ComplexityRouter

作用：最终决定走 ReAct 还是 Coordinator（确定性节点，可不调模型）。

输入：IntentAnalyzer 的 **`route_mode`（不是 intent）**、槽位、关键词特征。

规则示例（可落地为代码）：

| 条件 | route_mode |
|------|------------|
| 单指标 + 单时间窗 + 单分组/TopN | react |
| 多指标、同比环比、归因、多表跨域（订单+流量+退款等） | coordinator |
| 模型建议与规则冲突 | **以规则为准**，写 `route_source=rule_override` |

### 2.4 SchemaRetriever

作用：把「intent + slots 业务词」落地为**相关表/字段**；本阶段才注入裁剪后的 Schema。

要求：

- 不把全量 Schema 全部塞给模型；按 intent、slots.metrics / group_by、关键词选择相关表
- 将 slots 中的 metric key **映射**为 SQL 口径与所需列（见下表）
- 输出 relevant_tables 和 relevant_columns
- **不返回**应用表（`app_users`、`chat_sessions` 等）
- 表变大时优先加强本节点的召回/裁剪，而不是扩大 Intent 词表去枚举所有列

指标口径（第一版约定；key 与 Intent `METRIC_VOCAB` 对齐）：

- gmv / GMV = `sum(orders.pay_amount)`（可按时间/状态过滤，默认已支付口径在实现里注明）
- order_count / 订单量 = `count(distinct orders.id)`
- aov / 客单价 = `sum(orders.pay_amount) / count(distinct orders.id)`
- refund_rate / 退款率 = `count(distinct refunds.order_id) / count(distinct orders.id)`（同一时间窗内；实现对齐时间条件）
- payment_success_rate / 支付成功率 = `count(payments where status=success) / count(payments)`（同一时间窗）
- conversion_rate / 转化率 = `count(distinct session_id where is_conversion=1) / count(distinct session_id)`（`traffic_logs`）
- profit / 利润 = `sum( (order_items.unit_price - products.cost) * order_items.quantity - order_items.discount_amount )`（需 JOIN `products`）
- profit_rate / 利润率 = 利润 / `sum(order_items.unit_price * order_items.quantity - order_items.discount_amount)`（分母为销售额，避免除零）

### 2.5 SQLGenerator

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

### 2.6 SQLGuardrail

作用：SQL 安全与权限校验。

公共规则：

- 禁止多语句执行
- 禁止 `DROP`、`ALTER`、`TRUNCATE`、`CREATE` 及访问系统表（如 `sqlite_master`）
- 禁止访问或改写**全部应用表**（`app_users`、`chat_sessions`、`session_turns`、`user_preferences`、`user_analysis_summaries`）
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
- 仍禁止 DDL、多语句、系统表、全部应用表

### 2.7 SQLSandboxExecutor

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

本项目只实现 SQL 沙箱，不实现通用代码沙箱。SQLite 约定：短事务、写操作串行、执行超时，避免与 SSE 长连接互相拖死。

### 2.8 SQLRepairer

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

### 2.9 ChartPlanner

作用：根据查询结果规划图表。**位于主图尾环**——`SQLExecutor` 成功且非写操作时进入本节点，再到 `AnswerComposer`；写操作或空结果直接短路（`chart=None`），主路径**不**经 Tool Registry 调用（Registry 中 `render_chart` 仍注册供 ReAct / 外部扩展使用）。

实现策略：**轻量 LLM + 启发式降级**——优先一次 LLM 调用产出图表配置，失败或字段校验不通过时回退到基于列名/数据类型的启发式规则（日期列→`line`，占比列→`pie`，其余分类+数值→`bar`，无可用列→`table`）。

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

### 2.10 AnswerComposer

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

建议封装 5 个 Tool（ReAct / Coordinator **共用**）。

#### query_schema

用途：查询业务表结构和字段说明（不含应用表；analyst 可隐藏敏感字段元数据）。

#### retrieve_metric_definition

用途：查询业务指标口径（与 2.4 口径表一致）。

#### validate_sql

用途：执行 SQL 安全校验和权限校验（封装 SQLGuardrail）。

#### execute_sql

用途：执行**已通过** `validate_sql` 的 SQL（只读或 admin 受控写）。

- 内部必须再次（或信任紧前校验结果并强制顺序）走沙箱
- `SELECT` / `WITH`：`risk_level=medium`
- `INSERT` / `UPDATE` / `DELETE`：`risk_level=high`，且仅 `admin`；写失败/成功均打 AuditLog
- 描述勿写死 “read-only”；按语句类型区分风险

#### render_chart

用途：生成图表配置（封装 ChartPlanner 或共享其逻辑）。

### 3.3 Tool 元数据

每个 Tool 需要定义：

```json
{
  "name": "execute_sql",
  "description": "Execute validated SQL in sandbox (read or admin controlled write)",
  "input_schema": {},
  "risk_level": "medium",
  "permission_policy": "allow_after_validation",
  "enabled": true
}
```

风险等级：

- low：查询 schema、查询指标口径
- medium：执行只读 SQL、生成图表
- high：受控写 SQL、未来外部 API、大批量导出

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

应用日志**同时输出到 stdout 与 `logs/` 目录文件**，文本格式（便于人工排查）：

```text
INFO 2026-07-26 11:01:00 :     Waiting for application startup.
INFO 2026-07-26 11:01:23 :     request_start request_id=req_xxx path=/api/chat method=POST
INFO 2026-07-26 11:01:24 :     prompt_input mode=tools model=... detail={"messages":[...],"tools":[...]}
INFO 2026-07-26 11:01:25 :     prompt_output mode=tools detail={"content":null,"tool_calls":[...]}
INFO 2026-07-26 11:01:25 :     tool_start tool=execute_sql detail={"args":{...}}
INFO 2026-07-26 11:01:25 :     tool_end tool=execute_sql status=ok detail={"args":{...},"data":{...}}
```

- 应用日志：`logs/YYYY-MM-DD.log`；同日文件超过 10MB 时滚动为 `YYYY-MM-DD_1.log`、`YYYY-MM-DD_2.log`…
- 写操作审计：另附 `logs/audit.jsonl`（JSONL、脱敏，与 Prompt 分离）
- 排查用应用日志尽量完整：每次 LLM 的 `prompt_input` / `prompt_output`（含 messages / tools / content / tool_calls）、Registry 的 `tool_start` / `tool_end`（含完整 args 与 data）

事件类型至少覆盖：

- `run_start` / `run_end`
- `node_start` / `node_end`
- `prompt_input` / `prompt_output` / `llm_tool_calls`
- `tool_start` / `tool_end`（对齐 PreToolUse / PostToolUse 审计时机）
- `guardrail_deny`
- `sql_repair`
- `auth_fail` / `permission_deny`
- `route_decision`（记录 `route_mode` 与 `route_source`）

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

### 4.7 错误与中断收尾

| 情况 | 图行为 | SSE |
|------|--------|-----|
| 需要澄清 | 跳过 SQL，END | `answer` 或专用字段带 `clarification_question`，然后 `done` |
| Guardrail 拒绝 | END | `error`（可展示原因）+ `done` |
| 修复 1 次仍失败 | END | `error` + `done`（`repaired` 按实际） |
| 成功 | AnswerComposer → END | `sql` / `rows` / `chart` / `answer` + `done` |

---

## 5. 记忆管理

实现分层记忆：**Session 槽位 + 跨 Session「偏好 JSON + 最近摘要列表」**。不做向量库 / embedding，不做复杂记忆产品（无自动实体图谱、无多跳记忆推理）。

挂载位置：主图 **入口读、出口写**（见 §1.1），ReAct / Coordinator **共用**；不要做成 Coordinator 内部专属「Memory Agent」。

### 5.1 Session Memory（工作记忆）

用途：支持多轮追问，保存最近分析上下文与槽位。

示例：

第一轮：

> 最近 30 天各渠道 GMV 怎么样？

第二轮：

> 那只看华东地区呢？

Agent 应继承上一轮的指标、时间范围和分组方式，并增加地区过滤条件。

持久化：见 [02-数据库设计](./02-数据库设计.md) 的 `chat_sessions` / `session_turns`。

运行时槽位字段（与 Intent `slots` 对齐的业务层键，持久化时可直接存 JSON）：

- last_question
- last_intent
- last_sql
- last_metrics（如 `["gmv"]`，词表 key）
- last_time_range
- last_filters
- last_group_by
- last_result_summary

要求：

- 按 `session_id`（且归属 `user_id`）隔离
- 每个 session 保留最近 N 轮（默认 10）
- 可用内存缓存 + SQLite 持久化

### 5.2 User Long-term Memory（跨 Session，轻量）

形态固定为两块，避免做成半套记忆平台：

1. **`user_preferences.preferences_json`**：默认时间窗、常用维度、常用图表、指标口径覆盖
2. **`user_analysis_summaries` 最近 M 条摘要列表**：问题摘要、结论摘要、指标/过滤、session 引用

读写约定：

- 追问：先合并当前 Session 槽位；再读取该用户偏好 JSON + 最近摘要列表（例如最近 5 条）增强上下文
- 一轮分析成功结束后：更新偏好（可简单合并）、追加一条摘要（可异步）
- 检索：按 `user_id` 取列表 / 读 JSON，可选关键词过滤；**不做 embedding**
- 敏感字段不得写入
- 支持清理与上限（偏好单行覆盖写；摘要保留最近 M 条）
