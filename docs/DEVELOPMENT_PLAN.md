# Data Runtime Agent MVP 开发计划

> 依据 [ARCHITECTURE.md](./ARCHITECTURE.md) 的目标架构制定。当前目录已有目标架构、开发计划和 specs；本文从“尚未编码”开始安排实现；所有简历指标必须在对应验收阶段真实测得。

开发时请以 [SPEC_DRIVEN_DEVELOPMENT.md](./SPEC_DRIVEN_DEVELOPMENT.md) 和 [specs/README.md](./specs/README.md) 为落地依据。本文只描述排期和优先级，具体输入输出、不变量、错误语义和验收证据以对应 spec 为准。

## 1. 开发目标

在 6 周左右完成一个可演示、可评测的电商 Data Runtime Agent MVP，打通以下闭环：

```text
用户问题
→ agent_node 理解任务并选择动作
→ retrieval_node 召回或补检 Schema
→ query_generation_node 生成查询计划
→ execution_gateway_node 安全校验并执行
→ agent_node 判断目标是否完成
→ response_node 输出回答、表格或图表
→ Checkpoint / Memory Hook 保存可恢复状态
```

MVP 必须证明四件事：

1. Agent 能根据 Coverage 和执行结果继续行动，而不是只能生成一次 SQL；
2. Schema 增长后仍使用有限上下文，不把全量元数据注入模型；
3. LLM 不能绕过权限、SQL 安全、成本和写入审批；
4. 多轮追问可以依靠 Checkpoint、Artifact 和长期偏好可靠恢复。

## 2. 范围和优先级

### 2.1 P0：必须完成

- 8 张电商业务表、指标目录和固定种子数据；
- 五个顶层 LangGraph Node；
- 权限前置的 Schema 混合检索与 SchemaGap 补检；
- SQLGlot AST、表列/行级权限、EXPLAIN、超时和结果契约；
- MySQL 只读执行、ResultRepository 和 Trace；
- 30 个基础任务与 30 个危险查询测试；
- React 对话页和结果表格。

### 2.2 P1：形成项目亮点

- MySQL Checkpointer、滚动摘要和 Artifact 指代；
- 澄清型 LangGraph Interrupt；
- PromptContextBuilder 和长期偏好 Store；
- ECharts 图表与 CSV；
- 100 源、1000 表、约 3 万字段的合成元数据干扰集；
- 80～100 条任务级评测和消融实验。

### 2.3 P2：时间充足再完成

- Admin `INSERT/UPDATE`；
- MutationPreview、审批型 HITL、参数化事务与审计；
- 更完整的并发、恢复和长期记忆污染测试。

如果进度不足，应优先保证只读链路的正确率、安全性和评测，不应为了展示写入而牺牲核心问数质量。

## 3. 总体里程碑

| 阶段 | 建议时间 | 核心产物 | 阶段出口 |
| --- | ---: | --- | --- |
| M0 工程基线 | 2 天 | 项目骨架、Docker、配置和测试框架 | 服务可启动，CI/本地测试可运行 |
| M1 数据与语义目录 | 4 天 | 电商数据、指标、权限、关系和 Golden Case | 人工 SQL 能稳定得到 Golden Result |
| M2 可信只读网关 | 4 天 | SQLGlot Guard、RLS、EXPLAIN、Executor | 任何 SQL 执行前都经过同一 Gateway |
| M3 最小 Runtime Agent | 5 天 | 五 Node Graph、TaskFrame、查询与响应 | 10 个单轮问题端到端跑通 |
| M4 Schema RAG 与补检 | 5 天 | Milvus/BM25、Reranker、Coverage、SchemaGap | 在干扰 Schema 中召回正确对象并控制上下文 |
| M5 多轮记忆与制品 | 5 天 | Checkpointer、Artifact、摘要、偏好、React/ECharts | 多轮指代和 Interrupt 可恢复 |
| M6 Admin 写入 | 3 天 | WriteGateway、Preview、HITL、事务和审计 | 写入可确认、可恢复、可审计且不可越权 |
| M7 评测与面试收口 | 4 天 | 评测报告、Trace、消融、演示脚本 | 数据可复现，简历数字有测试依据 |

