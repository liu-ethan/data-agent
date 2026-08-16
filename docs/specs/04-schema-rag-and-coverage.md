# Spec 04：Schema RAG 与 Coverage

状态：`Draft`

对应里程碑：M4

## 1. 范围

实现权限前置的分层混合检索、Coverage 评估、SchemaGap 补检和上下文预算控制，使 Agent 在大规模 Schema 干扰下仍只看到有限 GroundedContext。

## 2. In Scope

- Source/Domain、Object、Field/Entity、Relation 四层索引。
- BM25 + Embedding + Reranker。
- 权限前置过滤。
- MySQL 权威元数据版本校验。
- Verified Join 1 到 2 跳扩展。
- CoverageEvaluator。
- SchemaGap 定向补检。
- ContextBudgeter。
- 合成元数据评测。

## 3. Out of Scope

- 自动学习新 Schema。
- 把完整 `information_schema` 注入 Prompt。
- 跨业务域任意 Join。
- 为合成元数据生成完整事实数据。

## 4. GroundedContext 契约

```json
{
  "context_id": "ctx_101",
  "catalog_version": "catalog_v18",
  "objects": [
    {
      "object_id": "obj_orders",
      "name": "orders",
      "grain": "order",
      "score": 0.91
    }
  ],
  "fields": [
    {
      "field_id": "field_orders_paid_at",
      "name": "orders.paid_at",
      "classification": "BUSINESS_TIME",
      "score": 0.88
    }
  ],
  "metrics": ["gmv"],
  "join_paths": [
    {
      "left": "orders.order_id",
      "right": "order_items.order_id",
      "cardinality": "one_to_many",
      "verified": true
    }
  ],
  "coverage": "SUFFICIENT"
}
```

## 5. CoverageResult 契约

```json
{
  "status": "PARTIAL",
  "covered": ["metric.gmv", "time.orders.paid_at"],
  "missing": ["entity.region"],
  "ambiguous": ["sales_overview"],
  "confidence_notes": ["top two business presets are close"],
  "schema_gap": {
    "missing_concepts": ["region shop mapping"],
    "candidate_object_ids": ["obj_orders", "obj_shops"],
    "narrow_query": "华东 region shop_id 映射"
  }
}
```

## 6. 补检规则

- 首次召回和补检复用同一个 `retrieval_node`。
- 补检输入必须包含 `existing_context_id` 和 `SchemaGap`。
- 补检不能扩大到全量数据源。
- 每个任务最多 2 次召回。
- 两轮后仍不足，进入 `ASK_USER` 或失败响应。

## 7. 上下文预算

默认预算：

```yaml
max_source_candidates: 3
max_object_candidates: 5
max_fields_per_object: 8
max_join_hops: 2
max_context_tokens: 3000
max_retrieval_rounds: 2
```

## 8. 验收标准

- 正确 Object/Field 能进入配置 TopK。
- 权限外对象不出现在候选、Prompt 和 Trace 中。
- P95 GroundedContext 不超过 Token 预算。
- 人为删除首次召回字段后，第二轮能按 SchemaGap 补齐。
- 禁用向量检索或 Reranker 后能生成对照指标。

## 9. 测试证据

- 100 source、1000 table、约 3 万 field 的合成元数据。
- 50 到 100 条 Schema Linking 评测。
- Recall@K、Context Precision、P95 token、P95 latency 报告。
- 权限过滤测试。
- SchemaGap 回归测试。

