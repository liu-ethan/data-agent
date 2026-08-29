# data-agent

<p align="center">
  <strong>电商经营分析数据 Agent</strong><br/>
  用一句话问 GMV、客单价、退款率；改 SKU 状态或库存，管理员确认后才落库。
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white" />
  <img alt="LangGraph" src="https://img.shields.io/badge/Orchestration-LangGraph-1C3C3C" />
  <img alt="React" src="https://img.shields.io/badge/UI-React_18-61DAFB?logo=react&logoColor=222" />
  <img alt="MySQL" src="https://img.shields.io/badge/Facts-MySQL-4479A1?logo=mysql&logoColor=white" />
  <img alt="SQLite" src="https://img.shields.io/badge/Control_Plane-SQLite_×6-003B57?logo=sqlite&logoColor=white" />
</p>

单 **Coordinator** 调度查询 Skill 与写入 Skill：自然语言进 FastAPI，LangGraph 完成意图识别、候选澄清与 HITL，指标由 `MetricCompiler` 按审核口径编译，SQL 经网关参数化执行。业务事实、Agent 控制面、查询结果分库存放，职责清晰。

| 亮点 | 做法 |
| --- | --- |
| 口径可信 | 审核指标落在 `seeds/`，模型不现场编公式、不加 JOIN |
| 查询可追溯 | Schema RAG → 骨架 SQL → 只读网关 → `CompiledQuery` 参数化执行 |
| 写入可审批 | SKU 状态 / 库存调整先出预览，管理员确认后提交，回执与审计进 MySQL |
| 存储隔离 | MySQL 业务事实 · SQLite 控制面 · Parquet 查询结果，三类各司其职 |
| 资源可审核 | Prompt、SQL、指标与写入模板全部外置，改口径不用改代码 |

```text
  用户  ──►  React Workbench (:5173)          登录 / 会话 / 对话 / HITL / 表&图
                    │  /api proxy
                    ▼
             FastAPI Gateway (:8000)          /auth  /chat  /interrupts  /results
                    │
                    ▼
         ┌──────────────────────┐
         │  Coordinator         │  意图 → 候选澄清(HITL) → 调度 Skill → 接地回答
         │  LangGraph + SqliteSaver
         └──────────┬───────────┘
              ┌─────┴──────┐
              ▼            ▼
     Query Skill      Write Skill
     Schema RAG       模板预览
     MetricCompiler   HITL 审批
     Read Gateway     Write Gateway
     MySQL SELECT     受控 UPDATE
              │            │
              ▼            ▼
     Parquet 明细      MySQL 回执 / 审计
     results.sqlite    da_write_receipt
                       da_write_audit

  存储职责
  ┌─────────────────┬──────────────────────────────┬─────────────────┐
  │ MySQL           │ SQLite × 6                   │ Parquet         │
  │ 业务事实        │ users / catalog / embeddings │ data/results/   │
  │ + 回执 + 审计   │ checkpoint / runtime / results│ 查询结果行     │
  └─────────────────┴──────────────────────────────┴─────────────────┘
```

---

## 快速启动

**环境：** Python 3.12+、Node.js 20+、MySQL（需 `mysql` 客户端）、OpenAI 兼容的 LLM / Embedding。

### 1. 安装后端

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
cp config.example.yaml config.yaml
```

在 `config.yaml` 中填入 MySQL、LLM、Embedding 与 `auth.jwt_secret`。密钥只放本地，不进仓库。

### 2. 初始化存储

```bash
python scripts/apply_mysql_slice.py   # 业务库 DDL、种子数据、账号权限
python scripts/init_sqlite.py         # 六个 SQLite 控制面 + 默认用户 / Catalog
```

| 用户 | 密码 | 角色 | 能力 |
| --- | --- | --- | --- |
| `admin` | `admin` | operator | 问数 + 审批写入 |
| `analyst` | `analyst` | analyst | 只读问数 |

### 3. 连通性

```bash
python scripts/check_connectivity.py
```

也可单独跑 `check_mysql.py` / `check_sqlite.py` / `check_llm.py` / `check_embedding.py`。

### 4. 启动

两个终端：

```bash
# API  http://127.0.0.1:8000
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

```bash
# UI   http://127.0.0.1:5173  （/api 代理到后端）
cd frontend && npm install && npm run dev
```

浏览器打开前端，使用 `admin` / `admin` 登录。启动时会将 MySQL `information_schema` 同步进 Catalog，问数即可对齐真实表结构。

---

## 仓库地图

| 路径 | 作用 |
| --- | --- |
| `backend/` | FastAPI、Coordinator、Query / Write Skill、网关、Catalog、结果存储 |
| `frontend/` | React 工作台（Vite，开发时把 `/api` 代理到 `:8000`） |
| `prompts/` | 全部 LLM Prompt（身份 / 任务 / 约束 / 输出 / few-shots） |
| `sql/` | MySQL / SQLite DDL 与命名查询 |
| `seeds/` | 表关系、指标、写入模板、产品文案 |
| `scripts/` | 业务库落地、SQLite 初始化、连通性检查 |
| `tests/` | 后端单测 · 前端 Vitest · Playwright E2E |
| `config.example.yaml` | 配置模板，复制为 `config.yaml` 后本地填写 |

## 存储设计

业务事实、控制面、查询结果分库存放，互不混写。

| 位置 | 职责 |
| --- | --- |
| **MySQL** `data-agent-ecommerce` | 电商业务事实；写入回执 `da_write_receipt`；审计 `da_write_audit` |
| **SQLite** `users.sqlite` | 本地用户、角色、权限版本 |
| **SQLite** `catalog.sqlite` | 表 / 列 / 关系 / 指标 / 写入操作定义 |
| **SQLite** `embeddings.sqlite` | Schema 向量 |
| **SQLite** `checkpoint.sqlite` | LangGraph 会话检查点 |
| **SQLite** `runtime.sqlite` | 会话列表投影 |
| **SQLite** `results.sqlite` | 结果元数据（路径、状态、TTL、所有者） |
| **Parquet** `data/results/` | 查询结果明细 |

Checkpoint 保存 Task、HITL payload 与结果引用；权限每次实时加载；写入是否成功以 MySQL 回执为准。

## 问数与写入

指标公式、表关系与写入模板以 `seeds/` 为准。问数覆盖订单、商品、门店、渠道、流量、支付与售后等经营主题。

可问指标包括 GMV、实付 GMV、净 GMV、订单量、客单价、退款率、转化率、新客数、复购率、广告 ROI。

写入支持 SKU 状态更新与库存调整：先出预览，管理员 HITL 确认后再提交，并写入回执与审计。

## 测试

```bash
pytest tests -v
cd frontend && npm test
cd frontend && npx playwright test
```
