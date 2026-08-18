# Data Runtime Agent

面向电商交易数据的自然语言分析系统。分析师用中文提问，系统在权限范围内完成目录检索、查询计划生成、只读 SQL 校验与执行，并返回可引用的结果、表格和运行轨迹。

系统由两部分组成：

- **后端** `backend/`：FastAPI 分析服务与运行图。
- **前端** `frontend/`：React 数据分析工作台，用于对话、结果表、证据栏和线程恢复。

运行时依赖 MySQL（业务数据与控制面）、Milvus（Schema 检索）以及已配置的 LLM / Embedding 服务。

## 项目介绍

业务人员经常需要按店铺、品类、时间窗口查询 GMV、订单、买家数和退款，但不应该直接面对数据库、权限策略和 SQL 安全边界。本系统把自然语言问题转换成受控的只读查询，并保证模型不能绕过权限、表白名单和执行预算。

### 能力

- 用自然语言查询电商交易指标，例如 GMV、品类 GMV、已支付订单数、已支付买家数、退款金额。
- 查询业务表字段和表结构；事实表查询必须带明确时间范围。
- 按用户角色和店铺范围做权限前置过滤，手机号、身份证等敏感分类默认不可见。
- 只允许受控的 `SELECT` / `WITH`；SQL 经过解析、对象校验、行级条件注入、EXPLAIN、超时和行数限制后才执行。
- 结果以 `result_id` 引用，支持分页读取和 CSV 导出；大对象不塞进会话状态。
- 多轮追问可恢复线程、检查点和制品引用；意图不清时会暂停并向用户澄清。
- 管理员对白名单字段的写入走独立审批流程，不执行模型生成的任意 DML。

### 运行链路

```text
用户问题
   │
   ▼
FastAPI  /api/chat  或  /api/chat/stream
   │
   ▼
Runtime Graph
   ├─ 理解任务并选择下一步
   ├─ 按权限召回有限 Schema 上下文
   ├─ 生成受约束的查询计划
   ├─ ReadGateway 校验并执行只读 SQL
   └─ 返回回答、结果引用和运行事件
```

### 数据范围

业务库包含八张电商表：

```text
shops       users        categories   products
orders      order_items  refunds      refund_items
```

运行时使用分账号访问：`agent_reader` 执行分析 SQL，`agent_control` 持久化会话与制品，`agent_writer` 仅用于受控写入，`agent_migration` 仅用于建表、迁移和目录发布。模型拿不到任何数据库连接信息。

## 快速开始

### 环境要求

- Python 3.12（项目约定 conda 环境 `python3.12`）
- Node.js 与 npm（启动前端时需要）
- MySQL 8
- 可用的 LLM 服务；本地 Schema 索引使用 FastEmbed 与 Milvus Lite

### 1. 安装依赖并准备配置

```bash
conda activate python3.12
python -m pip install -e ".[dev]"
cp "config template.yaml" config.yaml
```

在 `config.yaml` 中填写 MySQL 账号密码、JWT 密钥和 LLM 地址。`config.yaml` 已被 git 忽略。加载优先级为：环境变量 > `.secrets.yaml` > `config.yaml`。不要把真实密码、JWT 密钥或 API Key 提交到仓库。

### 2. 初始化数据库

MySQL 需已启动。在项目根目录准备业务库 `data_agent_ecommerce` 和系统库 `data_agent_system`，并写入种子数据：

```bash
export MYSQL_ROOT_PASSWORD='your-root-password'
export MYSQL_MIGRATION_PASSWORD='your-password'
export MYSQL_CONTROL_PASSWORD='your-password'
export MYSQL_READER_PASSWORD='your-password'
export MYSQL_WRITER_PASSWORD='your-password'
bash scripts/setup_databases.sh
```

该脚本会创建两个库、四个最小权限账号、业务表、系统迁移和演示种子。完成后采集 Schema 并构建检索索引：

```bash
make collect-catalog
make index-catalog
```

如需为演示账号设置密码：

```bash
python scripts/set_user_password.py u_demo_user
python scripts/set_user_password.py u_demo_admin
```

### 3. 启动后端

```bash
conda activate python3.12
make backend
```

检查服务：

```bash
curl http://localhost:8000/health
```

`status` 为 `ok` 表示 MySQL 业务库与系统控制面均已连通。`rag.configured` 为 `true` 表示 Schema 检索可用。

### 4. 启动前端

另开终端：

```bash
npm --prefix frontend install
make frontend
```

浏览器打开 <http://localhost:5173/login>。前端默认请求 `http://localhost:8000`；如需更换后端地址，在前端本地环境中设置 `VITE_API_BASE_URL`。

本地开发账号仅用于本机验证，生产环境不得沿用：

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 普通用户 | `u_demo_user` | `DataAgent@2026` |
| 管理员 | `u_demo_admin` | `DataAdmin@2026` |

`u_demo_user` 可访问 `shop_001`、`shop_002`；`u_demo_admin` 额外可访问 `shop_003`。

### 5. 使用 Docker Compose

