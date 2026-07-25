# Phase 4：Tool Registry、权限、沙箱与 AuditLog — Design Spec

> 产品规格以 `docs/` 为准；本文只描述 Phase 4 如何落地。  
> 对齐：`docs/06-开发计划.md` Phase 4、`docs/03-Agent设计.md` §2.6–2.7 / §3 / §4、`docs/04-接口与前端.md` SSE。  
> 承接：`spec/2026-07-25-phase3-langgraph-sse-design.md`（LangGraph 单路径 + SSE）。

## 1. 已确认决策

| 决策 | 选择 |
|------|------|
| 整体架构 | 轻量自研 Tool Registry（非 LangChain Tool 包装、非双轨旁路） |
| 图集成 | 保留 Phase 3 节点名与顺序；`SQLGuardrail` / `SQLExecutor` 节点内部只调 Registry |
| ReAct 选 Tool | 不上（Phase 5） |
| `render_chart` | 实现可 invoke 的 Tool（简易 chart config）；主图不自动调用；不推 `chart` SSE；前端不画图 |
| admin 写 SQL | Guardrail + 沙箱 + `execute_sql` 完整支持；单元/集成测试覆盖；SQLGenerator 仍只生成只读 SQL |
| `tool_*` 可观测 | pipeline 推 SSE `tool_start`/`tool_end`；工作台 Trace 展示；同步落 `logs/audit.jsonl` |
| SchemaRetriever 等 | 仍可直调确定性模块；**禁止**节点绕过 Registry 直连执行 SQL |
| 写影响行上限 | 事务内执行；`changes() > 100` 则 rollback 并返回错误（上限常量 `MAX_WRITE_ROWS=100`） |

## 2. 范围

### 做

- 轻量 Tool Registry + Pre/Post Tool 审计钩子
- 5 个内置 Tool：`query_schema` / `retrieve_metric_definition` / `validate_sql` / `execute_sql` / `render_chart`
- 升级 `SQLGuardrail`：按角色（analyst 只读；admin 受控写）；字段级敏感列；禁 DDL / 多语句 / 系统表 / 全部应用表
- 实现 `SQLSandboxExecutor`：analyst 只读连接 / admin 可写连接；超时；读 LIMIT 100；写 `affected_rows` + 行数上限
- 落盘 `logs/audit.jsonl`（脱敏）；`.gitignore` 忽略 `logs/`
- SSE + 前端 Trace 展示 `tool_*`
- README 同步 Phase 1–4：Registry、角色差异、沙箱、AuditLog

### 不做

- ComplexityRouter / ReAct / Coordinator 子图（Phase 5）
- Memory 读/写、SQLRepairer
- 主图自动调用 `render_chart`、前端图表渲染、完整 ChartPlanner（Phase 6）
- MCPToolProvider / 用户 OpenAPI Tool Manifest
- admin NL→写 SQL（生成器本阶段仍只读）
- `ask` 人工确认权限策略、通用代码沙箱

## 3. 目录结构（相对 Phase 3 增量）

```text
backend/app/tools/
├── __init__.py
├── schemas.py             # ToolSpec / ToolContext / ToolResult
├── registry.py            # register / invoke + Pre/Post
├── audit.py               # append logs/audit.jsonl + redact
└── builtins.py            # 注册 5 个内置 Tool 实现

backend/app/security/
├── sql_guardrail.py       # 升级：角色化读写
└── sql_sandbox.py         # 分角色连接、超时、LIMIT、写上限

backend/app/agent/
├── nodes/sql_guardrail_node.py   # registry.invoke("validate_sql")
├── nodes/sql_executor_node.py    # registry.invoke("execute_sql")
├── pipeline.py                   # 映射 tool_* SSE
└── sql_executor.py               # 删除或薄委托到 sandbox（禁止旁路入口）

frontend/src/pages/AppWorkbench.tsx   # Trace 处理 tool_start / tool_end
README.md
.gitignore                            # logs/
```

说明：

- Guardrail / Sandbox 保持确定性独立模块；Tool 与节点只编排调用
- `query_schema` / `retrieve_metric_definition` 可复用现有 schema API 与 `metrics.py` 逻辑，不必改 SchemaRetriever 节点必经 Tool

## 4. Tool 元数据与 Registry

### 4.1 ToolSpec

每个 Tool：

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

