# 通用开发注意事项与边界

> **强制：** 实现任何一个 Task 之前先读完本文件。Task 正文、旧举例、聊天里的临时说法与本文件冲突时，以本文件为准。
>
> 配套索引：[implementation-plan.md](implementation-plan.md) · 单 Task：[tasks/](tasks/)

本文件只约束「怎么开发、什么不能做、模块之间怎么交界」。具体步骤在对应 Task 文件里。

---

## 0. 怎么用这些 plan 文件

1. 读本文件（不变量、存储边界、契约、禁止项）。
2. 打开当前 Task 的 `plan/tasks/NN-*.md`，只做该文件 **Owns** / **In** 列出的事。**Out** 和 **Must not** 与本文件同等效力，不是建议。
3. 按 Task 里的 Step：先写失败测试 → 跑红 → 最小实现 → 跑绿。
4. 不要提前实现后续 Task 的文件，即使「顺手」。
5. 需要改共享契约时：先改本文件的「核心契约」，再改 `backend/app/types.py`，再改依赖它的 Task。禁止在某个 Task 里私自发明同名不同类型。

每个 Task 文件顶部的 Boundary 表字段含义：

| 字段 | 含义 |
| --- | --- |
| **Owns** | 本 Task 完成后必须成立的职责，也是验收口径 |
| **In** | 允许创建或修改的文件 |
| **Out** | 明确属于别的 Task，本 Task 碰了就是越界 |
| **Must not** | 即使「能跑」也不许做的行为 |

实现者可以是另一段会话、另一个 agent。不要假设对方读过上一份聊天记录。

---

## 1. 产品目标与架构（不得扩大）

**Goal:** 面向电商经营分析的可信数据 Agent：单 Coordinator + 可信查询 Skill + 受控写入 Skill。能安全问数、受限多轮、小范围写入，并用固定评测集验证。

**Architecture:** 用户请求进入 FastAPI → Coordinator（LangGraph）做意图识别、任务结构化、**唯一** HITL 与路由。

- 查询 Skill：Schema Agentic RAG → 查询骨架 → MetricCompiler → SQL 安全网关 → 只读 MySQL → Parquet。只返回 `QuerySkillResult`，**从不** `interrupt()`。
- 写入 Skill：WritePlan → 白名单模板 → 预览，返回 `WriteSkillResult`。Coordinator 再 `interrupt()`；恢复后才调用 `execute_write`。
- 共享组件：Schema Catalog、SQL 网关、MySQL 执行 Tool。
- 三套存储：MySQL = 业务事实源；SQLite Checkpoint = 会话真相；Parquet = 临时结果面。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、LangGraph + SqliteSaver、SQLGlot（MySQL 方言）、PyMySQL/SQLAlchemy、DuckDB、PyArrow、rank-bm25、OpenAI 兼容 LLM/Embedding、React 18 + TypeScript + Vite、MySQL 8 InnoDB、pytest。

不要换成另一套栈（不要 LangChain AgentExecutor、不要 MySQL Checkpointer、不要 Redis/MinIO）。

---

## 2. Locked Decisions（8 条不变量，不得再改）

