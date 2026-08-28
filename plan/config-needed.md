# 配置清单（请你填写后发我）

拿到这些值后，我会写成仓库根目录的 `config.yaml`（密钥不入库，只保留 `config.example.yaml`）。

填写方式任选其一：

1. 直接在本文件「填写区」补全后发我；
2. 按同样字段在聊天里贴一份 YAML。

不知道的项标 `待定`。有默认值的项可以不填，我会按默认写。

---

## 必须提供

这些没有就无法连上业务库和模型。

### 1. MySQL 业务库

MVP 要求：**同一 MySQL 实例、InnoDB**。查询账号和写入账号必须分开。

| 字段 | 说明 | 示例 | 你的值 |
| --- | --- | --- | --- |
| `mysql.host` | 主机 | `127.0.0.1` | |
| `mysql.port` | 端口 | `3306` | |
| `mysql.database` | 业务库名 | `ecommerce` | |
| `mysql.charset` | 字符集 | `utf8mb4` | |
| `mysql.admin.user` | 迁移/建表账号（DDL） | `root` | |
| `mysql.admin.password` | 上述密码 | | |
| `mysql.reader.user` | **只读**账号，只能 `SELECT` | `da_reader` | |
| `mysql.reader.password` | | | |
| `mysql.writer.user` | **写入**账号，只拥有白名单表的 `SELECT/INSERT/UPDATE` | `da_writer` | |
| `mysql.writer.password` | | | |

补充：

- 这套库里 **是否已有电商业务表**？（有 / 没有，从零生成合成数据）
- 若已有表：能否提供表清单，或允许我 `INFORMATION_SCHEMA` 拉 DDL？
- MySQL 版本？（建议 8.0+）

### 2. LLM

走 OpenAI 兼容接口即可（DeepSeek / Qwen / vLLM / OneAPI 都可以）。

| 字段 | 说明 | 示例 | 你的值 |
| --- | --- | --- | --- |
| `llm.base_url` | API 根路径 | `https://api.deepseek.com/v1` | |
| `llm.api_key` | 密钥 | `sk-...` | |
| `llm.model` | 主模型（意图、查询骨架、WritePlan、最终回答） | `deepseek-chat` | |
| `llm.timeout_seconds` | 单次调用超时 | `60` | |

若意图 / 查询骨架 / 写入计划要 **不同模型**，再补：

| 字段 | 用途 | 你的值 |
| --- | --- | --- |
| `llm.models.coordinator` | 意图识别、任务结构化、最终回答 | 空=用 `llm.model` |
| `llm.models.query_skeleton` | 查询骨架 | |
| `llm.models.write_plan` | WritePlan | |
| `llm.models.retrieval` | 表级检索问题、SchemaGap | |

### 3. Embedding（Schema RAG 向量检索）

| 字段 | 说明 | 示例 | 你的值 |
| --- | --- | --- | --- |
| `embedding.base_url` | 空=与 `llm.base_url` 相同 | | |
| `embedding.api_key` | 空=与 `llm.api_key` 相同 | | |
| `embedding.model` | 向量模型 | `text-embedding-v3` / `bge-m3` | |
| `embedding.dim` | 向量维度（必须与模型一致） | `1024` | |

若暂时没有 embedding 接口：写「没有」。MVP 可先只用 BM25，向量检索作为后续增强。

---

## 建议提供（有默认）

不填则按右列默认。

