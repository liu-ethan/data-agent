# DataInsight Agent

[![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?style=flat&logo=langchain&logoColor=white)](https://github.com/langchain-ai/langgraph)
[![LangChain](https://img.shields.io/badge/LangChain-1C3C3C?style=flat&logo=langchain&logoColor=white)](https://github.com/langchain-ai/langchain)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-20232A?style=flat&logo=react&logoColor=61DAFB)](https://react.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

面向电商经营分析的自然语言数据分析 Agent。

基于 [LangGraph](https://github.com/langchain-ai/langgraph) 做状态图编排，基于 [LangChain](https://github.com/langchain-ai/langchain) 提供 LLM / Tools / Memory 抽象。用户用自然语言提问，系统自动完成意图识别、Schema Linking、SQL 生成、权限校验、沙箱执行、错误修复，并以 SSE 流式输出表格、图表与分析结论。

---

## 目录

- [为什么不是普通 Text-to-SQL](#为什么不是普通-text-to-sql)
- [架构](#架构)
- [技术栈](#技术栈)
- [功能一览](#功能一览)
- [本地启动](#本地启动)
- [角色与权限](#角色与权限)
- [SQL Guardrail 与沙箱](#sql-guardrail-与沙箱)
- [Trace / AuditLog](#trace--auditlog)
- [记忆](#记忆)
- [界面预览](#界面预览)
- [评测](#评测)
- [失败案例分析](#失败案例分析)
- [后续扩展](#后续扩展)
- [License](#license)

---

## 为什么不是普通 Text-to-SQL

| 普通 Demo | DataInsight Agent |
|-----------|-------------------|
| 一次 prompt 出 SQL | LangGraph 状态图：节点可观测、可修复 |
| 模型直连数据库 | Guardrail + 沙箱，角色差异化权限 |
| 同步黑盒结果 | SSE 流式展示节点 / Tool 轨迹 |
| 无账号体系 | JWT 登录；analyst / admin（邀请码） |
| 无治理 | Tool Registry + AuditLog（与 Prompt 分离） |

主链路：

```text
自然语言问题
  → 意图识别 → Schema Linking → SQL 生成
  → 权限校验 → 沙箱执行 → 错误修复
  → 图表规划 → 分析结论
```

分流：

- **简单问题** → 单 Agent ReAct
- **复杂问题** → Coordinator（Schema / SQL / Chart·Insight / Memory）

---

## 架构

```text
                    ┌─────────────────────────────────────┐
                    │     /  营销登录·注册（JWT）            │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │     /app 工作台（SSE Chat）            │
                    └─────────────────┬───────────────────┘
                                      │
┌─────────────────────────────────────▼─────────────────────────────────────┐
│                         FastAPI + LangGraph                                 │
│  ComplexityRouter                                                          │
│       ├─ ReAct（简单）                                                      │
│       └─ Coordinator（复杂）                                                 │
│            Tools: query_schema / metric / validate_sql / execute_sql / chart │
│            Guardrail → Sandbox → Repair → Compose                          │
│            AuditLog (JSONL)  ⟂  agent_trace (SSE / UI)                     │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  SQLite：8 张业务表 + app_users        │
                    └─────────────────────────────────────┘
```

编排选型：**[LangGraph](https://github.com/langchain-ai/langgraph)**（状态图）+ **[LangChain](https://github.com/langchain-ai/langchain)**（LLM / Tools / Memory）。  
SQL 安全为确定性独立模块，不使用黑盒 SQL Agent。

官方文档：[LangGraph Docs](https://docs.langchain.com/oss/python/langgraph/overview) · [LangChain Docs](https://docs.langchain.com/oss/python/langchain/overview)

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React · Vite · TypeScript · TailwindCSS · Recharts |
| 后端 | Python · FastAPI · Pydantic · JWT · SSE |
| Agent | [LangGraph](https://github.com/langchain-ai/langgraph) · [LangChain](https://github.com/langchain-ai/langchain) |
| 数据 | SQLite（业务库 + 应用账号） |
| 模型 | OpenAI 兼容 API（`.env` 配置） |

---

## 功能一览

- 自然语言经营分析（GMV、退款率、转化率、渠道 TopN 等）
- 注册 / 登录；`admin` 需邀请码
- SSE 流式 Trace、SQL、结果表、图表、结论
- analyst 只读 + 敏感字段拦截；admin 受控写（INSERT/UPDATE/DELETE）
- Session 多轮追问 + 跨 Session 结构化长期记忆（无向量）
- 评测集与指标脚本

---

## 本地启动

> 下列命令对应目标目录结构；若某模块尚未合入，以当前仓库状态为准。

### 1. 环境变量

复制并填写（**不要提交真实密钥**）：

```bash
cp backend/.env.example backend/.env
```

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
JWT_SECRET=change-me
ADMIN_INVITE_CODE=your-invite-code
```

| 变量 | 说明 |
|------|------|
| `OPENAI_*` | 兼容 OpenAI 的模型服务 |
| `JWT_SECRET` | 签发登录 Token |
| `ADMIN_INVITE_CODE` | 注册 `admin` 时必填，与服务端校验 |

### 2. 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.db.init_db    # 初始化业务表 + app_users + 模拟数据
uvicorn app.main:app --reload --port 8000
```

### 3. 前端

```bash
cd frontend
npm install
npm run dev
```

浏览器打开前端地址（默认 `http://localhost:5173`）：

1. 在 `/` 注册：默认 `analyst`；选 `admin` 时填写邀请码  
2. 登录后进入 `/app` 工作台提问  

### 4. 示例问题

- 上个月 GMV 最高的 5 个渠道是什么？  
- 最近 30 天每天的订单量和 GMV 趋势如何？  
- 哪些商品品类的退款率最高？  

---

## 角色与权限

| | analyst | admin |
|--|---------|--------|
| 注册 | 直接注册 | 需 `ADMIN_INVITE_CODE` |
| SELECT | 经营数据 OK | 全部业务字段 |
| 敏感字段 | 拦截（如姓名等） | 允许 |
| INSERT / UPDATE / DELETE | 禁止 | 允许（业务表） |
| DROP / ALTER / 多语句 / 系统表 / `app_users` | 禁止 | 禁止 |

角色来自服务端 JWT，**前端不可自行切换角色**。

---

## SQL Guardrail 与沙箱

**Guardrail（执行前）**

- 解析语句类型与涉及表字段  
- 按角色允许 / 拒绝  
- 明细查询补 `LIMIT`；写操作限制影响行数  
- 不通过则阻断，不进沙箱  

**Sandbox（执行时）**

- analyst → 只读连接；admin → 可写连接  
- 超时、行数 / 影响行数上限  
- 错误脱敏，不回传堆栈  
- 写操作记入 AuditLog，Trace 标记高风险  

---

## Trace / AuditLog

参照常见 AI 应用治理：**展示轨迹** 与 **审计日志** 分离，且默认不注入 Prompt。

| 类型 | 用途 |
|------|------|
| `agent_trace` + SSE | 前端可解释展示（`node_*` / `tool_*`） |
| `AuditLog`（JSONL） | 排查、权限与写操作追溯 |

关联字段：`request_id` · `trace_id` · `session_id` · `user_id` · `user_role`  

Tool 路径固定 PreToolUse → 执行 → PostToolUse；日志脱敏（密码、JWT、邀请码、Key、敏感字段明文）。

---

## 记忆

| 层 | 内容 | 存储 |
|----|------|------|
| Session | 近 N 轮槽位（指标、时间、过滤、SQL 摘要等） | 内存 + session 表 |
| 用户长期 | 偏好、常用口径、历史分析摘要 | SQLite，按 `user_id` |

不做 embedding / 向量检索。敏感字段不入长期记忆。

---

## 界面预览

实现后将截图放到 `assets/` 并在此引用：

| 页面 | 占位 |
|------|------|
| 营销登录 / 注册页 | `![登录页](./assets/auth-landing.png)` |
| 工作台 | `![工作台](./assets/workspace.png)` |
| Agent Trace / SSE | `![Trace](./assets/agent-trace.png)` |

截图随前端完善后补齐。

---

## 评测

```text
backend/app/eval/questions.json    # ≥30 条
backend/app/eval/run_eval.py
backend/app/eval/eval_result.json  # 输出
```

指标：

- `execution_success_rate`
- `repair_success_rate`
- `schema_hit_rate`
- `permission_block_success_rate`
- `average_latency_ms`

```bash
cd backend && python -m app.eval.run_eval
```

### 评测结果

| 指标 | 结果 |
|------|------|
| execution_success_rate | _待跑评测后填写_ |
| repair_success_rate | _待填写_ |
| schema_hit_rate | _待填写_ |
| permission_block_success_rate | _待填写_ |
| average_latency_ms | _待填写_ |

---

## 失败案例分析

实现与评测后在此补充典型失败与对策，例如：

| 场景 | 现象 | 处理 |
|------|------|------|
| 模糊指标 | 触发澄清，不盲生成 SQL | ClarificationChecker |
| 危险 SQL | Guardrail 阻断 | analyst 写操作 / DDL |
| 执行报错 | 最多 1 次 Repair，再过 Guardrail | SQLRepairer |
| 权限不足 | SSE `error` + AuditLog | 字段 / 角色策略 |

---

## 后续扩展

- MCP Tool Provider  
- 用户上传 OpenAPI Tool Manifest  
- 适配 CodeBuddy SDK  
- 接入真实企业数仓  
- 更严格的语义评测  

**不做**：真实支付 / 订单 / CRM 等外部业务系统接入。

---

## License

本项目采用 [MIT License](./LICENSE) 开源。