1. **唯一 `interrupt()` 在 Coordinator。** 查询 Skill、写入 Skill 不得调用 `interrupt()`，只返回 `QuerySkillResult` / `WriteSkillResult`（可带 `hitl` 或 `preview`）。Coordinator 的 HITL 节点函数体只有 `interrupt()`，节点内不生成 `operation_id`、不写库。`/api/threads/{id}/resume` 只恢复 Coordinator 图。
2. **filter vs requery 只在查询 Skill 判断。** Coordinator 意图只有 `QUERY | WRITE | FOLLOWUP | CLARIFY | UNSUPPORTED`。`FOLLOWUP` 只表示「继续上一轮查询」。`followup.py` 看上一轮结果列集合：仅筛选/排序/选列/TopN → DuckDB；新指标/维度/时间/缺字段 → 合并上一轮 `QueryTask` 再进 Q01。Coordinator 不得再分 `FOLLOWUP_FILTER` / `FOLLOWUP_REQUERY`。
3. **只读 SQL 必须参数化；网关拦截敏感列。** `check_read_sql` 吃 `CompiledQuery`，拒绝字符串插值 SQL。Catalog 中 `is_sensitive=1` 的列出现在 SELECT/WHERE/JOIN 即为 `unsafe`。评测安全套件用夹具把某列标敏感。
4. **多指标按 `grain_table` 先聚合再拼接；多 JOIN 路径 → HITL。** 同一 `QueryTask.metric_ids` 是一等能力。编译器按 grain 各生成 CTE，再按维度对齐，禁止在聚合前把订单 grain 指标 JOIN 到 `fact_order_item`。关系图两点之间若有 ≥2 条审核路径且都会改变 grain/过滤，返回 `AMBIGUOUS`，不偷偷选最短路。仅当路径唯一时才自动补全。
5. **Q10 最多修 2 次，且仅修可修的 `unsafe`。** 可修：多选了 JOIN/列、误用了未在骨架声明的列。不可修、直接拒绝或交 Coordinator HITL：`too_broad`、未审核 JOIN、fan-out 重复统计、敏感列、注入/多语句。LLM 不能判安全；网关 `ok=false` 才能触发重生成。
6. **SqliteSaver（`checkpoint.sqlite`）是会话真相。** `runtime.sqlite` 只投影 `thread(thread_id, user_id, title, timestamps)` 供会话列表。禁止把任务正文、HITL payload 写入 `task` / `hitl_interrupt`（DDL 可留着，代码不写）。结果元数据只在 `results.sqlite`。
7. **评测绑定当前 12 张业务表，T16 只跑 runner，不再扩表。** 表清单、15 条审核关系、10 个指标公式以 `scripts/init_sqlite.py` 与 `migrations/mysql/001_ecommerce_slice.sql` 为准。文档里的 48 表 / 520 字段 / 62 关系是设计估算；报告打印本切片上的真实召回与准确率，禁止宣称 48 张表。T16 可以加行数（`full` 档），不能加新业务表。
8. **下列规则各只有一种合法解释：**
   - `tenant_id` 恒为 `"default"`。字段保留并做相等校验，不做真多租户。
   - `schema_version` ≡ `catalog_version`（结果行只是拷贝，不另发明第三套版本）。`permission_version` 在 `users.sqlite`，独立递增。
   - Catalog 权威源：物理表/列 ← `sync_from_mysql`（INFORMATION_SCHEMA + 注释）；指标与写入操作 ← `scripts/init_sqlite.py` 的 `METRICS`/`WRITE_OPS`；关系 ← INFORMATION_SCHEMA 外键，`source=fk`、`reviewed=1`、引用表→被引用表 = `many_to_one`。禁止 LLM 或 Embedding 添加边。
   - `data_as_of` = `min(request_time_utc, max(本次用到的各 grain 表 time_field 的 MAX()))`。空表则等于时间窗 `start`。回答不得把「今天」说成完整，若 `data_as_of` 早于本日结束。
   - `request_hash` = `SHA256(canonical_json({operation_type, object_ids_sorted, params, filters, version_snapshots}))`。预检得到的每行 `row_version` 必须进哈希。
   - W07 发现快照/目标/参数与预览不一致 → **不执行**，返回 `VERSION_CONFLICT` + 新预览；Coordinator 发新 `operation_id` 再 `interrupt()`。旧 ID 尚未插入 MySQL，直接丢弃。W08 事务内版本冲突 → `ROLLBACK`（回执未提交），同样换新 ID 重新预览。预检 80 行、确认时变成 >100 行 → `WRITE_SCOPE_TOO_LARGE`，不提交。
   - 撤销/补偿：不注册补偿操作。用户要求回滚已提交写入 → `UNSUPPORTED`。
   - 一句里又查又写 → `UNSUPPORTED`，要求用户先问数再写。
   - 用户用「这些 SKU」指上一轮结果：Coordinator 只从上一轮 READY 结果中名为 `sku_id` 或 `id` 且属于 `dim_sku` 的列取值填入 `WriteTask.object_ids`；列不存在或结果过期 → HITL / `RESULT_EXPIRED`。
   - `respond` 只把聚合标量、单位、时间窗、`data_as_of`、列名、`result_id`、指标版本送给 LLM。`preview_rows` 仅 API/前端使用，不进 Prompt。
   - CSV 上限 = `min(row_count, results.max_rows)`，且必须 READY + 所有者 + 实时权限 + TTL。不是无限明细导出。
   - `comparisons` 只允许 `yoy | mom | ratio | topn`。贡献度 → `UNSUPPORTED_ANALYSIS`。
   - 写入批准人必须是同一 `operator` 用户；`analyst` 的 `allowed_write_ops` 为空，预检即拒绝。