| 字段 | 含义 | 默认 |
| --- | --- | --- |
| `app.host` / `app.port` | 后端监听 | `127.0.0.1:8000` |
| `app.timezone` | 租户业务时区（「今天/本月」换算） | `Asia/Shanghai` |
| `sqlite.dir` | Agent 控制面目录（多个 `.sqlite`，不要合成一张表） | `./data/sqlite` |
| `sqlite.users` | 本地用户与权限 | `./data/sqlite/users.sqlite` |
| `sqlite.catalog` | 表/字段/关系/指标/写入操作 | `./data/sqlite/catalog.sqlite` |
| `sqlite.embeddings` | Schema 向量 | `./data/sqlite/embeddings.sqlite` |
| `sqlite.checkpoint` | LangGraph Checkpoint | `./data/sqlite/checkpoint.sqlite` |
| `sqlite.runtime` | 会话、任务、HITL | `./data/sqlite/runtime.sqlite` |
| `sqlite.results` | 查询结果元数据（Parquet 不进库） | `./data/sqlite/results.sqlite` |
| `results.dir` | Parquet 结果目录 | `./data/results` |
| `results.ttl_hours` | 结果过期 | `1` |
| `results.max_rows` | 单次查询最大行数 | `100000` |
| `results.max_bytes` | 单次结果文件上限 | `256MB` |
| `query.timeout_seconds` | 只读 SQL 超时 | `30` |
| `query.max_explain_rows` | `EXPLAIN` 扫描行数上限 | `5000000` |
| `write.max_affected_rows` | 单次写入影响行上限 | `100` |
| `write.approval_ttl_minutes` | HITL 批准有效期 | `15` |
| `schema_rag.table_top_k` | 表召回 TopK | `5` |
| `schema_rag.column_top_k` | 字段召回 TopK | `10` |
| `schema_rag.max_gap_rounds` | SchemaGap 补检轮数 | `2` |
| `auth.mode` | `local_password` / `off`（仅本机调试） | `local_password` |
| `frontend.dev_port` | Vite 端口 | `5173` |

### 本机登录（若 `auth.mode=local_password`）

MVP 单机，不做 SSO。需要至少一个可登录用户，HITL 批准人用同一身份。

| 字段 | 你的值 |
| --- | --- |
| 用户名 | |
| 密码 | |
| 显示名 | |
| 角色（`analyst` / `operator`，operator 才能确认写入） | |

可提供多人。

---

## 业务约定（影响种子数据和白名单写入）

文档要求 MVP 只注册 **1～2 类** 写入。请选定：

**写入操作 A（建议必选）**

- 名称，例如：`update_sku_status`（SKU 上下架）
- 目标表 / 主键
- 允许改的字段
- 是否必须 HITL（文档默认必须）

**写入操作 B（可选）**

- 名称，例如：`adjust_sku_inventory`（按 SKU 调库存）
- 同上

**评测数据集（已锁定，不要再扩表）**

业务库已是开发切片：12 张业务表 + 回执/审计，8 个域都有。实现计划 **不再** 扩到 48 张表。文档里的 48/520/62 是设计估算，评测 runner 按本切片出真实数字。

- [x] 使用当前 `data-agent-ecommerce` 切片（`dev_slice`）
- [ ] ~~扩到 48 张表~~ 已否决
- [ ] 使用我现有另一套业务库（未选）

**10 个指标是否按文档原样？**

文档固定：GMV、实付 GMV、净 GMV、订单量、客单价、退款率、转化率、新客数、复购率、广告 ROI。

- [ ] 按文档这 10 个
- [ ] 我另有口径（请附公式）

---

## 填写区（可直接改这里发我）

```yaml
mysql:
  host:
  port: 3306
  database:
  charset: utf8mb4
  admin:
    user:
    password:
  reader:
    user:
    password:
  writer:
    user:
    password:
  version: "8.0"
  has_existing_tables: false   # true / false

llm:
  base_url:
  api_key:
  model:
  timeout_seconds: 60
  models:
    coordinator:
    query_skeleton:
    write_plan:
    retrieval:

embedding:
  available: true              # false = MVP 只用 BM25
  base_url:
  api_key:
  model:
  dim:

app:
  host: 127.0.0.1
  port: 8000
  timezone: Asia/Shanghai

sqlite:
  dir: ./data/sqlite
  users: ./data/sqlite/users.sqlite
  catalog: ./data/sqlite/catalog.sqlite
  embeddings: ./data/sqlite/embeddings.sqlite
  checkpoint: ./data/sqlite/checkpoint.sqlite
  runtime: ./data/sqlite/runtime.sqlite
  results: ./data/sqlite/results.sqlite

auth:
  mode: local_password
  users:
    - username:
      password:
      display_name:
      role: analyst            # analyst | operator

write_ops:
  - type: update_sku_status
  - type: adjust_sku_inventory

dataset:
  mode: dev_slice              # dev_slice | full_48 | existing_db
  metrics: document_10         # document_10 | custom
```