总计约 32 个有效开发日。若只有 4 周，先完成 M0～M5，并将 M6 保留为设计说明。

## 4. M0：工程基线

### 4.1 任务

- [ ] 创建 `backend`、`frontend`、`migrations`、`tests` 和 `configs` 目录；
- [ ] 使用 Python、FastAPI、Pydantic、SQLAlchemy/Alembic 初始化后端；
- [ ] 使用 React + TypeScript 初始化前端；
- [ ] 编写 Docker Compose，一条命令启动 MySQL、后端和前端；Milvus 在 M4 接入；
- [ ] 建立 `.env.example`，区分 reader、writer 和 migration 账号；
- [ ] 配置精确 frontend origin 白名单、OPTIONS 预检、Bearer Authorization 和 SSE 跨域；
- [ ] 增加 `ruff`、类型检查、单元测试和前端检查命令；
- [ ] 定义统一错误结构、Trace ID 和日志脱敏规则；
- [ ] 建立 `docs/decisions`，记录重要架构取舍。

### 4.2 第一批核心数据结构

- `TaskFrame`
- `ContextFrame`
- `PermissionContext`
- `GroundedContext`
- `CoverageResult / SchemaGap`
- `QuerySpec / QueryPlan`
- `ResultObservation`
- `ArtifactSpec`
- `AgentState`

先定义 Pydantic Schema，再实现 Node，避免不同模块各自使用一套字典。

### 4.3 完成标准

- 后端 `/health`、前端首页和 MySQL 均可通过一个命令启动；
- 配置中不存在真实密码；
- 空测试集可以在本地一次执行；
- Trace ID 能从 API 请求贯穿到数据库日志。

## 5. M1：电商数据与语义目录

### 5.1 业务数据

创建并填充：

- `orders`
- `shops`
- `order_items`
- `products`
- `categories`
- `refunds`
- `refund_items`
- `users`

种子数据不能只有“正常答案”，必须包含：

- 一笔订单多个商品，验证事实表 Join 重复累计；
- 部分退款和多次退款；
- 支付失败、取消和未支付订单；
- 没有数据的日期和地区；
- 两个用户拥有不同的 `shop_id` 范围；
- 手机号、身份证等敏感字段；
- 商品名近义、地区名别名和状态枚举；
- 至少一组重复值，供多轮字段示例验证。

### 5.2 语义目录

优先使用 MySQL 管理以下版本化目录：

- `catalog_sources`
- `catalog_objects`
- `catalog_fields`
- `metric_definitions`
- `business_presets`
- `table_relations`
- `entity_aliases`
- `permission_policies`

首批指标：GMV、支付订单数、支付买家数、客单价、退款金额、金额退款率和品类 GMV。

### 5.3 Golden Case

先人工编写 20 条问题、Golden SQL 和 Golden Result，其中至少包括：

1. 昨天各品类 GMV；
2. 最近 15 天华东销售概览；
3. 本月和上月 GMV 对比；
4. 下降最多的三个品类；
5. 退款金额和金额退款率；
6. 无结果时间段；
7. 跨订单与明细表但不能重复累计的查询；
8. 不同用户执行同一问题得到不同授权范围。

### 5.4 完成标准

- 数据库重建后 Golden Result 不变化；
- 每个指标都有公式、时间字段、粒度和适用状态；
- 每条 Verified Join 都有方向、基数和版本；
- 人工 SQL 覆盖关键口径错误和一对多 Join 风险。

## 6. M2：可信只读执行网关

在接入 LLM 之前先完成 ReadGateway，避免开发过程中出现“模型 SQL 暂时直连数据库”的旁路。