---

## 3. 全局约束

- 单机单实例；SQLite 不做多写节点。MySQL 只放电商业务表和写入回执/审计；Agent 控制面按职责拆成多个 SQLite 文件，禁止把用户、Schema、向量、Checkpoint、结果元数据混进一张表或塞进 MySQL。
- LLM 只引用 `metric_id`，禁止模型自创指标公式；公式由 `MetricCompiler` 写入 SQL。
- JOIN 只能使用 Schema Catalog 中审核过的关系；缺失关系时 HITL 或拒绝，不能让 LLM 猜。
- 查询与写入使用不同 MySQL 账号；写入账号仅拥有注册操作所需权限。`da_reader` 对业务库是全表 SELECT，**列级/表级权限只在应用网关执行**，不要假设 MySQL 账号会挡列。
- 只读 SQL：单条 `SELECT`，禁止 `SELECT *`、锁定读、文件操作、危险函数、多语句。编译与执行必须使用 **命名参数**（`CompiledQuery.sql` + `params`），禁止把 `FilterCond.value` 拼进 SQL 字符串。
- 写入：仅 `update_sku_status` 与 `adjust_sku_inventory`，单次最多 100 行；超过上限拒绝，不自动拆批。`must_hitl` 恒为 true，配置不得关掉。
- SchemaGap 全局补检最多 2 轮；连续两轮无新增对象则停止。
- 结果文件先写 `<result_id>.part`，成功后原子重命名为 Parquet，状态 `WRITING → READY → EXPIRED → DELETED`。
- `operation_id` 只防止同一次操作因重试/恢复而重复生效；两个独立请求即使内容相同也是不同 ID。
- 写入是否提交只查 MySQL 主库回执；SQLite Checkpoint 不能当提交证据。
- 回执、业务变更、审计必须在同一 MySQL 实例的同一 InnoDB 事务中提交。
- 权限每次请求重新加载，不能从 Checkpoint 恢复。`RuntimeContext` 不含数据库连接。
- 「今天/本月」由服务端 `request_time_utc` + 时区规则换算。HITL **恢复**沿用原时间范围；**新用户消息**若改写了时间则重算，若未提时间则沿用上一轮 `QueryTask.time_range`。
- 只实现当前分支 `docs/` 下的 Coordinator+Skills 文档。禁止混用 `system-upgrade` 上的 `docs/specs`（ContextFrame / MySQL Checkpointer / 长期记忆）。
- 密钥只存在 `config.yaml`（gitignore）；仓库只提交 `config.example.yaml`。
- MySQL 切片、SQLite 控制面、`config.yaml` 已落地。后续任务 **不要** 再创建根目录 `migrations/001_*.sql` 或用 SQLite 冒充 MySQL。无 MySQL 时集成测试 skip，单测用 SQLGlot + 假 LLM。

---

## 4. 三套存储：谁写什么

| 存储 | 路径 / 库 | 允许写入的内容 | 禁止写入 |
| --- | --- | --- | --- |
| MySQL | `data-agent-ecommerce` | 12 张业务表 + `da_write_receipt` + `da_write_audit` | Agent 会话、权限、Catalog、向量、结果明细 |
| SQLite `users` | `data/sqlite/users.sqlite` | 本地用户、角色、`permission_version` | 会话正文、HITL、结果 |
| SQLite `catalog` | `data/sqlite/catalog.sqlite` | 表/列/关系/指标/写入操作定义 | 运行时任务、查询结果 |
| SQLite `embeddings` | `data/sqlite/embeddings.sqlite` | Schema 向量索引 | 其它任何运行时状态 |
| SQLite `checkpoint` | `data/sqlite/checkpoint.sqlite` | **仅** LangGraph `SqliteSaver` | 业务行、结果明细、权限快照当真相 |
| SQLite `runtime` | `data/sqlite/runtime.sqlite` | **仅** `thread(thread_id, user_id, title, timestamps)` 投影 | `task` / `hitl_interrupt` 正文（DDL 可留，代码不写） |
| SQLite `results` | `data/sqlite/results.sqlite` | 结果元数据（id、路径、状态、TTL、所有者） | Parquet 行、业务表拷贝 |
| Parquet | `data/results/` | 本次查询的临时结果文件 | 永久数仓、跨会话长期记忆 |

