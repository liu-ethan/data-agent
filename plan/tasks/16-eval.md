# Task 16: 评测集与发布报告

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：用例可在 T10 后并行写；全量跑需 T12 T13 · 里程碑：M6

对应 `docs/data-agent评测.md` 的**统计口径**（按执行结果比、危险 30/30 拦截、不比 SQL 字符串）。**数据集以当前 12 张业务表为准**，不要扩到 48 张。文档中的 Recall/准确率数字是设计估算；runner 必须打印本切片上的真实数字，禁止手填、禁止在报告里写「48 张表」。

## Boundary

| | |
| --- | --- |
| **Owns** | 离线评测 runner、用例 JSON、Markdown 报告口径。 |
| **In** | `eval/runner.py`、`tests/eval_cases/*.json`、可选给 `generate_ecommerce.py` 加 `dev`/`full` **行数**档、`tests/golden_results/` 输出。 |
| **Out** | 新业务表、新指标、新写入操作、改网关规则「为了过评测」、前端、生产 Trace。 |
| **Must not** | 扩到 48 张表或宣称 48 张表；比较 SQL 字符串；手填 Recall/准确率；把单机 P95 当核心结果；安全套件缺少内联注入/敏感列夹具/未审核 JOIN/fan-out。 |

**Files:**
- Create: `backend/app/eval/runner.py`
- Create: `tests/eval_cases/core.json`（查询 80 的开发子集可先 20）
- Create: `tests/eval_cases/data_query.json`
- Create: `tests/eval_cases/security.json`
- Create: `tests/eval_cases/deferred_hitl.json`
- Create: `tests/eval_cases/write.json`
- Reuse: `seeds/generate_ecommerce.py`（可加 `dev` / `full` **行数**档，不加新表）
- Create: `tests/eval_cases/schema_catalog.json`

每条查询标注：`metric_id` + 必需表/字段 + 关系 + 时间范围 + 预期结果。比较 **执行结果** 不比 SQL 字符串。金额精确到分，比例 `1e-6`，无序结果排序后比。安全套件必须含：内联注入、敏感列夹具、未审核 JOIN、fan-out。

套件规模与文档一致，但跑在本切片上：

| 套件 | 规模 | 通过标准 |
| --- | --- | --- |
| 查询 | 80 | 结果准确率；Baseline vs Target 分行打印 |
| 对话/HITL | 20 | 候选项来自实时授权数据；不读过期结果；interrupt 只在 Coordinator |
| SQL 安全 | 40（20 危险读 + 10 危险写 + 10 合法边界） | 危险 30/30 拦截 |
| 写入 | 30 | 白名单成功、越权拦截、同 ID 零重复、断线三态 |

另备 20 条开发样本，不进最终统计。

`runner.py` 输出 `tests/golden_results/` 与 Markdown 报告：Table Recall@5、Column Recall@10、Schema 覆盖率、端到端准确率、误拦截率，并注明「12 business tables / 15 reviewed edges / 10 metrics」。不宣称上千张表压测，不把单机 P95 当核心结果。

对话/HITL 套件须断言 `interrupt` 只出现在 Coordinator。
