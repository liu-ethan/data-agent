# Data Runtime Agent

面向电商交易数据的证据驱动分析运行时 MVP。用户可以用自然语言提问，系统会依次完成任务识别、权限优先的目录检索、查询计划生成、只读 SQL 校验与执行，并返回结果引用和可追踪的运行事件。

项目包含两个可独立启动的部分：

- `backend/`：基于 FastAPI 的分析 API 和有限状态运行图。
- `frontend/`：基于 React + Vite 的证据分析工作台，可查看回答、结果表和运行轨迹。

运行时不再提供内置 SQLite 数据链路。服务需要 MySQL（业务数据、权限、会话和制品）、Milvus（Schema RAG）和结构化 LLM/Embedding 服务均已配置；SQLite 仅作为单元测试注入适配器存在。

## 功能概览

- 支持电商交易场景中的 GMV、品类 GMV、已支付订单数、已支付买家数和退款金额等查询。
- 支持查询业务表字段和表结构；数据查询会要求明确的时间范围。
- 通过版本化 Pydantic 合约约束任务、目录上下文、查询计划、结果观察和运行状态。
- 统一的结构化 LLM Client 支持 Anthropic-compatible（包括 MiniMax）与 OpenAI-compatible 协议，具备连接复用、超时、有界重试、Token 用量和模型 Trace。
- 模型生成的 QueryDraft 在进入 ReadGateway 前必须通过 GroundedContext 的表、字段、指标、时间字段和 SQL AST 校验；最终回答必须引用当前 `result_id`。
- 目录检索先应用用户权限，再形成有限大小的上下文，支持字段分类和数据范围约束。
- `ReadGateway` 只允许受控的 `SELECT`/`WITH` 查询，并执行 SQL 解析、对象校验、时间条件、权限范围、行数和成本检查。
- 结果通过 `result_id` 引用，支持分页读取；前端支持结果表查看和 CSV 下载。
- 支持 SSE 运行事件、线程状态、检查点、用户澄清中断和可重放的恢复请求。
- 内置安全测试和固定案例评估，可生成 JSON/CSV 评估报告。

整体运行链路如下：

```text
用户问题
   │
   ▼
FastAPI /api/chat
   │
   ▼
RuntimeGraph
   ├─ RETRIEVE  权限优先的目录检索
   ├─ GENERATE  生成受约束的 QueryPlan
   ├─ EXECUTE   ReadGateway 校验并执行只读查询
   └─ RESPOND   返回回答、结果引用和 Trace 事件
```

## 快速开始

### 环境要求

- Python 3.12；项目约定使用 `python3.12`。
- Node.js 和 npm；仅启动前端时需要。
- MySQL 8、Milvus Lite/standalone，以及可用的 LLM 与 Embedding 服务。

### 1. 安装 Python 依赖并创建配置

在项目根目录执行：

```bash
conda activate python3.12
python -m pip install -e ".[dev]"
cp "config template.yaml" config.yaml
```

`config.yaml` 已被 `.gitignore` 忽略。应用启动时会读取它；配置加载优先级为：环境变量 > `.secrets.yaml` > `config.yaml`。本地演示可以直接使用模板中的非生产配置，不要把真实密码、JWT 密钥或 API Key 提交到仓库。

### 2. 启动后端

在项目根目录激活项目约定的 Python 环境后启动：

```bash
conda activate python3.12
make backend
```

检查服务：

```bash
curl http://localhost:8000/health
```

只有 MySQL、持久化控制面和 Schema RAG 都健康时才会返回 `status: "ok"`；`degraded` 表示当前环境未达到可执行分析任务的条件。

### 3. 启动前端（可选）

另开终端，在项目根目录安装依赖并启动：

```bash
npm --prefix frontend install
make frontend
```

浏览器打开 <http://localhost:5173/login>，使用应用账号和密码登录。前端默认请求 `http://localhost:8000`；如需连接其他后端地址，请通过前端本地配置文件设置 `VITE_API_BASE_URL`，不要在启动命令中硬编码环境变量。

### 4. 账号密码登录

登录页使用 `app_users.user_id + 密码` 登录，不需要用户手工获取或粘贴 JWT。服务端验证数据库中的 scrypt 密码哈希后签发短期 JWT，前端仅把它作为当前页面的 API 会话凭证。

当前本地开发数据库的测试账号如下，仅用于 localhost 功能验证，生产环境不得沿用：

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 普通用户 | `u_demo_user` | `DataAgent@2026` |
| 管理员 | `u_demo_admin` | `DataAdmin@2026` |

首次配置账号密码，执行以下命令并按提示输入两次密码：

```bash
python scripts/set_user_password.py u_demo_user
```

也可以为 `u_demo_admin` 设置独立密码。密码明文不会写入配置、日志或数据库；数据库只保存 `password_hash`。

在输入框中可以尝试：

```text
昨天各品类 GMV 是多少？
昨天销售额是多少？
昨天有多少已支付订单？
orders 表有哪些字段？
```

