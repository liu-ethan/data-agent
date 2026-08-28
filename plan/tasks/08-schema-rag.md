# Task 8: Schema Agentic RAG

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T2 T4 · 交给：T10 · 里程碑：M3

## Boundary

| | |
| --- | --- |
| **Owns** | 分层 Schema 召回（BM25 + 向量 + 关系图补全）和最多 2 轮 SchemaGap 补检。 |
| **In** | `retrieval/bm25.py`、`vector.py`、`schema_rag.py`、`prompt/retrieval.yaml`、`tests/test_schema_rag.py`。向量写入 `embeddings.sqlite`。 |
| **Out** | MetricCompiler、查询 Skill 图节点编排、`interrupt()`、往 Catalog 加边、Coordinator。 |
| **Must not** | 调用 `interrupt()`（返回 `SchemaGap` / `Ambiguous` 即可）；多路径时偷偷选最短路；LLM/Embedding 添加关系边；`catalog_version` 变了仍用旧索引；无 embedding 配置时硬失败（应退化为仅 BM25）。 |

**Files:**
- Create: `backend/app/retrieval/bm25.py`
- Create: `backend/app/retrieval/vector.py`
- Create: `backend/app/retrieval/schema_rag.py`
- Create: `backend/app/prompt/retrieval.yaml`
- Create: `tests/test_schema_rag.py`

**Interfaces:**
- `retrieve_schema(task: QueryTask, ctx, catalog) -> SchemaBundle | SchemaGap | Ambiguous`

流程（文档 Q02–Q07）：

1. 权限过滤 Catalog。
2. LLM 把指标/维度/筛选改写成 `table_queries[]`（测试用假 LLM）。
3. BM25（表名、别名、缩写）+ 向量（业务描述）召回表 TopK。
4. 仅在候选表内召回字段。
5. 关系图补全 JOIN 路径：两点间 **恰好一条** 审核路径才自动补；≥2 条且会影响 grain/过滤 → 返回 `Ambiguous`（Coordinator 再 HITL）。不新建边，不默默选最短路。
6. 覆盖检查：指标 deps、维度、筛选、时间字段、JOIN 是否齐全。
7. 不足：生成 `SchemaGap`，全局字段检索，把命中字段的表加入候选，回到 4。最多 2 轮；无新增则返回 `SchemaGap`/`Ambiguous` 给调用方，**本模块不 interrupt()**。

索引对象三种：表文档、字段文档（完整名 `db.table.column`）、关系图。关系图不进向量猜测。

`catalog_version` 变化必须重建索引并强制旧任务重新召回。

无 embedding 配置时：`vector.py` 返回空命中，BM25 单独工作。

- [ ] **Step 1:** 用合成 Catalog 测：只提 GMV 应召回 `fact_order`+`fact_order_item`；同名字段 `amount` 不应跨表乱配；缺表时补检能把字段所属表加进来。