| name | risk_level | permission_policy | 主图 |
|------|------------|-------------------|------|
| `query_schema` | low | allow | 不自动调 |
| `retrieve_metric_definition` | low | allow | 不自动调 |
| `validate_sql` | low | allow_after_validation | SQLGuardrail 节点 |
| `execute_sql` | medium（SELECT/WITH）/ high（I/U/D） | allow_after_validation | SQLExecutor 节点 |
| `render_chart` | medium | allow | 不自动调 |

`execute_sql` 的对外元数据 `risk_level` 可标 `medium`（描述写明写操作为 high）；**实际审计行**按语句类型写入 `detail.risk_level`（读 medium / 写 high）。

### 4.2 ToolContext / ToolResult

```text
ToolContext:
  request_id, trace_id, session_id, user_id, user_role, node

ToolResult:
  ok: bool
  data: dict | None
  error: str | None          # 已脱敏、无堆栈
  events: list[dict]         # 供 pipeline 投影为 SSE（tool_start/tool_end 等）
```

`user_role` **只**来自 `ToolContext`（JWT→AgentState），忽略 args 内自报角色。

### 4.3 invoke 路径

```text
1. PreToolUse：查 registry；disabled / deny → permission_deny + AuditLog，不执行
2. 执行 handler(args, context)
3. PostToolUse：status / latency_ms / 出参摘要；写 AuditLog
4. 返回 ToolResult（含 events）
```

写 SQL（INSERT/UPDATE/DELETE）：成功与失败均落 AuditLog（`risk_level=high`）。  
Audit 写盘失败：打 warning 日志，不改变 `ToolResult` / AgentState。

### 4.4 内置 Tool 行为摘要

| Tool | 行为 |
|------|------|
| `query_schema` | 返回业务表结构；analyst 隐藏敏感列元数据（对齐 `/api/schema`） |
| `retrieve_metric_definition` | 按 metric key 返回口径（`metrics.get_metric_spec`）；未知 key 返回清晰错误 |
| `validate_sql` | 调 `check_sql`；返回 `{ok, reason}` |
| `execute_sql` | 再调 Guardrail → `SQLSandboxExecutor`；读返回 columns/rows；写返回 `affected_rows` |
| `render_chart` | 根据 columns/rows 启发式返回 `{type,x,y,title}`（table/bar/line/pie 简易规则）；不依赖前端 |

## 5. SQLGuardrail（升级）

公共规则：

- 禁止多语句（分号分割，忽略字符串内分号）
- 禁止 DDL：`DROP` / `ALTER` / `TRUNCATE` / `CREATE` / `ATTACH` / `DETACH` / `PRAGMA`（及现有危险关键字）
- 禁止系统表（如 `sqlite_master`）与**全部应用表**
- 空 SQL / 未知角色 → 拒绝

角色：

| 角色 | 允许语句 | 敏感列 |
|------|----------|--------|
| analyst | `SELECT` / `WITH` | 拒 `users.name/phone/email/id_card` 及 `*` / 别名绕过（沿用 Phase 2/3 检测） |
| admin | `SELECT` / `WITH` / `INSERT` / `UPDATE` / `DELETE` | 允许读敏感列 |

实现注意：

- 从「全员只读」改为：写关键字对 **analyst** 禁止；对 **admin** 仅允许 I/U/D 作为语句类型（仍禁 DDL）
- 语句类型判定：去掉前导注释后以 `SELECT|WITH|INSERT|UPDATE|DELETE` 开头
- `REPLACE` 仍禁止（两边都不放）

## 6. SQLSandboxExecutor

```text
execute(sql, *, user_role, ...) ->
  Guardrail 不通过 → 不连库，返回错误
  analyst → sqlite3.connect + PRAGMA query_only=ON
  admin   → 普通可写连接（无 query_only）
  SELECT/WITH → 无 LIMIT 则包装 LIMIT 100；返回 columns/rows
  I/U/D → 事务执行；若 changes() > MAX_WRITE_ROWS(100) → rollback + 错误
         否则 commit；返回 affected_rows
  超时 → connection 设置 timeout（默认 5s）；错误信息简化，无堆栈
```

约束：

