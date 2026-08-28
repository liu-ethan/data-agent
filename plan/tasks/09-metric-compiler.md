# Task 9: MetricCompiler

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T2 T4 · 交给：T10 · 里程碑：M3

## Boundary

| | |
| --- | --- |
| **Owns** | 把 `QuerySkeleton` + 审核 `MetricSpec` 编译成参数化 `CompiledQuery`。 |
| **In** | `compiler/metric_compiler.py`、`tests/test_metric_compiler.py`。 |
| **Out** | 网关判定、MySQL 执行、RAG、Skill 图、HITL。 |
| **Must not** | 使用骨架里「看起来像公式」的字符串；多指标在行级 JOIN 后再 `COUNT(DISTINCT)` 充数；输出非参数化 SQL；把贡献度做成 comparison；对比期为 0 时除零。 |

**Files:**
- Create: `backend/app/compiler/metric_compiler.py`
- Create: `tests/test_metric_compiler.py`

**Interfaces:**
- `compile(skeleton: QuerySkeleton, metrics: list[MetricSpec], time: TimeRange) -> CompiledQuery`

规则：

- 把审核公式、固定筛选、时间字段、粒度写入 SELECT；筛选值进 `params`。
- 多个 `metric_ids`：按 `grain_table` 分组，每组一个聚合 CTE，再按 `group_by` 维度对齐。`aov` / `refund_rate` / `cvr` / `ad_roi` / `net_gmv` 必须走这条路径，禁止在行级 JOIN 后再 `COUNT(DISTINCT)` 充数。
- YoY：上年同期同 grain；MoM：上一周期；对比期为 0 → 结果列为空并在 summary 标明「无法计算」。
- 禁止使用 skeleton 里任何「看起来像公式」的字符串。
- 输出仍是单条 SELECT（可用 CTE）+ `params`。
- 同一输入两次编译，SQL 与 params 字节级相同。

- [ ] **Step 1:** 同一 skeleton 两次编译 SQL 与 params 字节级相同（确定性）；改 `metric.version` 后 SQL 变化；`gmv`+`order_count` 的 SQL 含两个 grain CTE，且 `fact_order_item` 不在订单 grain CTE 的 FROM 里。

- [ ] **Step 5: Commit** `feat: add deterministic metric compiler for audited formulas`