### 5. 直接调用 API

提交一次分析请求：

```bash
read -r -s -p 'Password: ' LOGIN_PASSWORD
echo
TOKEN=$(jq -nc --arg account 'u_demo_user' --arg password "$LOGIN_PASSWORD" \
  '{account:$account,password:$password}' | \
  curl -s -X POST http://localhost:8000/api/auth/login \
    -H 'Content-Type: application/json' --data-binary @- | jq -r .access_token)
unset LOGIN_PASSWORD
curl -s http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "message": "昨天各品类 GMV 是多少？",
    "timezone": "Asia/Shanghai"
  }'
```

响应中的 `status` 通常为 `SUCCEEDED`，`result_ids` 包含结果引用。使用结果引用分页读取数据：

```bash
curl -s 'http://localhost:8000/api/results/<result_id>' -H "Authorization: Bearer $TOKEN"
```

以 SSE 查看运行事件：

```bash
curl -N 'http://localhost:8000/api/chat/stream?message=昨天销售额是多少？' -H "Authorization: Bearer $TOKEN"
```

当目录检索无法唯一确定用户意图时，响应会是 `WAITING_FOR_USER`，并携带 `interrupt`。客户端应使用 `POST /api/threads/{thread_id}/interrupts/{interrupt_id}/resume` 提交澄清答案，并带上检查点版本和幂等请求 ID。

## API 端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 检查服务状态和配置摘要 |
| `POST` | `/api/auth/login` | 使用应用账号和密码建立 API 会话 |
| `POST` | `/api/chat` | 执行一次同步分析 |
| `GET` | `/api/chat/stream` | 以 SSE 返回运行事件和最终结果 |
| `GET` | `/api/results/{result_id}` | 分页读取结果数据 |
| `GET` | `/api/results/{result_id}/export.csv` | 受鉴权导出完整 CSV |
| `GET` | `/api/threads` | 读取当前用户的线程列表 |
| `GET` | `/api/threads/{thread_id}` | 恢复线程消息、结果和制品引用 |
| `GET` | `/api/artifacts/{artifact_id}` | 重新校验权限和目录版本后读取制品 |
| `POST` | `/api/threads/{thread_id}/interrupts/{interrupt_id}/resume` | 恢复等待用户澄清的线程 |

`POST /api/chat` 的最小请求体：

```json
{
  "message": "昨天销售额是多少？",
  "thread_id": null,
  "timezone": "Asia/Shanghai",
  "request_id": null,
  "expected_state_version": null
}
```

向已有线程发起新一轮请求时，必须提交线程详情返回的 `expected_state_version`；陈旧版本返回 `409 CHECKPOINT_CONFLICT`。新线程保持为 `null`。

用户通过账号密码登录；服务端签发的会话 JWT 用于后续 API 鉴权。请求体中的旧 `user_id` 仅作一致性校验，不能选择权限范围。内置本地身份为：

- `u_demo_user`：可访问 `shop_001`、`shop_002`。
- `u_demo_admin`：可访问 `shop_001`、`shop_002`、`shop_003`。

## 测试、评估与构建

完成配置后，在项目根目录执行：

```bash
# 单元测试、API 测试和安全测试
python3.12 -m pytest -q

# 或使用 Makefile
make test

# 对 config.yaml 指向的外部 MySQL 执行真实 reader/RLS/结果持久化集成测试
make test-mysql

# Python 语法检查
make lint

# 仅运行确定性兼容回归（报告会显式标记为非生产）
python3.12 scripts/run_evaluation.py --allow-test-double

# 通过真实鉴权 HTTP、MySQL、Milvus 和模型运行生产评测；令牌由账号登录后提供
python scripts/run_production_evaluation.py --account u_demo_user --limit 10

# 运行 70 条真实 Schema Linking / 权限前置检索评测
make evaluate-rag

# 前端类型检查和生产构建
cd frontend
npm run typecheck
npm test
npm run test:e2e
npm run build
```

旧的 SQLite/固定规则评测只可用于兼容回归，报告会显式标记为 `non_production`，不再产生或宣传生产 Task Completion Rate、LLM 延迟或 Token 成本。生产评测必须在固定的 MySQL 数据版本、Milvus index 版本和模型版本上通过 API 执行并比较 Golden Result。

## 配置说明

配置模板为根目录的 `config template.yaml`，主要配置区块如下：

| 区块 | 作用 |
| --- | --- |
| `app` / `server` | 服务名称、时区、监听地址和 CORS 来源 |
| `auth` | 密码登录后的会话 JWT 参数 |
| `mysql` | 外部 MySQL 连接和分账号配置 |
| `catalog` | MySQL Schema 采集源、表白名单、粒度和字段分类覆盖 |
| `milvus` | Milvus 连接、四层 collection 基名和索引批次 |
| `runtime_agent` | 迭代次数、检索轮数、查询数、行数和超时预算 |
| `retrieval_budget` | 候选对象、字段、上下文 token 和歧义阈值 |
| `read_query` | 只读 SQL 的语句类型、时间条件、表数、Join 数和成本限制 |
| `permissions` | 角色、店铺范围和禁止的数据分类 |
| `memory` / `artifacts` | 检查点、短期/长期记忆和结果制品限制 |
| `evaluation` | 评估案例目录、报告目录和固定时间锚点 |