Checkpoint 里只放：当前/上一轮 `QueryTask` 或 `WriteTask`、HITL payload、`result_id`、`operation_id`、`request_hash`。原始查询结果、MySQL 连接、权限对象 **不进** Checkpoint。权限每次 `reload_permissions()`。

---

## 5. 模块所有权（跨 Task 不要越界）

| 模块 | 唯一允许的职责 | 明确不是它的事 |
| --- | --- | --- |
| Coordinator | 意图、结构化任务、**唯一** `interrupt()`、路由 Skill、最终 `respond`、候选项 | 不写 SQL、不判断 DuckDB vs MySQL、不编译指标、不执行事务 |
| 查询 Skill | Q01–Q11、`followup.py` 的 filter/requery、返回 `QuerySkillResult` | 不 `interrupt()`、不自创公式、不改 Catalog 边 |
| 写入 Skill | `prepare_write` / `execute_write`，返回 `WriteSkillResult` | 不 `interrupt()`、预览阶段不 INSERT 回执、不注册补偿操作 |
| Schema RAG | 召回表/列 + 唯一路径补全 JOIN | 不添加边、不选多路径中的最短路、不 interrupt |
| MetricCompiler | 审核公式 → 参数化 `CompiledQuery` | 不跑网关、不执行 SQL、不接受骨架里的「公式字符串」 |
| 只读网关 | 判 `ok / unsafe / too_broad` | 不修 SQL、不让 LLM 判安全、不执行 |
| 写入网关 | 模板 AST 同构检查 | 不生成 HITL、不提交事务 |
| Result Store | Parquet 生命周期 + DuckDB 已有列筛选 | 不回查 MySQL、不让 LLM 写 DuckDB SQL |
| HTTP API | 鉴权、SSE、分页、CSV、resume Coordinator | 不按 Skill 分子 resume、不按页打业务库 |
| 前端 | 对话、表格、简单图、HITL 确认 | 不做 Trace/推理抽屉、不把 Prompt 展示给用户 |
| 评测 | 在 **当前 12 表** 上跑 runner | 不加业务表、不比 SQL 字符串、不手填 48 表数字 |

Skill 图可以存在，但 **没有** `interrupt()` 节点。

---

## 6. 切片锁定（禁止扩表）

**12 张业务表 + 2 张回执/审计，禁止再加业务表：**
`dim_store`, `dim_user`, `dim_category`, `dim_sku`, `dim_channel`, `dim_campaign`, `fact_order`, `fact_order_item`, `fact_payment`, `fact_refund`, `fact_traffic`, `fact_ad_spend`，以及 `da_write_receipt`, `da_write_audit`。

**15 条审核关系**（全部 `many_to_one`、`source=fk`、`reviewed=1`），来自 `scripts/init_sqlite.py` 的 `RELATIONS`：
`fact_order`→`dim_user/dim_store/dim_channel/dim_campaign`，`fact_order_item`→`fact_order/dim_sku`，`dim_sku`→`dim_category`，`fact_payment`→`fact_order`，`fact_refund`→`fact_order/fact_order_item`，`dim_campaign`→`dim_channel`，`fact_ad_spend`→`dim_campaign/dim_channel`，`fact_traffic`→`dim_store/dim_channel`。

**10 个指标**以 `scripts/init_sqlite.py` 的 `METRICS` 为唯一口径，不要另写一份互相漂移的 yaml（除非从该文件生成）：

| metric_id | grain_table |
| --- | --- |
| `gmv` | `fact_order_item` |
| `paid_gmv` | `fact_order_item` |
| `net_gmv` | `fact_order_item` |
| `order_count` | `fact_order` |
| `aov` | `fact_order` |
| `refund_rate` | `fact_refund` |
| `cvr` | `fact_order` |
| `new_customers` | `fact_order` |
| `repurchase_rate` | `fact_order` |
| `ad_roi` | `fact_order_item` |

同比/环比 **不是** 独立 metric，由 `QuerySkeleton.comparisons` 触发。贡献度不做。