### 6.1 执行链路

```text
CandidateSQL
→ SQLGlot MySQL AST
→ 单语句 SELECT / WITH
→ 表列与敏感字段权限
→ 注入行级范围并重新解析
→ 指标、JOIN、GROUP BY 和时间语义
→ EXPLAIN FORMAT=JSON
→ LIMIT、超时和只读账号
→ ResultRepository
→ 结果契约检查
→ ResultObservation
```

### 6.2 任务

- [ ] 拒绝多语句、DML、DDL、FILE、GRANT 和系统库；
- [ ] 校验所有表、列和函数是否在允许范围；
- [ ] 注入 `shop_id` 行级条件，并对改写结果再次解析；
- [ ] 配置事实表必须带时间过滤、最大表数和最大 Join 数；
- [ ] 执行 EXPLAIN，限制全表扫描和预估成本；
- [ ] 使用 `agent_reader`，设置执行超时和最大返回行数；
- [ ] 保存结果并只向 Graph 返回 `result_id + ResultSummary`；
- [ ] 检查列、粒度、时间覆盖、空结果和数值类型；
- [ ] 记录原始 SQL Hash、改写 SQL Hash、权限版本、成本和耗时。

### 6.3 安全测试

准备至少 30 条危险查询，覆盖：

- DDL、DML、文件读写和多语句；
- 子查询或 CTE 中的越权表；
- 使用 `SELECT *` 暴露敏感列；
- 注释、大小写和函数变体；
- 缺少行级过滤；
- 笛卡尔积、无时间条件的大表扫描；
- 一对多 Join 导致的重复聚合。

### 6.4 完成标准

- 30/30 危险用例被预期规则拦截；
- 允许的 Golden SQL 全部正常执行；
- 应用不存在绕过 ReadGateway 的数据库查询入口；
- 空结果被标记为 `EMPTY`，不能回答成数值 0。

## 7. M3：最小 Runtime Agent

本阶段先使用小规模目录或固定 Retrieval Service，目标是验证五节点 Graph 和状态流转，不同时调试 Milvus 召回。

### 7.1 Graph

实现五个顶层 Node：

1. `agent_node`：首轮生成 TaskFrame，后续选择 `RETRIEVE / GENERATE / EXECUTE / ASK_USER / RESPOND`；
2. `retrieval_node`：先调用可替换的 CatalogRetrievalService；
3. `query_generation_node`：生成 QuerySpec + CandidateSQL，证据不足返回 SchemaGap；
4. `execution_gateway_node`：只允许通过 M2 的 ReadGateway；
5. `response_node`：根据 ResultSummary 和 result_id 输出回答与表格描述。

### 7.2 Graph 不变量

- Coverage 不是 `SUFFICIENT` 时禁止进入 GENERATE；
- SQL 未通过 Gateway 时不能产生成功 ResultObservation；
- GoalChecklist 未完成时不能直接 END；
- 达到 6 轮、2 次召回或 30 秒预算后必须结束或澄清；
- 连续相同 Action 和参数时终止循环；
- 空结果不能自动扩大时间或删除用户条件。

### 7.3 API 和最小前端

前端详细契约、视觉方向、组件边界、响应式、无障碍、SSE 和 CORS 以 [Spec 08](./specs/08-frontend-experience.md) 为准。

- `POST /api/chat`：创建或继续线程；
- SSE：输出当前 Node、Action 和最终回答；
- 对话列表：展示用户与系统消息；
- Trace 折叠区：展示 Node 名、耗时和状态，不展示隐藏推理；
- 结果表格：根据 result_id 分页读取。

### 7.4 完成标准

- 10 个单轮 Golden Case 从自然语言端到端得到正确 Result；
- Trace 中可以看见真实的 Node 循环和预算变化；
- 简单问题不发生无意义的二次召回；
- 任何 SQL 都通过 M2 Gateway。

