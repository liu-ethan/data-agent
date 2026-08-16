# Spec 04：Schema RAG 与 Coverage

状态：`Implemented`

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

M3 使用 `CatalogRetrievalService` 的固定内存实现；M4 才切换到 BM25、Embedding 和 Reranker。两种实现必须返回相同的 `GroundedContext` 和 `CoverageResult` 契约。

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
      "source_id": "source_ecommerce",
      "domain": "交易",
      "score": 0.91
    }
  ],
  "fields": [
    {
      "field_id": "field_orders_paid_at",
      "name": "orders.paid_at",
      "data_type": "DATETIME",
      "nullable": false,
      "classification": "BUSINESS_TIME",
      "aliases": ["支付时间"],
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
  "coverage": "SUFFICIENT",
  "token_count": 684,
  "schema_version": "grounded_context_v1"
}
```

检索结果中的 `score` 必须归一化到 `[0, 1]`，并记录 `retrieval_method`、`index_version` 和 `permission_policy_version`。Embedding、Reranker 和 tokenizer 的模型标识必须进入 Trace，但不得记录 API key。

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
    "narrow_query": "华东 region shop_id 映射",
    "retrieval_round": 1,
    "schema_version": "schema_gap_v1"
  },
  "schema_version": "coverage_v1"
}
```

## 6. 补检规则

- 首次召回和补检复用同一个 `retrieval_node`。
- 补检输入必须包含 `existing_context_id` 和 `SchemaGap`。
- 补检不能扩大到全量数据源。
- 每个任务最多 2 次召回。
- 两轮后仍不足，进入 `ASK_USER` 或失败响应。
- `AMBIGUOUS` 需要候选差距小于 `ambiguity_score_gap` 时进入 `ASK_USER`；差距足够大才可判定为 `SUFFICIENT`。
- 权限过滤发生在候选进入 Reranker 前；未授权对象不能只在最终输出阶段删除。

## 7. 上下文预算

默认预算：

```yaml
max_source_candidates: 3
max_object_candidates: 5
max_fields_per_object: 8
max_join_hops: 2
max_context_tokens: 3000
max_retrieval_rounds: 2
min_rerank_score: 0.55
ambiguity_score_gap: 0.08
```

Token 预算按最终 JSON 序列化文本计算，使用配置指定的 tokenizer；没有 tokenizer 配置时使用固定的 `cl100k_base` 估算器，并把 `tokenizer_version` 写入 Trace。超出预算时按 `metric -> time field -> required fields -> joins -> optional aliases` 的顺序裁剪，不能裁掉权限、类型、Join 基数或必需过滤条件。

## 8. 验收标准

- 正确 Object/Field 能进入配置 TopK。
- 权限外对象不出现在候选、Prompt 和 Trace 中。
- P95 GroundedContext 不超过 Token 预算。
- 人为删除首次召回字段后，第二轮能按 SchemaGap 补齐。
- 禁用向量检索或 Reranker 后能生成对照指标。
- 每个候选都能回溯到 `catalog_version`、`index_version` 和权限策略版本。

## 9. 测试证据

- 100 source、1000 table、约 3 万 field 的合成元数据。
- 50 到 100 条 Schema Linking 评测。
- Recall@K、Context Precision、P95 token、P95 latency 报告。
- 权限过滤测试。
- SchemaGap 回归测试。

当前实现证据（2026-08-16）：

- `MySQLSchemaCollector` 真实查询 `information_schema.TABLES / COLUMNS / KEY_COLUMN_USAGE`，并对物理 Schema + 人工指标/别名/审核 Join 生成内容哈希版本。
- `CatalogIndexBuilder` 在 Milvus Lite 中构建 Source/Object/Field/Relation 四层 staging collections，校验 Schema、维度和行数后切换 MySQL active manifest。
- 本机实测：8 object、41 field、13 physical FK，FastEmbed `BAAI/bge-small-zh-v1.5` 512 维；四层文档数为 1/13/41/17。
- `tests/test_milvus_catalog_index.py` 使用真实临时 Milvus Lite 验证四层构建、幂等重建、Manifest、维度、Source 和字段 Classification 过滤。
- 100 source / 1000 table / 30000 field 的合成干扰集仍由 `tests/test_retrieval.py` 校验 TopK 和 3000 Token 上限。
- 真实生产 HTTP Golden 评测 `reports/task3-production-evaluation.json`：10/10 通过，包含 8 SUCCEEDED、1 WAITING_FOR_USER 和 1 PERMISSION_DENIED。
- 真实 Schema Linking 评测 `reports/schema-rag-evaluation.json`：70/70 通过；Object Recall@K 1.0、Field Recall@K 1.0、Context Precision 0.327571、P95 context token 2463（上限 3000）、P95 latency 3105.21ms、敏感候选泄漏 0。该报告固定记录 catalog/index/Embedding/Reranker 版本，可用 `make evaluate-rag` 重跑，并可用 `--disable-reranker` 或 `--dense-weight` 生成消融对照。
- 关闭 Reranker 的同一组消融报告 `reports/schema-rag-evaluation-no-reranker.json` 同样为 70/70、Recall@K 1.0，P95 latency 从 3105.21ms 降至 18.66ms；说明当前 70 条确定性案例不足以体现 Reranker 的质量增益，但真实 Reranker 的延迟成本已被量化，不能据此虚构质量提升。
