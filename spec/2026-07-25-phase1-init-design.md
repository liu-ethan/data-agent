# Phase 1：项目初始化 — Design Spec

> 产品规格以 `docs/` 为准；本文只描述 Phase 1 如何落地。  
> 对齐：`docs/06-开发计划.md` Phase 1、`docs/02-数据库设计.md`、`docs/04-接口与前端.md`（schema 分阶段例外）、`docs/01-需求总览.md` 目录约定。

## 1. 已确认决策

| 决策 | 选择 |
|------|------|
| `/api/schema` 鉴权 | Phase 1 **暂不鉴权**；Phase 2 再纳入 JWT（已同步 `docs/04`、`docs/06`） |
| 前端范围 | 仅 Vite + React + TS + Tailwind 脚手架能 `npm run dev`；营销页 / 工作台留 Phase 2（已同步 `docs/06`） |
| 数据访问 | 标准库 `sqlite3` + 手写 DDL / 种子；不引入 SQLAlchemy / Alembic |

## 2. 范围

### 做

- 创建 `backend/`（FastAPI）与 `frontend/`（Vite 脚手架）
- 配置：仓库根目录 `config.yaml`（模板 `config_template.yaml`；含 llm / backend / frontend）
- SQLite：8 张业务表 + 5 张应用表；业务模拟数据（含 `users` 敏感字段列）
- 结构化 JSON 日志骨架（`request_id`；响应头 `X-Request-Id`）
- `GET /api/schema`：仅业务表结构，不鉴权

### 不做

- JWT / 注册 / 登录 / me
- LangGraph / Agent 节点 / Tool / Guardrail / 沙箱
- 营销登录页与工作台 UI
- `logs/audit.jsonl` 落盘（Phase 4）
- 预建空的 `auth/`、`agent/`、`tools/`、`security/` 目录

## 3. 目录结构

```text
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口、CORS、request_id 中间件、路由挂载
│   ├── config.py               # pydantic-settings
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py         # DB 路径、get_connection
│   │   ├── schema.py           # DDL；BUSINESS_TABLES / APP_TABLES
│   │   └── init_db.py          # 建库 + 种子；python -m app.db.init_db
│   ├── api/
│   │   ├── __init__.py
│   │   └── schema.py           # GET /api/schema
│   └── log/
│       ├── __init__.py
│       └── logging.py          # JSON 日志 + contextvars request_id
├── tests/
│   ├── test_init_db.py
│   └── test_schema_api.py
└── requirements.txt

config_template.yaml            # 配置模板（可提交）
config.yaml                     # 本地配置（gitignore）
frontend/                       # Vite + React + TS + Tailwind；默认页即可
```

库文件默认路径：`backend/data/ecommerce.db`（gitignore，由 init 生成）。

## 4. 数据库与种子

### 4.1 表

字段与约束对齐 `docs/02-数据库设计.md`。

**业务表（进入 schema 响应）：**  
`users`, `products`, `orders`, `order_items`, `payments`, `refunds`, `campaigns`, `traffic_logs`

**应用表（建表但不进 schema）：**  
`app_users`, `chat_sessions`, `session_turns`, `user_preferences`, `user_analysis_summaries`

`schema.py` 导出：

- `BUSINESS_TABLES: frozenset[str]`
- `APP_TABLES: frozenset[str]`

后续 Guardrail 复用同一常量，禁止业务 SQL 访问应用表。

### 4.2 种子规则

- 业务表合计行数 ≥ 1000（以 `orders` / `order_items` / `traffic_logs` 为主）
- 时间跨度覆盖最近 180 天
- 多渠道、城市、品类、支付方式、退款原因
- `users` 必须含 `name` / `phone` / `email` / `id_card`（可用掩码形态，列必须存在）
- 可支撑 GMV、订单量、退款率、转化率、客单价、利润率等分析
- 应用表可空；可选预置 1 个演示 `analyst`（`password_hash` 占位，Phase 2 接真实哈希）
- `init_db` **幂等覆盖**：重复执行删除/重建库文件或清空后重种；README / 模块 docstring 标明会覆盖本地库