- 短事务；写操作依赖 SQLite 文件锁串行，不引入额外分布式锁
- 默认 chat 路径：`SQLExecutor` 节点 → Registry → sandbox；删除节点对旧 `sql_executor.execute_sql` 的直接调用（该模块可删或改为 sandbox 的私有委托，不得作为公开旁路 API）

## 7. AuditLog

- 路径：仓库根或 backend 约定的 `logs/audit.jsonl`（实现固定为项目根 `logs/audit.jsonl`，与 README 一致）
- 追加 JSON Lines；字段对齐 docs/03 §4.3：

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
  "node": "SQLExecutor",
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

事件至少覆盖本阶段实际产生的：`tool_start` / `tool_end` / `permission_deny` / `guardrail_deny`（可由 validate/execute 失败映射）。

脱敏：

- 禁止：密码、JWT、邀请码、API Key、完整堆栈
- analyst 场景不落敏感列明文
- SQL：`detail.sql` 截断至 ≤200 字符，并带 `sql_fingerprint`（规范化空白后的短 hash 或首关键字+表名摘要）；大结果只记行列数

## 8. 图节点与 SSE

主图顺序不变：

```text
… → SQLGenerator → SQLGuardrail → SQLExecutor → AnswerComposer
```

- `sql_guardrail_node`：`registry.invoke("validate_sql", {sql}, context)`；失败写 `state.error`
- `sql_executor_node`：`registry.invoke("execute_sql", {sql}, context)`；读结果写入 columns/rows；失败写 error
- 节点在 delta 中附带 `tool_events: list[{event, data}]`（来自 `ToolResult.events`，用后可由下一节点覆盖，不要求累积进长期 state）
- pipeline 在对应 `node_start` 之后、`node_end` 之前依次 yield 这些 `tool_*` 事件

SSE（pipeline）：

- 新增投影：`tool_start` / `tool_end`（payload：`tool`、`summary` 或 status/risk 摘要）
- 既有：`node_*` / `route_decision` / `sql` / `rows` / `answer` / `error` / `done`
- 本阶段不发 `chart`

前端：`AppWorkbench` 对 `tool_start`/`tool_end` `pushTrace`，文案含 tool 名与简要 status。

## 9. README / 文档同步

- 状态改为 Phase 1–4 已落地
- 说明：Tool Registry 为 SQL 执行唯一入口；analyst/admin 差异；沙箱分角色；AuditLog 路径与脱敏；`tool_*` SSE
- 标明：双模式子图 / Repair / 图表 UI / Memory 仍为后续 Phase
- 若 `docs/04` 与实现事件名一致则只核对；不一致做最小同步（不扩写 Phase 5+）

## 10. 测试策略（TDD）

| 区域 | 关键用例 |
|------|----------|
| Guardrail | analyst 拒 INSERT/敏感/DDL；admin 允 UPDATE 业务表；两边拒 DDL/多语句/app 表/`REPLACE` |
| Sandbox | 未过 Guardrail 不执行；analyst 连接拒写；admin 写返回 affected_rows；写超 100 行 rollback |
| Registry | 5 Tool 已注册；hooks 顺序；deny 不执行；execute 写操作 AuditLog 有行 |
| Tools | query_schema 对 analyst 藏敏感元数据；retrieve_metric 已知/未知 key；render_chart 返回合法 config |
| Chat SSE | happy path 含 `tool_start`/`tool_end`；澄清路径仍无 sql |
| 回归 | Phase 3 graph/chat/clarification/guardrail 只读路径 |

## 11. 验收对照（docs/06 Phase 4）

| 项 | 标准 |
|----|------|
| analyst | 写操作与敏感字段被阻断；DDL 被阻断 |
| admin | 允许 INSERT/UPDATE/DELETE；DDL / 多语句 / 全部应用表仍阻断 |
| 无旁路 | SQL 必须通过 Guardrail 才能执行；默认 chat 无直连 DB |
| 可观测 | Tool 调用有 SSE Trace + AuditLog 记录 |

## 12. 全局约束

- Python：仅 conda `python3.12`；配置仅根目录 `config.yaml`
- 只在本仓库工作区改代码；禁止用 git worktree 做功能开发
- Agent 不自动 `git commit`；完成后汇报建议 message，由用户提交
- 一次只改 Phase 4 相关文件；不做无关重构
- 跨 Phase 安全硬约束：默认可演示 chat 路径 SQL 必须经 Guardrail 再进沙箱
