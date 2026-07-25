# Phase 5：错误修复、分流与记忆 — Design Spec

> 产品规格以 `docs/` 为准；本文只描述 Phase 5 如何落地。  
> 对齐：`docs/06-开发计划.md` Phase 5、`docs/03-Agent设计.md` §1 / §2.3 / §2.8 / §5、`docs/02-数据库设计.md` §0.3–0.5。  
> 承接：`spec/2026-07-25-phase4-tool-registry-sandbox-audit-design.md`（Tool Registry + Guardrail + Sandbox + AuditLog）。

## 1. 已确认决策

| 决策 | 选择 |
|------|------|
| 子图深度 | Coordinator = 固定节点链；ReAct = 真 LLM Tool-calling 循环（非贴标签短路径） |
| 整体拓扑 | 主图双分支 + **共享 SQL 尾环**（Guardrail → Executor → Repair×1） |
| ReAct 与执行 | ReAct **禁止**调用 `execute_sql`；执行只走共享尾环 |
| ReAct 工具集 | `query_schema` / `retrieve_metric_definition` / `validate_sql` + 轻量 `propose_sql`（只写 state） |
| ReAct 步数 | `MAX_REACT_STEPS=5` |
| Repair 挂载 | 两模式共用同一 Repair 环；仅沙箱执行失败可修；Guardrail 拒绝不进 Repair |
| Repair 次数 | 最多 1 次；`repaired` 标记 |
| 槽位合并 | Intent 后确定性浅合并（本轮非空覆盖，空则继承） |
| 长期记忆写回 | 规则合并 `preferences_json` + 模板摘要；无 LLM 摘要、无向量 |
| LLM 客户端 | 继续 `openai` SDK（`app.agent.llm`）；不上 LangChain `create_react_agent` |
| Chart | Phase 5 不做 ChartPlanner / 前端图表 |

## 2. 范围

### 做

- `SQLRepairer`（最多修 1 次；修后必须再过 Guardrail）
- `ComplexityRouter`：采纳 IntentAnalyzer 的 `route_mode`，规则可硬覆盖；`route_source` = `model` | `rule_override`
- 挂接 ReAct 子图与 Coordinator 子图（共用 Tool Registry / Guardrail / Sandbox）
- Session 槽位（`session_turns`）+ 长期记忆轻量形态：`preferences_json` + 最近摘要列表
- Memory 读/写挂在主图两端（两模式共用）
- 多轮追问与跨 Session 偏好/摘要复用
- pipeline / SSE：`route_decision` 改由 ComplexityRouter 发出；Repair 后可再推 `sql`（`repaired: true`）
- README 同步 Phase 5 行为（双模式、Repair、记忆形态）

### 不做

- ChartPlanner、前端图表、`chart` SSE（Phase 6）
- 向量库 / embedding / 实体图谱
- MCPToolProvider / 用户 OpenAPI Tool Manifest
- 将 Registry 换成 LangChain Tool 包装或 `create_react_agent`
- admin NL→写 SQL 生成器（生成仍以只读分析为主；admin 写能力仍仅 Guardrail/Sandbox 层）
- 澄清路径改写长期 preferences / 追加摘要

## 3. 主图拓扑

```text
START
  → MemoryLoad
  → IntentAnalyzer
  → SlotMerge
  → ClarificationChecker
  → ComplexityRouter          # 替换 Phase 3/4 的 RouteEmit
       ├─ need_clarification → ClarificationReply → MemorySave → END
       ├─ react              → ReActSubgraph → 共享尾环
       └─ coordinator        → SchemaRetriever → SQLGenerator → 共享尾环

共享尾环:
  SQLGuardrail
    ├─ error → MemorySave → END          # 权限/规则阻断，不 Repair
    └─ ok    → SQLExecutor
                 ├─ ok              → AnswerComposer → MemorySave → END
                 ├─ fail & !repaired → SQLRepairer → SQLGuardrail（再走）
                 └─ fail & repaired  → MemorySave → END   # error；repaired=true
```

约束：

