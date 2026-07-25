# Phase 2：鉴权 + 基础查询闭环 — Design Spec

> 产品规格以 `docs/` 为准；本文只描述 Phase 2 如何落地。  
> 对齐：`docs/06-开发计划.md` Phase 2、`docs/04-接口与前端.md`、`docs/03-Agent设计.md`（Guardrail 最小子集）、`docs/02-数据库设计.md`（`app_users`）。

## 1. 已确认决策

| 决策 | 选择 |
|------|------|
| Chat 默认路径 | `POST /api/chat` **直接 SSE**；SQL 经**最小只读 Guardrail** 再执行；无旁路直连进默认/Demo 分支 |
| 编排 | **线性函数管线**，不上 LangGraph（Phase 3 再拆节点与图） |
| 注册 | 成功即签发 JWT（响应与 login 同形） |
| 前端深度 | `/` 具备营销气质；`/app` 可用工作台；不做简历级精修（Phase 6） |
| 前端主题 | **浅白色主题**（登录页与工作台统一；避免默认暗色/紫系 AI 模板风） |
| 模块切分 | 分层薄管线：`auth/` + `security/sql_guardrail.py` + `agent/pipeline.py` + `api/chat.py` |
| admin 写操作 | Phase 2 **不做**；管线与 Guardrail 仅 `SELECT`/`WITH` |
| sync 接口 | 本阶段不提供 `POST /api/chat/sync` |

## 2. 范围

### 做

- 注册 / 登录 / me（JWT）
- `GET /api/schema` 纳入鉴权；analyst 隐藏敏感字段元数据
- `GET /api/examples`（≥15 条静态示例）
- `POST /api/chat` SSE + 线性管线（生成 SQL → Guardrail → 执行 → 结论）
- 营销感登录注册页 `/`；工作台 `/app`（回答 / SQL / 表格 / 基础 Trace）
- 角色从 JWT 注入轻量 `AgentState`
- 更新 README 启动说明（注册登录、邀请码、demo 账号）；同步 `docs/04` 中 Phase 1 schema 例外说明

### 不做

- LangGraph、IntentAnalyzer、Clarification、ComplexityRouter、SQLRepairer、ChartPlanner、Memory 写回
- Tool Registry、完整 SQL 沙箱（只读/可写连接分离）、`logs/audit.jsonl`
- admin 受控写 SQL、图表渲染、评测接口
- 任何默认可演示的「跳过 Guardrail 直连 DB」路径

## 3. 目录结构（相对 Phase 1 增量）

```text
backend/app/
├── auth/
│   ├── __init__.py
│   ├── routes.py          # POST register / login；GET me
│   ├── jwt.py             # 签发与解析
│   ├── passwords.py       # bcrypt 哈希与校验
│   └── deps.py            # get_current_user（FastAPI Depends）
├── security/
│   ├── __init__.py
│   └── sql_guardrail.py   # 最小只读校验（独立模块）
├── agent/
│   ├── __init__.py
│   ├── state.py           # 轻量 AgentState
│   ├── llm.py             # OpenAI-compatible 客户端
│   ├── sql_generator.py
│   ├── sql_executor.py    # 仅经 Guardrail 后执行
│   ├── answer_composer.py
│   └── pipeline.py        # 线性编排，产出 SSE 事件
├── api/
│   ├── schema.py          # 鉴权 + analyst 字段过滤
│   ├── examples.py
│   └── chat.py            # SSE 入口
frontend/src/
├── pages/
│   ├── LoginPage.tsx      # `/`
│   └── AppWorkbench.tsx   # `/app`
├── auth/                  # token 存取、ProtectedRoute
└── api/                   # fetch / SSE 封装
```

说明：`sql_guardrail` 放 `security/`（对齐解耦）。`docs/01` 终态目录若写 `agent/sql_guardrail.py`，Phase 4 再统一命名，本阶段不为此纠结。

## 4. 鉴权

### 4.1 密码与 JWT

- 密码：`bcrypt` 哈希存入 `app_users.password_hash`；响应永不含明文或哈希
- JWT：`HS256`，密钥来自 `config.yaml` → `backend.jwt_secret`
- Claims：`sub`（user id 字符串）、`username`、`role`、`exp`
- 默认过期：7 天（常量即可，不必先做配置项）
- 角色只来自服务端用户记录 / JWT；禁止请求体自报 `user_role` 影响权限