### 4.3 连接

- `database.py`：`get_connection()` → `sqlite3.Connection`，`row_factory=sqlite3.Row`
- 路径来自 `Settings.database_path`，默认 `backend/data/ecommerce.db`

## 5. API 契约

### 5.1 `GET /api/schema`（Phase 1 不鉴权）

响应示例：

```json
{
  "tables": [
    {
      "name": "orders",
      "columns": [
        {"name": "id", "type": "INTEGER", "nullable": false},
        {"name": "pay_amount", "type": "REAL", "nullable": true}
      ]
    }
  ]
}
```

规则：

- 仅 `BUSINESS_TABLES`；响应中不得出现任何 `APP_TABLES` 表名
- 列信息来自 `PRAGMA table_info`（或等价）；Phase 1 含敏感字段列名
- 不返回表内数据行

### 5.2 可选 `GET /health`

返回 `{"status":"ok"}`，便于探活；非验收硬性项。

### 5.3 CORS

允许本地前端源（至少 `http://localhost:5173`）。

## 6. 配置

仓库根目录统一配置：

- 模板：`config_template.yaml`（可提交）
- 本地：`config.yaml`（gitignore；`cp config_template.yaml config.yaml`）
- 段：`llm` / `backend` / `frontend`（前后端与大模型同一文件）
- 后端：`app.config` 读取；测试可用 `APP_CONFIG` 指向临时 yaml
- 禁止再用 `.env` / `.env.example` 作为运行配置

## 7. 可观测（骨架）

`log/logging.py`：

- stdout JSON 行日志
- 字段子集对齐 `docs/03`：`ts`, `level`, `request_id`, `event`, `latency_ms`, 可选 `detail`
- 中间件：生成 `request_id` → contextvars → 响应头 `X-Request-Id`
- Phase 1 事件：`request_start` / `request_end`；schema 成功时 `event=schema_served`
- `trace_id` 可与 `request_id` 相同或省略；不写 `audit.jsonl`

## 8. 前端

- `npm create vite`（React + TS）+ Tailwind
- 默认 Vite 页即可；无路由、无登录、无工作台
- 验收：`npm run dev` 成功启动

## 9. 测试

TDD 轻量覆盖：

1. `test_init_db`：执行 init 后，库中存在 8 业务 + 5 应用表；业务合计行数 ≥ 1000；`users` 有敏感列
2. `test_schema_api`：`GET /api/schema` → 200；`tables` 恰好 8 个业务表名；无应用表名

## 10. 启动与验收

```bash
# backend
cp config_template.yaml config.yaml   # 填写本地密钥
cd backend
# 强制使用 AGENTS.md 指定的 conda python3.12（禁止系统 python / 仓库 .venv）
/home/user/miniconda3/envs/python3.12/bin/pip install -r requirements.txt
/home/user/miniconda3/envs/python3.12/bin/python -m app.db.init_db
/home/user/miniconda3/envs/python3.12/bin/uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && npm install && npm run dev
```

| 项 | 标准 |
|----|------|
| 后端 | uvicorn启动；`GET /api/schema` 200 |
| 前端 | `npm run dev` 打开默认页 |
| DB | `ecommerce.db` 存在；表齐全；业务行数 ≥ 1000 |
| schema | 恰好 8 张业务表；无应用表 |

Phase 1 完成后可微调 README「本地启动」使命令可执行；不写完整 Demo 叙事。

## 11. 依赖（Phase 1）

后端大致：`fastapi`, `uvicorn[standard]`, `pydantic-settings`, `python-dotenv`, `pytest`, `httpx`（TestClient）。  
不引入 LangChain / LangGraph（Phase 3+）。