1. ReAct 与 Coordinator **不是两套 SQL 执行实现**：最终执行只经 Registry `execute_sql` → Guardrail + Sandbox。
2. Memory 不在 Coordinator 内部专属；入口读、出口写。
3. 删除默认路径对 `RouteEmit` 的依赖（可删文件或改为 thin deprecated 包装，主图不再引用）。

## 4. AgentState 增量

在 Phase 4 `AgentState` 上增加：

```python
# 记忆
session_slots: dict | None          # MemoryLoad：上一轮槽位（业务层）
user_preferences: dict | None       # preferences_json 解析结果
recent_summaries: list[dict] | None # 最近摘要（最多 5 条注入）

# ReAct
react_messages: list[dict] | None   # OpenAI messages 历史
react_step: int                     # 当前步数
# generated_sql 由 propose_sql / SQLGenerator / SQLRepairer 写入

# 已有
route_source: str | None            # "model" | "rule_override"
repaired: bool
```

`route_mode` 最终值以 ComplexityRouter 输出为准（可能被规则覆盖）。

## 5. ComplexityRouter

确定性节点，不调模型。

### 5.1 输入

- IntentAnalyzer 建议的 `route_mode`
- SlotMerge 后的 `slots`
- 原始 `question`（关键词）

### 5.2 规则（高置信硬覆盖）

| 条件 | 结果 `route_mode` |
|------|-------------------|
| 单指标 +（有 time_range 或可继承）+ 无对比/归因多信号 +（有 top_n 或单 group_by 或纯聚合） | `react` |
| `len(metrics) >= 2`，或命中对比/同比/环比/归因/并且/以及 等，或跨域多指标信号 | `coordinator` |
| 模型建议与规则冲突 | **以规则为准**，`route_source=rule_override` |
| 无规则命中 | 保留模型建议，`route_source=model` |
| 模型值非法/空 | 默认 `react`；若同时命中复杂关键词则 `coordinator` |

实现落在纯函数 `decide_route(question, slots, model_route) -> (route_mode, route_source)`，节点只写 state。

### 5.3 SSE

`ComplexityRouter` 节点结束后，pipeline 推送：

```json
{
  "route_mode": "react",
  "route_source": "rule_override"
}
```

（事件名仍为 `route_decision`，与 `docs/04` 一致。）

## 6. ReAct 子图

### 6.1 循环

```text
ReActAgent (LLM + tools) ↔ ReActToolNode (registry.invoke / propose_sql)
  退出 → 进入共享尾环（须已有 generated_sql，否则 error）
```

### 6.2 工具

| 名称 | 来源 | 说明 |
|------|------|------|
| `query_schema` | Registry | 只读 Schema |
| `retrieve_metric_definition` | Registry | 指标口径 |
| `validate_sql` | Registry | Guardrail 封装；**不执行** |
| `propose_sql` | 子图本地 / 可注册为 low-risk 内部 tool | 校验非空 SQL 字符串，写入 `generated_sql`；不连库 |

**禁止**在 ReAct 循环内暴露或调用：`execute_sql`、`render_chart`。

### 6.3 退出与上限

- `MAX_REACT_STEPS=5`
- 优先退出：`generated_sql` 非空（建议在 `propose_sql` 前或后调用过 `validate_sql`，但不强制 validate ok 才能退出——最终仍过共享 Guardrail）
- 步数耗尽仍无 SQL → 写 `error`，跳过尾环执行，走 MemorySave → END
- LLM/工具异常 → 简化 `error`，无堆栈

### 6.4 实现约束

- 继续 `openai` SDK function/tools calling；Tool JSON schema 从 Registry `ToolSpec` + `propose_sql` 元数据生成
- 实际副作用只经 `registry.invoke`（`propose_sql` 除外，仅写 state）
- `tool_events` 仍由 Registry 产生，pipeline 映射 `tool_*` SSE + AuditLog

## 7. Coordinator 子图

Phase 5 保持现有固定链：

```text
SchemaRetriever → SQLGenerator →（共享尾环）
```