代码支持的常用环境变量包括：

```text
DRA_ENVIRONMENT
DRA_CORS_ORIGINS          # 逗号分隔
DRA_MYSQL_HOST
DRA_MYSQL_PORT
DRA_MYSQL_DATABASE
DRA_LLM_API_KEY
DRA_JWT_SECRET
DRA_MYSQL_MIGRATION_PASSWORD
DRA_MYSQL_CONTROL_PASSWORD
DRA_MYSQL_READER_PASSWORD
DRA_MYSQL_WRITER_PASSWORD
```

日志或错误输出应使用代码提供的脱敏逻辑；不要把密码、Token、手机号、身份证号等敏感值写入 README、测试报告或前端代码。

## 数据与外部 MySQL

MySQL 是唯一运行时数据源，包含以下八张业务表：

```text
shops       users        categories   products
orders      order_items  refunds      refund_items
```

仓库也提供 MySQL 初始化脚本，适用于准备外部数据环境：

1. `scripts/mysql_schema.sql` 创建业务表。
2. `migrations/001_catalog.sql` 创建版本化语义目录表。
3. `scripts/catalog_seed.sql` 写入人工维护的指标、别名和审核 Join。
4. `migrations/002_runtime_state.sql` 至 `007_password_auth.sql` 创建运行时持久化、Checkpoint 历史、密码认证、BM25 词项和索引 Manifest。
5. `scripts/mock_mysql_data.sh seed` 仅在开发环境写入可重复的 `seed_v1` 业务数据。
6. `python3.12 scripts/index_catalog.py --collect-only` 从 `information_schema` 采集白名单 Schema 并生成内容版本。
7. `python3.12 scripts/index_catalog.py --index-only` 使用真实 Embedding 构建四层 staging collections，校验后切换 active manifest。

也可用 `make collect-catalog` 只采集 MySQL，或用 `make index-catalog`
一次完成采集与索引。Milvus Lite 不支持 collection alias，因此原子
切换由 MySQL active manifest 完成；失败的 staging collection 不会替换当前版本。

运行时使用 `agent_reader` 执行分析 SQL，使用 `agent_control` 持久化控制面状态；`agent_migration` 只用于建表、迁移、Schema 采集和索引发布。LLM 无法取得任何数据库连接信息。

## Docker Compose

可以使用 Compose 启动示例服务：

```bash
export MYSQL_ROOT_PASSWORD='change-me'
export MYSQL_MIGRATION_PASSWORD='change-me'
docker compose up --build
```

服务地址：

- 前端：<http://localhost:5173>
- 后端：<http://localhost:8000>
- MySQL：`localhost:3306`

当前 Compose 文件只负责服务容器和网络，不自动写入业务种子或构建 Milvus 索引。首次启动仍需显式执行迁移、种子和 `scripts/index_catalog.py`；后端不会回退到 SQLite。

## 项目结构

```text
backend/
  app/
    api/           FastAPI、JWT 和 SSE 交付边界
    graph/         五个顶层 LangGraph Node 和运行时编排
    gateways/      不可绕过的只读 SQL 安全边界
    services/      LLM、Schema 采集/检索、Embedding、权限和 Trace 服务
    repositories/  生产 MySQL 数据/目录、Milvus 索引与运行状态适配器
    memory/        Checkpoint、Artifact 和记忆实现
    models/        版本化 Pydantic 合约
    ports/         Graph 与基础设施之间的依赖倒置接口
    bootstrap.py   生产依赖组合根
    testing.py     显式确定性测试组合
    testing_adapters/ SQLite 与内存测试替身
frontend/
  src/             React 分析工作台
scripts/           MySQL、种子数据和评估脚本
tests/             单元、API、检索、记忆和安全测试
migrations/        目录表迁移
```

## 当前边界

- 生产运行强制依赖 MySQL、Milvus、Embedding 和结构化 LLM；SQLite、内存目录和无模型分支仅用于显式测试注入。
- JWT 身份、权限范围、检查点、SSE 事件、线程、结果与制品均由服务端持久化并重新鉴权；相对时间使用请求时的实时钟表并保存绝对范围。
- 只读网关使用 SQLGlot AST、RLS、EXPLAIN、MySQL reader 权限校验、只读事务、超时和结果契约。Admin `INSERT/UPDATE` 的 WriteGateway 与审批 HITL 仍属于 M6 延后范围，当前 API 不暴露写入口。
- 固定 SQLite 评测是 `non_production` 兼容回归；生产完成率和成本指标必须由配置完整的真实 API 评测产生。