**写入操作仅两类：** `update_sku_status`、`adjust_sku_inventory`。

---

## 7. MVP 明确不做

Multi-Agent、Python/Pandas 代码执行、预测/聚类、跨数据源、MinIO/Redis、分库分表、长期用户记忆、已提交操作一键回滚、补偿写入操作、图表报告 Skill、贡献度分析、前端 Trace/推理抽屉、真多租户、48 张表扩展、`FOLLOWUP_FILTER`/`FOLLOWUP_REQUERY` 意图、Skill 内 `interrupt()`、用 SQLite 冒充 MySQL `EXPLAIN`/`FOR UPDATE`。

也不要去实现 `system-upgrade` 分支上的 ContextFrame / MySQL Checkpointer。

---

## 8. 编码与测试约定

- **先测试后实现。** Task 的 Step 1 是失败测试。没有对应测试的行为，当它不存在。
- **最小实现。** 不预留「以后多租户 / 多 Skill / 多数据源」的抽象。
- **假 LLM。** 单测一律注入假客户端；不要在 CI 单测里打真实 API。
- **真 MySQL 可 skip。** 集成测试标 `@pytest.mark.integration`；连不上就 skip，不要改用 SQLite 方言。
- **不要改文件地图以外的路径。** 需要新文件时，先改本文件的「文件地图」和对应 Task 的 Files 节，再创建。
- **不要改无关代码。** 每个 diff 行都要能追溯到当前 Task。
- **密钥。** 不把 `config.yaml`、真实 key 写入测试或 README。测试读 `config.example.yaml` 或临时文件。
- **确定性。** `MetricCompiler` 对同一输入两次输出必须字节级相同。
- **数字来源。** 回答里的数字必须能在 `ResultSummary` 中找到，找不到就拒答，不编造。候选项必须带稳定 ID 且来自实时授权查询，查不到只说未查到。

---

## 9. 核心契约（名称与字段不得改）

实现放在 `backend/app/types.py`。下面是全仓库共用的形状；Task 2 负责落地，其它 Task 只引用。