### 4.2 接口

**`POST /api/auth/register`**

```json
{
  "username": "alice",
  "password": "********",
  "role": "analyst",
  "invite_code": null
}
```

规则：

- `role=analyst`：无需邀请码
- `role=admin`：必须提供 `invite_code`，与 `backend.admin_invite_code` 校验；邀请码不入库
- 用户名唯一；冲突 → 400
- 邀请码错误 → 400
- 成功响应与 login 同形（直接签发 JWT）：

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user": { "id": "1", "username": "alice", "role": "analyst" }
}
```

**`POST /api/auth/login`** — 用户名密码校验 → 同上响应；失败 → 401。

**`GET /api/auth/me`** — Bearer 校验 → `{ id, username, role }`。

### 4.3 依赖注入

- `get_current_user`：解析 `Authorization: Bearer` → 验签 → 查 `app_users` 确认仍存在
- 缺/坏 token → 401
- 用户不存在 → 401

### 4.4 种子账号

- `init_db` 将现有 `demo_analyst` 的占位哈希改为真实 bcrypt（密码如 `demo1234`，写进 README）
- 不预置 admin；文档说明用邀请码注册

## 5. Schema 与 Examples

### 5.1 `GET /api/schema`（Phase 2+ 需登录）

- 依赖 `get_current_user`
- 仅 `BUSINESS_TABLES`；不含任何应用表
- `analyst`：从 `users` 表列元数据中移除 `name` / `phone` / `email` / `id_card`
- `admin`：可见全部业务字段元数据
- 敏感字段常量与 Guardrail 共用（如 `security` 或 `db` 导出 `SENSITIVE_USER_COLUMNS`）

### 5.2 `GET /api/examples`

- 需登录
- 返回 ≥15 条静态问题，文案对齐 `docs/04` §3
- 形状：`{ "examples": [ { "id": "1", "question": "..." }, ... ] }`

## 6. Chat 管线与安全

### 6.1 轻量 AgentState

至少包含：`question`, `session_id`, `user_id`, `user_role`, `request_id`, `trace_id`,  
`generated_sql`, `columns`, `rows`, `answer`, `error`, `agent_trace`, `latency_ms`。  
本阶段不填 intent / route_mode / slots / chart / memory（字段可预留为 `None`，非必须）。

### 6.2 线性管线

```text
run_start
  → SQLGenerator（LLM + 角色过滤后的业务 schema）
  → SQLGuardrail（失败则 error + done，不执行）
  → SQLExecutor（仅 Guardrail 通过后）
  → AnswerComposer（短结论；LLM 或模板兜底）
  → done