不在本阶段加入 Chart/Insight 专用步（AnswerComposer 仍在尾环成功后）。

## 8. 共享尾环 + SQLRepairer

### 8.1 边条件

| 来源 | 条件 | 下一跳 |
|------|------|--------|
| SQLGuardrail | `error` 有值 | MemorySave → END |
| SQLGuardrail | 通过 | SQLExecutor |
| SQLExecutor | 成功 | AnswerComposer |
| SQLExecutor | 失败且 `repaired` 为假 | SQLRepairer |
| SQLExecutor | 失败且 `repaired` 为真 | MemorySave → END |
| SQLRepairer | 产出新 SQL | SQLGuardrail（强制再校验） |

### 8.2 SQLRepairer 行为

- 进入时：`repaired=true`；保留原 `error` 文案供 prompt
- 输入：`question`、当前 `generated_sql`、`error`、相关 Schema（`relevant_tables` / `relevant_columns` / `metric_specs`；ReAct 路径若缺 Schema，Repairer 内可用 `query_schema` 或已有 state 补齐，**不得**旁路执行 SQL）
- 输出：新 `generated_sql`；清空 `error`（若模型失败则保留/写入清晰错误且不再循环）
- 修复后必须再过 Guardrail；若 Guardrail 拒绝 → END（不再二次 Repair）

### 8.3 验收：至少 3 类可自动修复错误

测试可构造（mock LLM 返回修好的 SQL）：

1. 未知列名 / 列名拼写错误  
2. 聚合查询缺 GROUP BY / 聚合误用  
3. 表名错误或 JOIN 缺表（在相关 Schema 范围内）

每类断言：Repair → 再 Guardrail → 再执行成功（或至少再过 Guardrail）；`repaired=true`；SSE `sql.repaired=true`。

## 9. Memory

### 9.1 MemoryLoad

1. 确认 `chat_sessions` 中 `session_id` 归属 `user_id`（否则 error / 拒绝）
2. 读该 session 最近 1 轮 `session_turns` → `session_slots`
3. 读 `user_preferences` → `user_preferences`
4. 读该用户最近 5 条 `user_analysis_summaries` → `recent_summaries`
5. Intent prompt 可注入短上下文：上一轮槽位摘要、偏好 `default_time_range`、最近摘要一行标题（**不含**全库 Schema、不含敏感明文）

### 9.2 SlotMerge

纯函数 `merge_slots(prev, curr, preferences) -> slots`：

- 本轮非空标量/非空 list/非空 dict → 覆盖  
- 本轮 `null` / 缺省 / 空 list → 继承 prev  
- 若无 prev 且 `time_range` 仍空：可用 `preferences.default_time_range` 填充（减少无谓澄清）
- ClarificationChecker / Router / 下游均使用 **merged** `slots`

### 9.3 MemorySave

| 结束原因 | `session_turns` | `preferences_json` | `user_analysis_summaries` |
|----------|-----------------|--------------------|---------------------------|
| 澄清 | 写 turn（无 sql；可记澄清问句到 result_summary） | 不改 | 不追加 |
| 分析成功 | 写 turn（slots + sql + 短结果摘要） | 浅合并更新 | 追加 1 条模板摘要 |
| 最终失败（Guardrail/执行/ReAct 无 SQL） | 写 turn（含错误摘要） | 不改 | 不追加 |

上限与隔离：

- 每 session 保留最近 **N=10** 轮（删最旧）
- 每用户摘要保留最近 **M=20** 条
- 读写按 `session_id` + `user_id` 隔离；摘要/偏好 **禁止**写入姓名/手机/邮箱/身份证等敏感明文
- preferences 建议合并键：`default_time_range`、`preferred_dimensions`（来自 group_by）；不写角色

`session_turns` 字段与 `docs/02` 对齐：`metrics_json` / `time_range_json` / `filters_json` / `group_by_json` / `sql_text` / `result_summary` 等。

### 9.4 追问验收例

