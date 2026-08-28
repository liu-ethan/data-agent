# Task 4: Catalog / 指标 / 审核关系（绑定已有切片）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T2 T3 · 交给：T5 T8 T9 T11 T16 · 里程碑：M1

不要新建业务表，不要「T16 再扩到 48 张」。切片已经覆盖 8 个域。表/关系/指标清单以 [development-notes.md](../development-notes.md) §6 为准。

## Boundary

| | |
| --- | --- |
| **Owns** | SQLite Catalog 的读写、从 MySQL `INFORMATION_SCHEMA` 同步物理表/列/外键、加载 10 个审核指标与写入操作定义。 |
| **In** | `catalog/models.py`、`store.py`、`metrics.py`、`sync.py`、`tests/test_catalog.py`、`tests/test_metrics.py`。复用已有 DDL / `init_sqlite.py` / `generate_ecommerce.py`。 |
| **Out** | 向量索引（T8）、MetricCompiler SQL（T9）、网关、Skill 图、新业务表、第二份指标 yaml（除非从 `init_sqlite.py` 生成）。 |
| **Must not** | 扩到 48 张表；让 LLM 或 Embedding 添加关系边；用 INFORMATION_SCHEMA 覆盖 `METRICS`/`WRITE_OPS`；把 Catalog 写进 MySQL；把 `schema_version` 做成独立于 `catalog_version` 的第三套版本。 |

**锁定表（12 张业务 + 2 张回执/审计，禁止再加业务表）：**
`dim_store`, `dim_user`, `dim_category`, `dim_sku`, `dim_channel`, `dim_campaign`, `fact_order`, `fact_order_item`, `fact_payment`, `fact_refund`, `fact_traffic`, `fact_ad_spend`，以及 `da_write_receipt`, `da_write_audit`。

**锁定关系（15 条，来自 `scripts/init_sqlite.py` 的 `RELATIONS`）：**
`fact_order`→`dim_user/dim_store/dim_channel/dim_campaign`，`fact_order_item`→`fact_order/dim_sku`，`dim_sku`→`dim_category`，`fact_payment`→`fact_order`，`fact_refund`→`fact_order/fact_order_item`，`dim_campaign`→`dim_channel`，`fact_ad_spend`→`dim_campaign/dim_channel`，`fact_traffic`→`dim_store/dim_channel`。全部 `many_to_one`、`source=fk`、`reviewed=1`。

**Files:**
- Reuse: `migrations/mysql/001_ecommerce_slice.sql`、`scripts/init_sqlite.py`、`seeds/generate_ecommerce.py`
- Create: `backend/app/catalog/models.py`
- Create: `backend/app/catalog/store.py`
- Create: `backend/app/catalog/metrics.py`
- Create: `backend/app/catalog/sync.py`
- Create: `tests/test_catalog.py`
- Create: `tests/test_metrics.py`

**10 个指标以 `scripts/init_sqlite.py` 的 `METRICS` 为唯一口径**：

| metric_id | grain_table | 公式要点 |
| --- | --- | --- |
| `gmv` | `fact_order_item` | `SUM(oi.price * oi.qty)`，订单状态 ∈ paid/shipped/completed |
| `paid_gmv` | `fact_order_item` | `SUM(oi.pay_amt)`，时间字段 `fact_order.paid_at` |
| `net_gmv` | `fact_order_item` | 实付 − 退款；编译时退款侧必须先按 grain 聚合再减 |
| `order_count` | `fact_order` | `COUNT(DISTINCT o.id)` |
| `aov` | `fact_order` | paid_gmv / order_count；**先分别按 grain 聚合再相除** |
| `refund_rate` | `fact_refund` | 成功退款额 / paid_gmv；同样先聚合再除 |
| `cvr` | `fact_order` | 下单用户 / `SUM(fact_traffic.visitor_cnt)`；表已存在，缺表 HITL 不再适用 |
| `new_customers` | `fact_order` | `first_order_at` 落在时间窗内的去重用户 |
| `repurchase_rate` | `fact_order` | 窗内 ≥2 单用户 / 窗内下单用户 |
| `ad_roi` | `fact_order_item` | 有 `campaign_id` 的 paid_gmv / `fact_ad_spend.amount` |

同比/环比 **不是** 独立 metric，由 `QuerySkeleton.comparisons` 触发编译器生成 CTE。对比期为 0 时返回「无法计算」，不除零。贡献度不做。

**Interfaces:**
- `CatalogStore.load() -> CatalogSnapshot`
- `get_metric(metric_id) -> MetricSpec`
- `list_reviewed_edges() -> list[TableRelation]`
- `sync_from_mysql()`：离线读 `INFORMATION_SCHEMA` + 表/字段注释，写入 SQLite Catalog，成功后 `catalog_version += 1`。外键导入规则见 Locked Decision 8。指标/write_op 不以 INFORMATION_SCHEMA 为准。

`TableRelation` 字段：`left_table`, `right_table`, `left_col`, `right_col`, `cardinality` (`one_to_one` | `one_to_many` | `many_to_one`), `source` (`fk` | `human`), `version`。

- [ ] **Step 1:** 测试指标加载、未知 `metric_id` 抛错、关系图恰好 15 条 FK 边、不含 LLM 猜测边、`cvr`/`ad_roi` 的 `needs_tables` 在当前库可解析。

- [ ] **Step 5: Commit** `feat: add metric semantic layer over existing schema catalog`