## 8. M4：Schema RAG 与 SchemaGap 补检

### 8.1 离线索引

为 Source、Object、Field/Entity 和 Relation 分别建立索引文档，至少包含：

- 稳定 ID、名称、别名和描述；
- 数据源、业务域、粒度和 Owner；
- 字段类型、分类、枚举摘要和所属对象；
- PK/FK、关系基数和 Verified 状态；
- `catalog_version` 和权限过滤字段。

Milvus 保存向量和稳定 ID，权威元数据仍在 MySQL；在线返回后必须根据版本到 MySQL 校验。

### 8.2 在线检索

```text
PermissionContext 前置过滤
→ Source / Domain TopK
→ Object TopK
→ 候选 Object 内 Field / Entity TopK
→ Schema Graph 扩展 1～2 跳 Join
→ BM25 + Embedding + Reranker
→ CoverageEvaluator
→ ContextBudgeter
→ GroundedContext
```

### 8.3 SchemaGap

首次召回和补检必须复用同一个 `retrieval_node`：

- 首次输入：TaskFrame + PermissionContext；
- 补检输入：SchemaGap + candidate object_ids + existing_context_id；
- 补检不能扩大到全量数据源；
- 最多 2 次召回；
- 仍不充分时进入 ASK_USER，而不是猜字段。

### 8.4 合成元数据评测

- 生成 100 个 source、1000 张表和约 3 万字段；
- 加入近义表名、同名字段、无关业务域和越权对象；
- 不为合成表生成完整业务数据，只评测目录检索；
- 固定 50～100 个 Schema 查询，记录 Recall@K、Context Precision、Token 和延迟。

### 8.5 完成标准

- 正确 Object/Field 能进入配置的 TopK；
- 权限外对象不会出现在候选和 Trace 中；
- P95 GroundedContext 不超过既定 Token 预算；
- 人为删除首次召回字段后，第二轮能根据 SchemaGap 补齐；
- 禁用向量检索或 Reranker 后可产生对照数据。

## 9. M5：多轮记忆、Interrupt 和结果制品

### 9.1 三层状态

| 层级 | 本阶段实现 |
| --- | --- |
| Working State | AgentState 保存当前 TaskFrame、Coverage、Observation、GoalChecklist 和预算 |
| Short-term Memory | MySQL Checkpointer 保存 thread 状态、消息、滚动摘要和 Artifact 引用 |
| Long-term Memory | MySQL UserMemoryStore 按 Key 保存用户确认的稳定偏好 |

### 9.2 任务

- [ ] 每个 Graph super-step 后保存 Checkpoint；
- [ ] Interrupt 前强制保存可恢复状态；
- [ ] 使用乐观锁处理同一线程并发更新；
- [ ] 实现 ArtifactRepository 和 ReferenceResolver；
- [ ] 支持“刚才结果”“第一个字段”“再加退款率”；
- [ ] 根据 Token 阈值生成结构化滚动摘要；
- [ ] 实现 PromptContextBuilder，按 Node 投影最小 Prompt；
- [ ] 长期偏好只在用户明确设置或确认后写入；
- [ ] 同一 Key 版本化覆盖，不把每轮内容无限追加；
- [ ] 实现澄清型 `ASK_USER + Interrupt + resume`；
- [ ] 支持 React Data Table、CSV 和 ECharts DSL。

### 9.3 记忆测试

- 关闭进程后恢复未完成会话；
- HITL 前后不重复执行已完成步骤；
- “第一个字段”绑定到正确 Artifact；
- Artifact 版本过期时进入澄清；
- “这次只看华东”不会写为长期默认；
- “以后默认看店铺 A”确认后能跨线程召回；
- 权限变化后旧 Artifact 和默认店铺重新校验；
- 长对话压缩后仍保留未完成目标和明确条件。

### 9.4 完成标准