```bash
export MYSQL_ROOT_PASSWORD='change-me'
export DRA_MYSQL_MIGRATION_PASSWORD='change-me'
export DRA_MYSQL_CONTROL_PASSWORD='change-me'
export DRA_MYSQL_READER_PASSWORD='change-me'
export DRA_MYSQL_WRITER_PASSWORD='change-me'
export DRA_JWT_SECRET='change-me'
export DRA_LLM_BASE_URL='https://api.example.com'
export DRA_LLM_API_KEY='change-me'
export DRA_LLM_MODEL='your-model'
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | <http://localhost:5173> |
| 后端 | <http://localhost:8000> |
| MySQL | `localhost:3306` |

Compose 只拉起容器和网络，首次仍需执行数据库初始化与 `make index-catalog`。

## 端到端示例

下面以本地已启动的前后端为例，走通一次完整问数。

### 工作台

1. 打开 <http://localhost:5173/login>，使用 `u_demo_user` / `DataAgent@2026` 登录。
2. 在输入框提问：

   ```text
   昨天各品类 GMV 是多少？
   ```

3. 工作台会显示运行节点（检索、生成、执行、回答），右侧证据栏给出召回对象、权限范围和结果引用。
4. 回答完成后，中间栏展示自然语言结论和结果表；可分页查看，也可导出 CSV。
5. 在同一线程继续追问，例如：

   ```text
   再看昨天退款总金额
   orders 表有哪些字段？
   ```

   系统会继承当前线程的时间范围、店铺权限和已有结果引用，而不是从零开始。
6. 如果问题有歧义（例如同时命中多个指标或时间窗口不完整），界面会暂停并给出澄清选项。提交答案后，原线程从检查点恢复继续执行。

也可直接尝试：

```text
昨天销售额是多少？
昨天有多少已支付订单？
昨天每个店铺的支付买家数？
上周退款总金额是多少？
最近 7 天日均 GMV？
```

### API

登录页输入账号和密码即可。服务端校验后签发短期会话，前端自动保存并附带到后续请求，不需要手工复制或配置 Token。

登录请求：

```json
POST /api/auth/login
{
  "account": "u_demo_user",
  "password": "DataAgent@2026"
}
```

登录成功后即可提交分析。同步问数：

```json
POST /api/chat
{
  "message": "昨天各品类 GMV 是多少？",
  "timezone": "Asia/Shanghai"
}
```

成功时 `status` 为 `SUCCEEDED`，`answer` 为结论，`result_ids` 为结果引用，`thread_id` 和 `state_version` 用于后续追问。

分页读取结果：`GET /api/results/{result_id}`  
导出 CSV：`GET /api/results/{result_id}/export.csv`

同一线程追问时带上当前 `thread_id` 和 `expected_state_version`，版本过期会返回 `409 CHECKPOINT_CONFLICT`：

```json
POST /api/chat
{
  "message": "再看昨天退款总金额",
  "thread_id": "<thread_id>",
  "timezone": "Asia/Shanghai",
  "expected_state_version": 1
}
```

以 SSE 观察运行过程：`POST /api/chat/stream`，请求体与同步问数相同。

若返回 `WAITING_FOR_USER`，用澄清接口恢复：

```json
POST /api/threads/{thread_id}/interrupts/{interrupt_id}/resume
{
  "answer": "按品类看 GMV",
  "client_request_id": "resume_001",
  "expected_state_version": 1
}
```

### 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 服务与数据库健康检查 |
| `POST` | `/api/auth/login` | 账号密码登录 |
| `GET` | `/api/me` | 当前用户身份与权限策略 |
| `POST` | `/api/chat` | 同步执行一次分析 |
| `POST` | `/api/chat/stream` | 以 SSE 返回运行事件和最终结果 |
| `GET` | `/api/results/{result_id}` | 分页读取结果 |
| `GET` | `/api/results/{result_id}/export.csv` | 导出 CSV |
| `GET` | `/api/threads` | 当前用户的线程列表 |
| `GET` | `/api/threads/{thread_id}` | 恢复线程消息、结果和制品引用 |
| `DELETE` | `/api/threads/{thread_id}` | 删除当前用户的线程 |
| `POST` | `/api/threads/{thread_id}/interrupts/{interrupt_id}/resume` | 恢复等待澄清的线程 |
| `GET` | `/api/artifacts/{artifact_id}` | 校验权限后读取制品 |

身份来自登录会话。请求体不能用来切换用户或扩大权限范围。

## 配置

常用环境变量：

```text
DRA_ENVIRONMENT
DRA_CORS_ORIGINS
DRA_MYSQL_HOST
DRA_MYSQL_PORT
DRA_MYSQL_BUSINESS_DATABASE
DRA_MYSQL_SYSTEM_DATABASE
DRA_MYSQL_MIGRATION_PASSWORD
DRA_MYSQL_CONTROL_PASSWORD
DRA_MYSQL_READER_PASSWORD
DRA_MYSQL_WRITER_PASSWORD
DRA_JWT_SECRET
DRA_LLM_BASE_URL
DRA_LLM_API_KEY
DRA_LLM_MODEL
DRA_LLM_PROTOCOL
```

`config.yaml` 主要区块：

| 区块 | 作用 |
| --- | --- |
| `app` / `server` | 服务名、时区、监听与 CORS |
| `auth` | 登录后的会话 JWT |
| `mysql` | 业务库、系统库和分账号 |
| `milvus` | Schema 检索索引 |
| `catalog` | Schema 采集白名单与字段分类 |
| `runtime_agent` | 迭代、检索轮次、行数和超时预算 |
| `llm` | 模型协议、超时和 Embedding |
| `read_query` | 只读 SQL 形态、时间条件与成本限制 |
| `permissions` | 角色、店铺范围和禁止的数据分类 |

日志和错误输出会脱敏；不要把密码、Token、手机号或身份证号写入配置模板或前端代码。
