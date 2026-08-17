# Spec 07：评测、消融与发布收口

状态：`Implemented`

对应里程碑：M7

## 1. 范围

建立固定评测集、指标计算、消融实验、报告输出和面试演示材料。任何项目亮点数字都必须来自这个阶段的可复现实验。

## 2. In Scope

- 80 到 100 条任务级评测。
- 危险 SQL、越权和 Prompt Injection 用例。
- 大规模合成 Schema 干扰评测。
- Checkpoint、HITL、长期记忆测试。
- 消融实验。
- JSON/CSV 报告。
- Trace 抽样审查。
- README 和演示页更新。

## 3. Out of Scope

- 用未固定 Prompt 或临时数据生成简历数字。
- 只用 SQL Execution Accuracy 代表任务完成率。
- 把设计目标写成已实现结果。

## 4. 评测用例契约

```json
{
  "case_id": "eval_001",
  "category": "multi_step_data_query",
  "user_id": "u_east_user",
  "messages": [
    {"role": "user", "content": "对比本月和上月各品类 GMV，找出下降最多的三个品类。"}
  ],
  "golden_task_frame": {
    "intent": "DATA_QUERY",
    "metric_ids": ["gmv"],
    "dimension_ids": ["categories.category_name"],
    "time_range": {
      "start": "2026-08-15T00:00:00+08:00",
      "end": "2026-08-16T00:00:00+08:00",
      "timezone": "Asia/Shanghai"
    },
    "filters": []
  },
  "required_objects": ["orders", "order_items", "products", "categories"],
  "required_fields": ["orders.paid_at", "order_items.item_paid_amount"],
  "expected_action_sequence": ["RETRIEVE", "GENERATE", "EXECUTE", "RESPOND"],
  "golden_result_ref": "eval_001_result.json",
  "should_clarify": false,
  "should_reject": false,
  "budgets": {
    "max_steps": 6,
    "max_retrieval_rounds": 2,
    "max_seconds": 30
  },
  "data_version": "seed_v1",
  "catalog_version": "catalog_v1",
  "result_compare": {
    "row_order": "explicit",
    "numeric_abs_tolerance": 0.01,
    "numeric_rel_tolerance": 0.0001,
    "null_equals_zero": false
  },
  "schema_version": "eval_case_v1"
}
```

`golden_task_frame`、`required_objects`、`required_fields`、Action 序列和结果快照都必须非空。多轮、Interrupt 和 HITL 用例使用 `messages` 数组表达完整会话，不允许用单条消息代替多轮评测。

可运行任务级用例必须覆盖问数和多轮，不能用 Schema 检索用例占满 80～100 条分母：

- 问数类（`single_turn_data_query` / `metric_query` / `refund_query` / `empty_result` / `multi_step_data_query`）不少于 20 条；
- 多轮类（`follow_up` / `multi_turn` / `checkpoint` / `long_term_memory`）不少于 12 条；
- `schema_catalog` 不超过 25 条。大规模 Schema Linking 对照放在 `tests/schema_rag_cases.json`，由 `scripts/evaluate_schema_rag.py` 运行，不计入任务完成率。

生产评测不得读取进程内 `AgentState`。`POST /api/chat` 的 `ChatResponse.evidence` 和终态 SSE 的 `RuntimeEvent.evidence` 必须带上公开评测字段：`intent`、`metric_ids`、`object_names`、`field_names`、`coverage`、`retrieval_rounds`、`grounded_context_tokens`、`schema_gap_recovered`。`schema_gap_recovered` 仅在召回轮次 ≥ 2 时取值，否则为 `null`。这些字段属于证据栏数据，不是隐藏推理。

## 5. 指标

- Task Completion Rate。
- TaskFrame Accuracy。
- Object/Field Recall@K。
- Context Precision。
- Schema Gap Recovery。
- Result Accuracy。
- Action Routing Accuracy。
- Average Graph Steps。
- Security Pass Rate。
- HITL Resume Success。
- Follow-up Resolution Accuracy。
- Checkpoint Recovery Success。
- Long-term Memory Precision。
- P95 Latency。
- Average Token Cost。
- P95 GroundedContext Tokens。

指标口径：

- `Task Completion Rate`：结果状态、是否应澄清/拒绝、权限和结果比较全部通过的 case 占比。
- `Result Accuracy`：按 `result_compare` 比较列集合、行集合和数值，不只比较 SQL 字符串。
- `Security Pass Rate`：危险 SQL、越权、敏感字段和 Prompt Injection 用例按预期拒绝或隔离的占比。
- `P95 Latency`：从 API 接收请求到最终 SSE 完成事件，成功和失败分别报告。
- 其他指标必须在报告中写明分子、分母和过滤条件。

## 6. 必做消融

至少比较：

- 全量 Schema 注入 vs 分层最小 GroundedContext。
- 只有 BM25 vs BM25 + Embedding + Reranker。
- 禁用 SchemaGap 补检 vs 启用补检。
- 全历史 Prompt vs 摘要 + 引用 + 按需投影。
- SQL Execution Accuracy vs Task Completion Rate。

## 7. 报告要求

报告必须记录：

- 数据版本；
- 代码版本；
- 模型和 Prompt 版本；
- 配置预算；
- 每个指标的计算方式；
- 失败案例；
- 每个失败案例的改进过程（`reports/failure-improvements.json`：基线错误、代码改动、复现命令、复跑后结果）；
- Trace 样本；
- 可复现命令。
- 数据库时区、评测时间锚点、随机种子、LLM provider/protocol/model 和 tokenizer 版本。

## 8. 验收标准

- 评测脚本可重复运行并输出 JSON/CSV。
- 每个简历数字能定位到数据集、代码版本和计算方式。
- 至少保留 5 到 8 个失败案例及改进过程。
- 未通过评测的能力在 README 中明确标记为设计目标或延期。
- 2 分钟 / 10 分钟面试讲稿本轮不做，等生产 HTTP 报告达到可写入 README 的水平后再补。

## 9. 测试证据

- 固定 eval cases。
- `ChatResponse.evidence` / `RuntimeEvent.evidence` 可被生产 HTTP 评测采集。
- 报告快照。
- 消融结果表。生产 HTTP 报告的 `ablations` 必须来自 `production_ablations()`：SQL vs TCR 和 GroundedContext token 用当次 outcomes，不得回填 test-double 数字。
- 安全测试汇总。
- Trace 审查记录。
- 每个失败 case 的错误码、最后 Action、版本信息和最小复现命令。
