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
- 补检输入必须包含 `existing_context_id`、已有 `GroundedContext` 和 `SchemaGap`。
- 补检不能扩大到全量数据源：跳过 Source 层检索，把 `source_ids` 钉在已有对象的授权 source 上，并与上一轮字段/对象合并。
- 每个任务最多 2 次召回。
- 两轮后仍不足，进入 `ASK_USER` 或失败响应。
- `AMBIGUOUS` 需要候选差距小于 `ambiguity_score_gap` 时进入 `ASK_USER`；差距足够大才可判定为 `SUFFICIENT`。判定只允许发生在 `CoverageEvaluator`。
- 权限过滤发生在候选进入 Reranker 前；未授权对象不能只在最终输出阶段删除，也不能出现在 Prompt 或 Trace 中。

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

仓库内验收以测试为准；一次性评测报告由 `make evaluate-rag` 生成，不作为合入证据。

- 合成干扰集 TopK 与 Token 上限 (`tests/test_retrieval.py`)。
- 生产检索权限前置、Embedding 版本校验、Reranker 拒绝未授权 ID (`tests/test_production_retrieval.py`)。
- Milvus Lite 四层构建、幂等重建、Source 和 Classification 过滤 (`tests/test_milvus_catalog_index.py`)。
- information_schema 采集与 BM25 归一化 (`tests/test_schema_catalog.py`)。
- Spec 04 §6/§7/§8 不变量 (`tests/test_schema_rag_spec04.py`)：未授权 source 不进 Reranker/Trace、SchemaGap 补检钉源并保留上一轮字段、CoverageEvaluator、预算先裁 alias 再裁多余 Join、候选携带 catalog/index/permission 版本、关闭 Reranker 仍返回 GroundedContext。
- 完整 Schema Linking 对照通过 `scripts/evaluate_schema_rag.py`（`--disable-reranker` / `--dense-weight`）重跑，报告写入 `reports/`，不提交仓库。

## 10. 模块拆分 (M4 重构后)

| 文件 | 行数 | 职责 |
|---|---|---|
| `services/catalog_retrieval.py` | ~360 | 生产分层检索编排：权限前置、BM25+dense 融合、补检钉源、Reranker |
| `services/coverage.py` | ~70 | CoverageEvaluator，唯一 Coverage/SchemaGap 判定 |
| `services/catalog_baseline.py` | ~310 | M3 固定目录、合成干扰集、本地 hybrid 对照 |
| `services/embedding.py` | ~150 | FastEmbed / OpenAI-compatible embedding |
| `services/schema_catalog.py` | ~270 | information_schema 采集、SearchDocument、版本哈希 |
| `repositories/catalog.py` | ~700 | MySQL 权威目录、BM25、hydrate、manifest |
| `repositories/catalog_index.py` | ~280 | Milvus 四层索引与 staging builder |

`ContextBudgeter` 和 `PassthroughReranker` 与生产检索同文件，因为它们只被检索编排调用；Coverage 判定独立可测，所以单独成模块。