- 15 条多轮任务全部能得到可解释的指代结果；
- Checkpoint 中断恢复不丢状态、不重复副作用；
- 完整结果集不进入 Prompt，只传 result_id 和必要摘要；
- 长期偏好召回不会覆盖用户当前明确条件；
- 表格、CSV 和图表都引用真实 result_id。

## 10. M6：Admin 写入与审批

### 10.1 实现边界

只允许：

- Admin；
- 白名单表和字段；
- 参数化 `INSERT/UPDATE`；
- 主键或唯一键限定的小范围变更。

始终禁止：

- `DELETE`；
- DDL、复制表和重命名表；
- FILE、GRANT 和任意原始模型 DML。

### 10.2 流程

```text
MutationSpec
→ 权限、白名单、类型和唯一键检查
→ 读取 before 值
→ MutationPreview
→ Checkpoint + Interrupt
→ Admin 确认
→ 重新校验权限和数据版本
→ 后端生成参数化 SQL
→ 事务执行
→ Audit Log
→ MutationObservation
```

### 10.3 完成标准

- User 写入在进入审批前被拒绝；
- Admin 无权字段和禁止操作直接拒绝；
- Preview 能展示 before、after 和预计影响行数；
- 确认后权限或数据版本变化会使旧批准失效；
- 相同 Checkpoint 重放不会重复提交；
- 审计记录能还原操作者、请求、前后值和结果。

## 11. M7：评测、消融与面试收口

### 11.1 最终评测集

| 类型 | 建议数量 |
| --- | ---: |
| Schema 和指标解释 | 10 |
| 单次数据读取 | 20 |
| 多步骤读取与分析 | 15 |
| 多轮时间、实体和 Artifact 指代 | 15 |
| Admin 写入与 HITL | 10 |
| 越权、危险 SQL 和 Prompt Injection | 15 |
| 长尾表达与大规模 Schema 干扰 | 10 |

同一评测用例应保存：

- 用户问题和用户权限；
- Golden TaskFrame；
- 必须召回的 Object/Field/Join；
- Golden SQL 或 Golden Result；
- 预期 Action 序列；
- 是否应该澄清、拒绝或 HITL；
- 最大步骤、Token 和时延预算。

### 11.2 指标

- Task Completion Rate
- TaskFrame Accuracy
- Object/Field Recall@K
- Context Precision
- Schema Gap Recovery
- Result Accuracy
- Action Routing Accuracy
- Average Graph Steps
- Security Pass Rate
- HITL Resume Success
- Follow-up Resolution Accuracy
- Checkpoint Recovery Success
- Long-term Memory Precision
- P95 Latency
- Average Token Cost
- P95 GroundedContext Tokens

### 11.3 必做对照实验

至少比较以下版本：

1. 全量 Schema 注入 vs 分层最小 GroundedContext；
2. 只有 BM25 vs BM25 + Embedding + Reranker；
3. 禁用 SchemaGap 补检 vs 启用补检；
4. 全历史 Prompt vs 摘要 + 引用 + 按需投影；
5. 只评 SQL Execution Accuracy vs 任务完成率。

### 11.4 完成标准

- 评测脚本可重复运行并输出 JSON/CSV 报告；
- 每个简历数字都能定位到数据集版本、代码版本和计算方式；
- 至少保留 5～8 个失败案例及对应改进过程；
- 面试演示可在 2 分钟概览和 10 分钟深挖两种模式下完成；
- README 中“目标”指标只在验证后改为真实结果。

## 12. 每周建议安排

### 第 1 周：先把数据和安全地基做好

- 完成 M0；
- 完成 M1 数据库、指标目录和 20 条 Golden Case；
- 开始 M2 AST 与禁止项。

本周输出：可重建数据库、Golden Result、第一版安全测试。

### 第 2 周：打通可信的单轮问数

- 完成 M2 ReadGateway；
- 完成 M3 五 Node Graph 的最小版本；
- React 展示对话、Trace 和结果表。

