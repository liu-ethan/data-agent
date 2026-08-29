# data-agent

电商问数：单 Coordinator + 查询 Skill + 写入 Skill。先读边界，再最小改动。

## 文件

| 路径 | 作用 |
| --- | --- |
| `docs/` | 产品规格。只用当前分支，禁止混用 `system-upgrade` 的 `docs/specs`。 |
| `docs/development-notes.md` | 不变量、存储边界、契约。 |
| `backend/app/types.py` | 跨模块契约。改契约先改文档再改代码。 |
| `prompts/` | 全部 LLM Prompt（身份 / 任务 / 约束 / 输出 / few-shots）。 |
| `sql/` | MySQL/SQLite DDL 与命名查询。禁止把静态 SQL 再写进 Python。 |
| `seeds/` | 切片表、指标、写入模板、产品文案。唯一业务常量源。 |
| `backend/app/resources/` | Prompt / SQL / 域常量加载器。 |

## 实现

Python：`/home/user/miniconda3/envs/python3.12`（`bin/python` / `bin/pip`）。不要用系统 python。

连通性：`/home/user/miniconda3/envs/python3.12/bin/python scripts/check_connectivity.py`（也可单独跑 `check_mysql.py` / `check_sqlite.py` / `check_llm.py` / `check_embedding.py`）。不写业务行、不往 embeddings.sqlite 落向量。

## 存储（写错库 = 错）

MySQL 只放电商事实。Agent 控制面全部在 SQLite（拆成 6 个文件）。查询结果行只进 Parquet。三类禁止互写。

| 放哪 | 只允许 | 禁止 |
| --- | --- | --- |
| **MySQL** `data-agent-ecommerce` | 12 张业务表；写入回执 `da_write_receipt`；审计 `da_write_audit` | 用户、权限、Catalog、向量、会话、HITL、结果元数据/明细 |
| **SQLite** `users.sqlite` | 本地用户、角色、`permission_version` | 业务行、会话、结果 |
| **SQLite** `catalog.sqlite` | 表/列/关系/指标/写入操作**定义** | 业务行、运行时任务、查询结果 |
| **SQLite** `embeddings.sqlite` | Schema 向量 | 其它任何状态 |
| **SQLite** `checkpoint.sqlite` | 仅 LangGraph SqliteSaver（会话真相） | 业务行、结果明细、权限当真相 |
| **SQLite** `runtime.sqlite` | 仅 `thread` 列表投影 | `task` / `hitl_interrupt` 正文（DDL 可留，代码不写） |
| **SQLite** `results.sqlite` | 结果元数据（id、路径、状态、TTL、所有者） | Parquet 行、业务表拷贝 |
| **Parquet** `data/results/` | 本次查询临时结果 | 业务源、长期记忆 |

Checkpoint 只放：当前/上一轮 Task、HITL payload、`result_id`、`operation_id`、`request_hash`。原始结果、MySQL 连接、权限对象不进 Checkpoint。写入是否提交只查 MySQL 回执。

路径：`data/sqlite/*.sqlite`。不要把六个库合成一个，也不要把控制面表建进 MySQL。

## 硬规则

- `interrupt()` 只许出现在 Coordinator。Skill 只返回结果。
- 只读 SQL 必须参数化（`CompiledQuery`）。LLM 不写指标公式、不加 JOIN 边。
- 切片锁死：12 张业务表、15 条关系、10 个指标。不扩表，不宣称 48 表。
- 写入仅 `update_sku_status` / `adjust_sku_inventory`，≤100 行，必须 HITL。
- `tenant_id` 恒 `"default"`。权限每次重新加载，不进 Checkpoint。
- `config.yaml`、MySQL 切片、SQLite 控制面已落地，不要重做。
- 单测用假 LLM。无 MySQL 则 skip 集成测。禁止用 SQLite 冒充 MySQL。

## 不做

Multi-Agent、代码沙箱、一键回滚、贡献度、Trace 抽屉、真多租户、Redis/MinIO。
