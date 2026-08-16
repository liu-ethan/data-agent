# MySQL 本地环境脚本

`mysql_env.sh` 用于初始化和排查 Data Runtime Agent 的本地 MySQL 环境。

## 初始化

在项目根目录执行：

```bash
chmod +x scripts/mysql_env.sh
scripts/mysql_env.sh init
```

脚本不包含默认密码。交互式运行时会以隐藏输入方式询问；CI 或非交互环境必须通过 `MYSQL_ROOT_PASSWORD`、`MYSQL_MIGRATION_PASSWORD`、`MYSQL_READER_PASSWORD` 和 `MYSQL_WRITER_PASSWORD` 注入。

脚本可以重复运行。重复运行会确认账号密码并重新应用权限，不会删除数据库或表。

`config.yaml` 已被 `.gitignore` 忽略；也可以只保留非敏感配置，将密码放入环境变量或 `.secrets.yaml`。

## 排查

```bash
# 检查 root、数据库、三个应用账号连接、权限和表
scripts/mysql_env.sh check

# 只查看三个应用账号的权限
scripts/mysql_env.sh grants

# 查看 data_agent 当前的表
scripts/mysql_env.sh tables
```

`check` 也可以写成 `status`。非交互运行示例：

```bash
MYSQL_MIGRATION_PASSWORD='...' \
MYSQL_READER_PASSWORD='...' \
MYSQL_WRITER_PASSWORD='...' \
scripts/mysql_env.sh check
```

如果 MySQL 不在默认地址，可以覆盖连接参数：

```bash
MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 scripts/mysql_env.sh check
```

## 权限边界

| 账号 | 权限 |
| --- | --- |
| `agent_migration` | `data_agent.*` 全部权限，仅用于迁移、采集和索引发布 |
| `agent_control` | 运行状态表读写、权限表和语义目录只读 |
| `agent_reader` | 仅八张分析业务表的 `SELECT`, `SHOW VIEW` |
| `agent_writer` | 仅 `products.product_name` 的受控 `UPDATE`（当前 API 未启用） |

先运行 `init` 创建数据库服务账号，应用全部 Migration 后必须运行 `harden` 写入表级授权。脚本不创建应用层登录用户；应用身份保存在 `app_users`，通过 `scripts/set_user_password.py` 设置密码。

## 注意事项

生产环境应使用部署平台的密钥管理能力；不要把密码写入仓库或命令行参数。

如果应用运行在 Docker 容器中，需要把 `MYSQL_ACCOUNT_HOST` 改成应用容器的网络来源，或者为对应 host 单独创建账号；不要在生产环境直接使用 `'%'` 放开来源。

## Schema 目录与 Milvus 索引

先使用 migration 账号应用 `migrations/005_schema_rag.sql`，再执行：

```bash
# 只采集白名单 MySQL information_schema 并重建 BM25 词项
python3.12 scripts/index_catalog.py --collect-only

# 从当前权威 catalog_version 构建四层 Milvus 索引
python3.12 scripts/index_catalog.py --index-only
```

不带参数时两步连续执行。脚本不会创建业务表、写入事实数据或
原地删除 active collections。输出只包含版本、模型、维度和文档计数。

## Schema RAG 评测

索引激活后，可通过真实 MySQL、Milvus、Embedding 和 Reranker 运行 70 条
Schema Linking 固定案例：

```bash
python3.12 scripts/evaluate_schema_rag.py

# Reranker / dense-BM25 权重消融
python3.12 scripts/evaluate_schema_rag.py --disable-reranker
python3.12 scripts/evaluate_schema_rag.py --dense-weight 0.4
```

默认报告写入 `reports/schema-rag-evaluation.json`，包含 Object/Field Recall@K、
Context Precision、P95 token、P95 latency、敏感字段泄漏数以及完整版本信息。