本周输出：“昨天各品类 GMV”端到端 Demo，且没有 SQL 旁路。

### 第 3 周：完成真正的 Schema Linking

- Milvus + BM25 + Reranker；
- 分层检索和权限前置过滤；
- CoverageEvaluator、ContextBudgeter 和 SchemaGap；
- 小规模检索评测。

本周输出：“最近 15 天华东销售表格”出现一次真实补检后完成。

### 第 4 周：多轮和可恢复状态

- MySQL Checkpointer；
- Artifact、ReferenceResolver 和滚动摘要；
- PromptContextBuilder 与澄清 Interrupt；
- CSV、ECharts 和长期偏好。

本周输出：“字段列表 → 用第一个字段查重复值”跨轮完成，重启后可恢复。

### 第 5 周：扩大评测并完成前端体验

- 合成大规模元数据；
- 60～80 条任务评测；
- Trace、错误态、空结果和 HITL 前端；
- 修复主要失败案例。

本周输出：第一版离线评测报告和可稳定录屏的 Demo。

### 第 6 周：写入、完整评测和简历数据

- 有余力则完成 M6 WriteGateway；
- 扩展到 80～100 条完整评测；
- 完成消融和成本分析；
- 更新 README、演示页和简历数字。

本周输出：最终评测报告、2 分钟讲稿和可复现项目。

## 13. 开发过程中的全局完成定义

一个任务只有同时满足以下条件才算完成：

- 有明确输入输出的数据结构；
- 正常、异常、越权和边界路径均有测试；
- Trace 能解释该步骤为什么发生；
- 不新增绕过权限或 Gateway 的旁路；
- 大对象通过 ID 引用，不无界写入 State 或 Prompt；
- README 与实际行为一致；
- 未评测的能力和数字不能写成既成事实。

## 14. 风险与降级策略

| 风险 | 早期信号 | 降级或处理方式 |
| --- | --- | --- |
| 同时开发 Agent、RAG 和安全导致难以定位问题 | 单条查询需要同时调多个模块 | M3 先使用固定 RetrievalService，M4 再替换为真实 RAG |
| Milvus 运维占用过多时间 | 本地环境频繁启动失败 | 先用可替换内存/SQLite 索引验证接口，再切 Milvus；最终演示必须使用 Milvus |
| LLM SQL 不稳定 | 同义问题生成差异过大 | 强化 QuerySpec、指标目录和受控 GroundedContext，不用更多 Prompt 掩盖问题 |
| RLS AST 改写复杂 | 嵌套查询出现漏注入 | MVP 先限制支持的 SQL 形态并补测试，拒绝无法证明安全的语句 |
| 长期记忆污染查询条件 | 新会话自动带入错误地区 | 只存用户确认偏好，当前明确条件优先，注入前重新校验权限 |
| 写入功能拖慢主线 | 只读准确率尚未稳定 | 将 M6 降级为设计与 Preview 演示，先完成只读评测 |
| 指标看起来很好但不可复现 | 测试集和 Prompt 经常变化 | 固定数据、评测集、模型配置和 Git 版本，报告中记录全部版本 |

## 15. 开发启动清单

第一天只做以下事项，不立即接入 LLM：

1. 初始化后端、前端、测试和 Docker Compose；
2. 创建 MySQL reader/writer/migration 三类账号设计；
3. 定义第一版 Pydantic 核心模型；
4. 建立 8 张业务表和版本化语义目录的 migration；
5. 写 5 条最小 Golden Case；
6. 写 10 条 SQL 安全失败用例；
7. 确认一条人工 SQL 可以通过未来 ReadGateway 的接口返回 ResultObservation。

前三天的目标不是“让 Agent 聊起来”，而是让数据、口径、安全边界和测试基线先稳定下来。这样后续接入 LLM 时，才能判断问题来自模型、检索还是执行，而不是同时调试所有环节。