```python
# backend/app/types.py

from __future__ import annotations
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class Intent(str, Enum):
    QUERY = "query"
    WRITE = "write"
    FOLLOWUP = "followup"     # 仅表示继续上一轮查询；filter vs requery 由查询 Skill 决定
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"


class TimeRange(BaseModel):
    start: str          # ISO8601, inclusive
    end: str            # ISO8601, exclusive  [start, end)
    grain: Literal["day", "week", "month"] = "day"
    label: str          # "2026-08" / "今天" 解析后的展示名
    source: Literal["user", "server_default"] = "server_default"


class PermissionSet(BaseModel):
    tenant_id: str
    user_id: str
    role: Literal["analyst", "operator"]
    allowed_tables: list[str]
    allowed_columns: list[str]          # "db.table.column"
    allowed_metrics: list[str]
    allowed_write_ops: list[str]
    catalog_version: int
    permission_version: int


class RuntimeContext(BaseModel):
    tenant_id: str
    user_id: str
    role: Literal["analyst", "operator"]
    request_time_utc: str
    timezone: str
    permissions: PermissionSet
    thread_id: str


class FilterCond(BaseModel):
    field: str
    op: Literal["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "like"]
    value: Any


class LocalFilterSpec(BaseModel):
    filters: list[FilterCond] = []
    order_by: list[str] = []
    select: list[str] = []
    topn: int | None = None


class QueryTask(BaseModel):
    task_id: str
    metric_ids: list[str]
    dimensions: list[str]
    filters: list[FilterCond]
    time_range: TimeRange
    order_by: list[str] = []
    limit: int | None = None
    parent_result_id: str | None = None
    catalog_version: int
    permission_version: int


class WriteTask(BaseModel):
    task_id: str
    operation_type: str
    object_ids: list[str]
    params: dict[str, Any]
    filters: list[FilterCond] = []
    permission_version: int


class ResultSummary(BaseModel):
    result_id: str
    row_count: int
    columns: list[str]
    preview_rows: list[dict[str, Any]]  # API/前端 ≤20 行；禁止送进 respond Prompt
    units: dict[str, str] = {}
    time_range: TimeRange
    data_as_of: str
    metric_versions: dict[str, int] = {}
    schema_version: int                 # 必须等于当时的 catalog_version
    parent_result_id: str | None = None


class CompiledQuery(BaseModel):
    sql: str                            # 仅命名参数，例如 WHERE status IN :statuses
    params: dict[str, Any]


class SkillErrorCode(str, Enum):
    SCHEMA_GAP = "SCHEMA_GAP"
    AMBIGUOUS = "AMBIGUOUS"
    UNSAFE_SQL = "UNSAFE_SQL"
    TOO_BROAD = "TOO_BROAD"
    RESULT_EXPIRED = "RESULT_EXPIRED"
    PERMISSION_CHANGED = "PERMISSION_CHANGED"
    WRITE_SCOPE_TOO_LARGE = "WRITE_SCOPE_TOO_LARGE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    UNKNOWN_COMMIT = "UNKNOWN"
    UNSUPPORTED_ANALYSIS = "UNSUPPORTED_ANALYSIS"
    REJECTED = "REJECTED"


class QuerySkillResult(BaseModel):
    ok: bool
    result: ResultSummary | None = None
    error_code: SkillErrorCode | None = None
    error_message: str | None = None
    hitl: dict[str, Any] | None = None


class WriteSkillResult(BaseModel):
    ok: bool
    operation_id: str | None = None
    status: Literal["preview", "committed", "rejected", "unknown"] | None = None
    affected_rows: int | None = None
    audit_id: str | None = None
    preview: dict[str, Any] | None = None
    error_code: SkillErrorCode | None = None
    error_message: str | None = None


class QuerySkeleton(BaseModel):
    metric_ids: list[str]         # 同一任务可多指标；编译器按 grain_table 拆 CTE
    select_dims: list[str]
    from_table: str
    joins: list[dict[str, str]]   # {left, right, on_left, on_right, cardinality}
    filters: list[FilterCond]
    time_field: str
    group_by: list[str]
    comparisons: list[Literal["yoy", "mom", "ratio", "topn"]] = []
    limit: int | None = None


class WritePlan(BaseModel):
    operation_type: str
    object_ids: list[str]
    params: dict[str, Any]
    filters: list[FilterCond] = []


class SchemaGap(BaseModel):
    missing_concept: str
    purpose: str
    constraints: list[str] = []
    excluded: list[str] = []
```

后续 Task 若需要新类型：加到 `types.py` **并且** 同步改本文件这一节。不要在 Skill 内部另造一套平行模型。

---

## 10. 文件地图

后续任务只改这里列出的路径。需要新路径时先改本节。

```text
.
├── pyproject.toml
├── config.example.yaml
├── config.yaml                          # gitignore
├── README.md
├── backend/app/
│   ├── main.py                          # FastAPI 入口
│   ├── config.py                        # 加载 YAML → Settings
│   ├── logging.py
│   ├── types.py                         # 跨模块契约
│   ├── api/auth.py, chat.py, results.py, interrupts.py
│   ├── llm/client.py, schemas.py
│   ├── prompt/*.yaml
│   ├── runtime/context.py, time.py, permissions.py
│   ├── catalog/models.py, store.py, sync.py, metrics.py
│   ├── retrieval/bm25.py, vector.py, schema_rag.py
│   ├── compiler/metric_compiler.py
│   ├── gateway/ast.py, read_policy.py, write_policy.py, explain.py
│   ├── mysql/pool.py, execute_read.py, execute_write.py
│   ├── results/store.py, parquet.py, duckdb_filter.py
│   ├── coordinator/graph.py, intent.py, hitl.py, respond.py, candidates.py
│   ├── skills/query/graph.py, coverage.py, followup.py
│   ├── skills/write/graph.py, registry.py, preview.py
│   └── eval/runner.py
├── frontend/                            # Vite React 工作台
├── migrations/mysql/                    # 已存在：001 切片 DDL / 002 seed / 003 grants
├── migrations/sqlite/                   # 已存在：六个控制面库的 DDL
├── scripts/init_sqlite.py, apply_mysql_slice.sh
├── scripts/check_connectivity.py        # MySQL / SQLite / LLM / embedding ping
├── seeds/generate_ecommerce.py          # 已存在；不要再写 generate_data.py 第二份生成器
├── tests/
└── data/                                # gitignore
    ├── sqlite/
    │   ├── users.sqlite                 # 本地用户与权限
    │   ├── catalog.sqlite               # 表/字段/关系/指标/写入操作
    │   ├── embeddings.sqlite            # Schema 向量
    │   ├── checkpoint.sqlite            # LangGraph SqliteSaver（会话真相）
    │   ├── runtime.sqlite               # 仅 thread 列表投影，不写 HITL/任务正文
    │   └── results.sqlite               # 结果元数据（不是业务行）
    └── results/                         # Parquet 临时结果
```

