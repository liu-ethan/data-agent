# Spec 07：评测、消融与发布收口

状态：`Draft`

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
  "golden_task_frame": {},
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
  }
}
```

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
- Trace 样本；
- 可复现命令。

## 8. 验收标准

- 评测脚本可重复运行并输出 JSON/CSV。
- 每个简历数字能定位到数据集、代码版本和计算方式。
- 至少保留 5 到 8 个失败案例及改进过程。
- 演示可支持 2 分钟概览和 10 分钟深挖。
- 未通过评测的能力在 README 中明确标记为设计目标或延期。

## 9. 测试证据

- 固定 eval cases。
- 报告快照。
- 消融结果表。
- 安全测试汇总。
- Trace 审查记录。