1. Session A：「最近 30 天各渠道 GMV」→ 成功  
2. 同 session：「那按城市拆一下」→ 继承 metrics=`gmv`、time_range=`last_30d`，group_by 变为含 `city`  
3. 新 Session B（同用户）：MemoryLoad 可读到 preferences / 最近摘要（不要求自动注入为强制过滤，但 state 与 Intent 上下文可见）

## 10. 目录结构（相对 Phase 4 增量）

```text
backend/app/agent/
├── memory/
│   ├── __init__.py
│   ├── store.py             # session_turns / preferences / summaries CRUD + 淘汰
│   ├── merge.py             # merge_slots
│   └── summarize.py         # 模板摘要、偏好浅合并、敏感剥离
├── nodes/
│   ├── memory_load.py
│   ├── slot_merge.py
│   ├── complexity_router.py # 替换 route_emit 在主图中的位置
│   ├── sql_repairer.py
│   ├── memory_save.py
│   ├── react_agent.py
│   └── react_tools.py       # propose_sql + tool 调用适配
├── react_subgraph.py
├── graph.py                 # 重接主图
└── pipeline.py              # RouteEmit → ComplexityRouter；Repair sql 事件

backend/tests/
├── test_complexity_router.py
├── test_sql_repairer.py
├── test_slot_merge.py
├── test_memory_store.py
├── test_react_subgraph.py
└── test_phase5_pipeline.py
```

说明：

- Guardrail / Sandbox / Registry **不**搬进 memory 或 ReAct 私有实现
- `route_emit.py` 可删除或保留为测试兼容薄封装；主图与 pipeline 只认 `ComplexityRouter`

## 11. pipeline / SSE 增量

- `route_decision`：在 `ComplexityRouter` 的 `node_end` 后推送（不再依赖 `RouteEmit`）
- `sql`：`SQLGenerator` / ReAct `propose_sql` 成功后可推一次；`SQLRepairer` 成功后再推，`repaired: true`
- `tool_*`：ReAct 内 Registry 调用与 Phase 4 相同
- `done`：不变；失败路径仍有 `error` + `done`

## 12. 测试策略（TDD）

优先失败测试再实现：

1. **ComplexityRouter**：单指标 TopN → react + rule_override 或 model；多指标/对比词 → coordinator + rule_override；`route_source` 区分  
2. **merge_slots**：空覆盖继承；非空覆盖；偏好默认 time_range  
3. **memory store**：隔离、N/M 上限淘汰、成功写 preferences/摘要、澄清不写长期记忆  
4. **SQLRepairer**：3 类错误 mock 修复；修后必经 Guardrail；Guardrail 拒绝不进 Repair  
5. **ReAct**：工具列表不含 `execute_sql`；`propose_sql` 写入 `generated_sql`；步数上限  
6. **集成**：同 session 追问继承；跨 session 读到 preferences 或摘要；react/coordinator 分流可观测

Python：仅 `/home/user/miniconda3/envs/python3.12`；配置仅 `config.yaml` / 测试 `APP_CONFIG`。

## 13. 验收对照（Phase 5）

| 验收项 | 设计落点 |
|--------|----------|
| 至少 3 类 SQL 错误能自动修复 | §8.3 + tests |
| 简单走 ReAct，复杂走 Coordinator；`route_source` 可区分 | §5 + §6 + §7 |
| 同 session 追问继承槽位；跨 session 读偏好/摘要 | §9 |
| 「那按城市拆一下」继承上下文 | §9.2 / §9.4 |
| 修复后 SQL 再过 Guardrail | §8.1–8.2 |
| Memory 按 session/user 隔离；长期记忆仅为偏好 JSON + 摘要列表 | §9 |

## 14. 全局约束（继承）

- 只在本仓库 `main` 工作区改代码；不用 git worktree 做功能开发  
- Agent 不自动 `git commit` / `git push`（除非用户当次明确要求）  
- SQL 默认可演示路径必须经 Guardrail → Sandbox；禁止旁路直连  
- 能确定就不调模型：Router、SlotMerge、Memory 规则写回、Guardrail  
- 一次只改 Phase 5 相关文件；不做顺手大重构  