---

## 11. 开发顺序与依赖

```text
M0  工程骨架 + config 加载          → pytest 能跑          T1
M1  Catalog / 指标 / 开发切片数据    → 能连 MySQL 并查到 GMV 底表  T2–T4
M2  SQL 网关 + 只读执行 + Result Store → 手写合法/非法 SQL 可拦截可落盘  T5–T7
M3  Schema RAG + MetricCompiler + 查询 Skill → 一问 GMV 出 result_id  T8–T10
M4  写入注册表 + HITL + 事务回执     → 预览→确认→提交，断线可恢复  T11–T13
M5  Coordinator + API + 前端         → 浏览器可对话、分页、CSV、确认写入  T13–T15
M6  评测集自动出报告                 → 对照 docs/data-agent评测.md 的口径  T16
```

```text
T1 骨架
 └─ T2 类型/时间/权限
     ├─ T3 MySQL 池
     │    └─ T4 Catalog/指标
     │         ├─ T5 只读网关
     │         │    └─ T7 只读执行 ← T6 Result Store
     │         ├─ T8 Schema RAG
     │         └─ T9 MetricCompiler
     │               └─ T10 查询 Skill ← T5 T7 T8 T9 T6
     ├─ T11 写入网关/事务 ← T3 T4
     │    └─ T12 写入 Skill
     └─ T13 Coordinator ← T10 T12 T2 candidates
          └─ T14 API ← T13 T6
               └─ T15 前端
T16 评测 可在 T10 后并行写用例（绑定 12 表切片），T13/T12 完成后跑全量
```

MySQL 与 `config.yaml` 已可用：M0–M2 单测不连库；集成测试连真 MySQL，连不上则 skip。禁止再用 SQLite 方言冒充 MySQL `EXPLAIN` / `FOR UPDATE`。LLM 单测一律假客户端。

---

## 12. 规格覆盖

| 文档要求 | 任务 |
| --- | --- |
| Coordinator 意图/路由/多轮/HITL | T13 |
| 可信查询 Skill 全流程 Q01–Q11 | T10 |
| Schema Agentic RAG、Gap≤2 | T8 |
| MetricCompiler、禁止 LLM 公式 | T9 |
| SQLGlot 网关、重复统计、EXPLAIN | T5 |
| 只读账号、流式 Parquet、TTL | T3 T6 T7 |
| 受限多轮 DuckDB | T6 T10 |
| 服务端时间、「今天」 | T2 |
| 候选项驱动 HITL，不编造 | T13 `candidates.py` |
| 受控写入 1～2 类、≤100 行 | T11 T12 |
| prepare / Coordinator interrupt / execute 拆分 | T12 T13 |
| 回执+审计同事务、断线三态 | T11 |
| 单机 SQLite Checkpoint 为会话真相 | T13 T14 |
| 前端分页/CSV/确认，无 Trace | T14 T15 |
| 评测口径与自动报告（本切片，不扩 48 表） | T16 |
| 不做 Multi-Agent/代码沙箱/一键回滚/补偿写入 | 本文件 §7 |
| 参数化只读 SQL、敏感列 | T5 T7 T9 |
| 多指标按 grain 先聚合 | T9 |
| 唯一 interrupt 在 Coordinator | T12 T13 |

---

## 13. 已经完成、不要再做一遍

1. 根目录 `config.yaml`（gitignore）与 `config.example.yaml`。
2. MySQL `data-agent-ecommerce` 切片 DDL + seed + `da_reader`/`da_writer` 授权。
3. `scripts/init_sqlite.py` 初始化的六个 SQLite 控制面库与 10 个指标、15 条关系。

剩下只需：`sync_from_mysql` 建向量索引（T8）、用真实 LLM 跑通一条 GMV 查询 + 一条写入 HITL（T13 之后）。
