# data-agent难点及解决

## 1. 数据库没有GMV、同比等字段，怎么算？

**使用指标语义层，不让LLM猜公式。**

每个指标保存公式、依赖字段、固定筛选、时间字段、单位和版本。Coordinator识别`metric_id`，MetricCompiler将审核定义写入SQL；同比、环比、占比和TopN尽量在MySQL内完成。

```text
同比 =（本期值 - 上年同期值）/ 上年同期值
环比 =（本期值 - 上一周期值）/ 上一周期值
```

对比期为0时返回“无法计算”。指标不存在或存在多个口径时进入HITL。

## 2. “今天”是哪一天？

**时间由服务端提供，模型不能自行判断。**

每次请求注入`request_time_utc`和租户时区，并由规则把“今天、本月”等转换为固定的`[start_time, end_time)`。同一任务跨零点或HITL恢复后仍使用原时间范围；新请求重新取时间。

回答同时返回数据截止时间，避免把尚未同步完成的今日数据当成完整数据。

## 3. 用户问题模糊时怎么追问？

**先查真实候选项，再HITL；LLM只组织追问。**

- 指标歧义：查询指标语义层；
- 商品歧义：调用受限的只读候选查询Tool；
- 时间歧义：查询可用数据范围。

候选项必须经过权限过滤并携带稳定ID。没有结果时只说明未查到，不能编造选项。恢复后重新检查权限和对象状态。

## 4. Coordinator和Skill如何通信？

**使用小型共享State、Skill私有State和结果引用。**

| 数据 | 保存内容 |
| --- | --- |
| Runtime Context | 用户、租户、实时权限、请求时间和数据库连接 |
| Coordinator State | 当前任务、上一轮任务、HITL信息、`result_id`和`operation_id` |
| Skill State | Schema补检、SQL修复、写入预览等单次调用状态 |
| Result Store | SQLite元数据和本地Parquet结果 |

Skill之间只传结构化任务、状态和ID，不传整张结果表。MySQL保存业务数据与写入回执，SQLite保存Agent状态，Parquet保存临时查询结果。

## 5. 查询结果很大，分页能解决吗？

**分页只解决展示问题，不能减少数据库扫描或磁盘占用。**

- 聚合或中小结果：流式写入Parquet，前端按`result_id`分页读取；
- 无约束`SELECT *`或超过硬限制：拒绝并要求缩小时间、字段或筛选范围。

结果状态为`WRITING → READY → EXPIRED → DELETED`。文件先写`.part`，成功后原子重命名；后台按TTL清理过期结果和孤儿文件。

## 6. 能否继续筛选上一轮结果？

**支持已有列上的简单操作。**

筛选、排序、选列和TopN由规则生成参数化DuckDB查询并读取Parquet；新结果记录`parent_result_id`。新增指标、维度、时间或缺失字段时，合并上一轮`QueryTask`并重新查询MySQL。

LLM不能读取完整Parquet，也不能生成任意DuckDB SQL。结果过期时返回`RESULT_EXPIRED`。

## 7. 写入超时，不知道是否提交怎么办？

**不能直接重试，先用`operation_id`查询MySQL主库回执。**

```text
存在且request_hash一致 → 返回原结果
确认不存在             → 使用同一operation_id重试
主库状态无法确认       → UNKNOWN并转人工
```

操作回执、业务变更和数据库审计必须在同一MySQL InnoDB事务中提交。SQLite只保存Agent状态，不能证明业务写入是否成功。

`operation_id`只覆盖同一次操作的网络重试、并发提交和流程恢复，不会识别两个独立创建但内容相同的请求。

## 8. 几千张表如何召回Schema？

**使用分层Schema Agentic RAG，不把全部Schema交给LLM。**

```text
表召回
  → 候选表内字段召回
  → 审核关系补全表关联
  → 检查指标、维度、筛选和时间字段
      ├─ 完整：生成最小Schema
      ├─ 歧义：HITL
      └─ 缺失：SchemaGap → 全局字段补检 → 再次补全
```

一张表对应一条表文档，一个字段对应一条字段文档，外键和审核过的业务关系单独组成关系图。BM25处理名称和缩写，向量检索处理业务描述。字段使用完整标识，权限过滤在召回前完成。

补检最多2轮；无新增对象时停止。Catalog缺少业务说明或关联关系时进入HITL，不能让LLM猜测。

## 9. 如何拦截错误关联和重复统计？

**LLM生成查询骨架，规则决定能否执行。**

SQLGlot解析AST后，网关检查JOIN是否使用本次召回的审核关系。关系图记录关联字段和一对一/一对多关系，指标定义记录数据所在层级；两者结合即可发现一对多JOIN造成的金额重复累加。

这些是通用规则，不需要为520个字段逐个写`if/else`。需要维护的是有效表关系、每张表一行代表什么，以及核心指标定义。`EXPLAIN`只检查执行成本，不能判断业务计算是否正确。

## 10. 为什么不做更多Skill？

**MVP只保留可信查询和受控写入两条主线。**

- 常用指标在SQL中完成，不执行LLM生成的Python；
- 图表、分页和CSV由前端实现，不包装成Skill；
- 不做长期用户记忆、Multi-Agent、预测、聚类和跨数据源分析；
- 写入只覆盖1～2类白名单操作，不做通用或大批量写入。

## 参考资料

- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [SQLGlot](https://github.com/tobymao/sqlglot)
- [MySQL InnoDB锁定读](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)
- [LangGraph Agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)
- [CHESS：大型Schema检索](https://arxiv.org/abs/2405.16755)
