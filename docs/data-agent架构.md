# data-agent项目架构

## 1. 项目定位

面向电商经营分析的可信数据Agent，支持业务指标问数、受限多轮查询和小范围数据写入。

采用`Coordinator+2个Skill`：Coordinator负责意图识别、路由、多轮状态和HITL；可信查询Skill与受控写入Skill共用Schema Catalog、SQL安全网关和MySQL执行Tool。MVP不做通用数据分析平台。

## 2. 总体架构

```text
用户请求
   ↓
Coordinator
意图识别 → 结构化任务 → 路由 → HITL → 最终回答
   ├─ 可信查询Skill
   │  指标定义 → Schema Agentic RAG → 查询骨架 → 指标编译 → 安全执行
   └─ 受控写入Skill
      WritePlan → 白名单SQL → 变更预览 → HITL → MySQL事务

公共组件：Schema Catalog / SQL安全网关 / MySQL执行Tool
存储：MySQL业务库 / SQLite状态与元数据 / Parquet临时结果
```

Coordinator只保存任务和结果引用。Skill维护单次调用的内部状态，原始查询结果不进入Checkpoint。

## 3. 模块职责

| 模块 | 职责 | 输出 |
| --- | --- | --- |
| Coordinator | 识别查询或写入意图，补全时间与筛选条件，管理HITL和多轮状态 | `QueryTask`、`WriteTask`、回答 |
| 可信查询Skill | 召回Schema、生成查询、固定指标口径并安全执行 | `result_id`、摘要、数据依据 |
| 受控写入Skill | 将写入计划映射到白名单SQL，经预览和确认后执行 | `operation_id`、状态、审计ID |
| 指标语义层 | 保存GMV等指标的公式、固定筛选、时间字段和版本 | `metric_id`对应的审核定义 |
| Schema Catalog | 保存表、字段、业务说明和审核过的表关系 | 最小Schema与关联关系 |
| SQL安全网关 | 校验最终SQL，隔离查询与写入策略 | 通过或拒绝原因 |
| MySQL执行Tool | 使用独立读写账号执行SQL | 查询流或写入回执 |

`MetricCompiler`是规则组件：LLM只引用`metric_id`，由它把审核过的指标公式写入SQL，避免模型自创GMV口径。

## 4. 查询流程

```text
识别指标、维度、筛选和时间
  → 权限过滤
  → 召回候选表
  → 在候选表内召回字段
  → 用审核关系补全表关联
  → 信息不足时生成SchemaGap并全局补检（最多2轮）
  → LLM生成结构化查询骨架
  → MetricCompiler写入指标公式
  → SQL安全网关
  → 只读MySQL执行
  → Parquet临时结果 + result_id
```

SQL安全网关通过SQLGlot解析MySQL AST，并按规则检查：

- 仅允许单条`SELECT`，拒绝`SELECT *`、锁定读、文件操作和危险函数；
- 表、字段和权限必须与本次任务一致；
- JOIN必须使用Schema Catalog中的审核关系；
- 根据表关系和指标所在数据层级，拦截一对多JOIN造成的重复统计；
- 通过`EXPLAIN`限制扫描量，并在执行端限制时间、行数和文件大小。

SQLGlot只负责解析，不负责判断业务含义。表关系、数据层级和指标定义缺失时，系统进入HITL或拒绝执行，不能让LLM猜测。

查询结果先写`<result_id>.part`，成功后原子重命名为Parquet并标记`READY`。超过硬限制时中止查询并删除临时文件。

### 受限多轮查询

- 只筛选、排序、选列或TopN：校验权限与TTL后，用DuckDB读取已有Parquet并生成新`result_id`；
- 新增指标、维度、时间或缺失字段：合并上一轮`QueryTask`，重新查询MySQL；
- 结果过期：返回`RESULT_EXPIRED`，由用户确认是否重查。

## 5. 写入流程

```text
自然语言请求
  → LLM生成WritePlan，不生成写SQL
  → 操作注册表选择审核过的参数化SQL模板
  → 只读预检目标主键、影响行数和数据版本
  → 生成operation_id、request_hash和预览
  → HITL确认
  → 重新检查权限
  → SQL安全网关校验最终模板SQL
  → MySQL InnoDB事务
     回执 → 版本复验 → 业务变更 → 审计 → COMMIT
```

MVP只注册1～2类操作，单次最多影响100行。写入必须针对明确主键，或可在事务内锁定并复验的有限目标。

`operation_id`只保证同一次操作在重试、断线或LangGraph恢复时不会重复生效。两个独立创建的业务请求会得到不同ID，不属于该幂等范围。

提交响应丢失时，只查询MySQL主库回执：

- 已存在且`request_hash`一致：返回原结果；
- 确认不存在：使用同一`operation_id`重试；
- 主库状态无法确认：标记`UNKNOWN`并转人工。

回执、业务变更和数据库审计必须位于同一MySQL实例，并在同一InnoDB事务中提交。SQLite不参与业务事务。

## 6. 状态与存储

| 存储 | 内容 | 生命周期 |
| --- | --- | --- |
| MySQL | 电商业务表、写入回执、数据库审计 | 业务级 |
| SQLite | LangGraph Checkpoint、任务、HITL、指标/Schema/结果元数据 | 会话或配置级 |
| Parquet | 未超限的临时查询结果 | TTL到期删除 |

Result Store状态为`WRITING → READY → EXPIRED → DELETED`。元数据记录来源表、字段和权限版本；读取接口只接收服务端生成的`result_id`。权限已变化时拒绝读取并重新查询。读取与TTL删除使用同一结果锁。

Schema Catalog通过离线任务同步DDL、注释和审核关系。Schema变化后递增`catalog_version`，旧版本任务必须重新召回。

## 7. MVP边界

包含：

- 单Coordinator、可信查询Skill、受控写入Skill；
- 指标语义层、Schema Agentic RAG、SQL安全网关；
- 数据库候选项驱动的HITL澄清；
- 本地Parquet、TTL、分页和结果内简单筛选；
- 1～2类白名单写入、写前预览、HITL、事务回执和审计；
- 单机单实例运行与离线评测。

不包含：

- Multi-Agent、数据计算Skill、图表报告Skill和长期用户记忆；
- 任意Python/Pandas代码、预测、聚类和跨数据源分析；
- MinIO、Redis、分库分表、多实例部署和跨库事务；
- 无限制明细导出、任意临时结果SQL、通用写入和在线批量写入；
- 已提交操作的一键回滚。撤销必须作为新的补偿操作再次HITL。

## 8. 主要风险

| 风险 | 控制办法 |
| --- | --- |
| LLM自创指标公式 | 只输出`metric_id`，由MetricCompiler写入审核公式 |
| 错误关联或重复统计 | JOIN只能使用审核关系；根据一对多关系和指标所在数据层级进行规则校验 |
| SQL通过解析但仍危险 | 权限、语法白名单、只读账号、`EXPLAIN`和运行时限制共同防护 |
| HITL恢复重复执行代码 | 准备、审批、执行拆成三个节点；审批节点只调用`interrupt()` |
| 确认期间数据变化 | 最终事务按版本条件更新或锁定目标；冲突后重新预览和确认 |
| 同一操作被重复提交 | `operation_id`唯一键、`request_hash`和同事务回执 |
| Parquet半写入或读删冲突 | `.part`原子重命名、状态机、读删互斥锁和孤儿文件清理 |
| SQLite多实例写冲突 | MVP限定单机单实例；扩展时更换PostgreSQL Checkpointer和对象存储 |

详细评测见[data-agent评测.md](data-agent评测.md)。