```

- LLM：`openai` SDK，读 `llm.api_key` / `base_url` / `model`
- SQLGenerator：prompt 只含业务表 schema（analyst 已脱敏）；要求只输出一条 SQL；本阶段只生成 `SELECT`/`WITH`
- AnswerComposer：基于 `columns`/`rows`（可截断）生成简短中文结论；LLM 失败时用模板兜底（如「查询返回 N 行」）

### 6.3 最小 SQLGuardrail

默认 chat 路径的唯一执行前校验：

1. 单语句（拒绝额外 `;` 多语句）
2. 仅允许以 `SELECT` 或 `WITH` 开头（忽略前导空白/注释的合理处理即可）
3. 拒绝 DDL / 危险关键字：`DROP` / `ALTER` / `TRUNCATE` / `CREATE` / `ATTACH` / `DETACH` 等
4. 拒绝全部应用表名与 `sqlite_master` 等系统表
5. `analyst`：拒绝敏感标识 `users.name` / `users.phone` / `users.email` / `users.id_card`，以及在明确 `users` 上下文下的裸列名匹配（实现取简单可靠策略，文档写清）
6. 不信任请求体角色；`user_role` 参数必须来自鉴权用户

失败：不调用 executor；SSE 推送脱敏 `error` + `done`。

### 6.4 SQLExecutor（Phase 2 简化）

- **必须**先 `guardrail.check(sql, user_role=...)`；公开 API 无「跳过」开关
- 使用现有 `get_connection()` 执行；结果最多 100 行；无 `LIMIT` 的 SELECT 由执行层追加 `LIMIT 100`
- 错误信息不含堆栈
- 完整只读/可写沙箱连接分离留给 Phase 4；本阶段执行层仍算「经 Guardrail 的受控执行」，不是旁路直连

### 6.5 SSE 事件

| event | data |
|-------|------|
| `run_start` | `request_id`, `trace_id`, `session_id` |
| `node_start` / `node_end` | `node`: `SQLGenerator` \| `SQLGuardrail` \| `SQLExecutor` \| `AnswerComposer`；`node_end` 可带 `summary` |
| `sql` | `{ "sql": "...", "repaired": false }` |
| `rows` | `{ "columns": [...], "rows": [...] }` |
| `answer` | `{ "text": "..." }`（整段结论；本阶段不做 token 流） |
| `error` | `{ "message": "..." }`（已脱敏） |
| `done` | `{ "latency_ms": N, "need_clarification": false, "clarification_question": null }` |

不发：`route_decision` / `tool_*` / `chart` / `token`（后续 Phase）。

## 7. 前端

### 7.1 路由与主题

| 路径 | 说明 |
|------|------|
| `/` | 营销登录 / 注册；已登录可导航至 `/app` |
| `/app` | 工作台；无 token → 重定向 `/` |

- Token：`localStorage` key `daa_token`
- API base：沿用 `frontend/src/config.ts` 的 `__APP_CONFIG__`
- **视觉主题：浅白色**——亮底、深字、克制强调色；登录页可用浅色氛围（细纹理/淡渐变），不做暗色模式；实现时遵循 `frontend-design` skill，并避开紫系/奶油衬线陶土等模板套路

### 7.2 登录注册页（`/`）

- 品牌名 **data-analysis-agent** 为第一视口主信号
- 一句价值主张 + 登录/注册表单
- 注册默认 `analyst`；选 `admin` 显示邀请码
- 成功后写入 token 并进入 `/app`

### 7.3 工作台（`/app`）

**左侧：** 项目名与短说明、当前用户 `username + role`（只读）、示例问题、数据表列表、退出登录  

**右侧：** 自然语言输入；SSE 渐进展示回答、SQL、结果表；可折叠简易 Agent Trace  

**交互：** 点击示例填入输入框；提交建立 SSE；Guardrail/执行错误展示 `error` 文案  

**不做：** 图表区（可隐藏）；客户端改角色

## 8. 依赖增量

后端增加：`PyJWT`、`passlib[bcrypt]`（或 `bcrypt`）、`openai`、`python-multipart`（若表单需要；JSON 注册则可不加）。  
前端增加：`react-router-dom`。  
不引入 LangChain / LangGraph（Phase 3+）。

## 9. 测试（TDD）

| 区域 | 用例要点 |
|------|----------|
| auth | analyst 注册成功发 JWT；admin 无码失败、有码成功；登录成功/失败；`/me` 401/200 |
| schema | 未登录 401；analyst 无敏感列名；admin 有；仍无应用表 |
| guardrail | 多语句 / DDL / 应用表 / analyst 敏感字段 → 拒绝；合法 SELECT → 通过 |
| chat | mock LLM：SSE 含 `run_start`/`sql`/`rows`/`answer`/`done`；Guardrail 拒绝时无 rows、有 error |
| 回归 | 更新既有 `test_schema_api`：改为带 token 请求 |

手工联调：真实 LLM 下至少 5 个 `docs/04` 示例问题成功返回结果。

## 10. 验收对照（docs/06 Phase 2）

| 项 | 标准 |
|----|------|
| 注册 | 可注册 analyst；带邀请码可注册 admin |
| 门禁 | 未登录无法进入工作台 |
| 闭环 | ≥5 个示例问题成功返回结果 |
| 前端 | 展示回答、SQL、表格 |
| 安全 | 无 Guardrail 的临时执行路径不得进入 README Demo / 默认可运行分支 |

## 11. 文档同步

- `docs/04`：删除或改写 Phase 1「schema 暂不鉴权」例外，标明 Phase 2+ 需登录 + analyst 敏感列过滤
- README：注册登录、邀请码、`demo_analyst` 密码、启动步骤；明确默认 chat 经 Guardrail
