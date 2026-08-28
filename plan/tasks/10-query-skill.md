# Task 10: 可信查询 Skill（LangGraph）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T5 T6 T7 T8 T9 · 交给：T13 T16 · 里程碑：M3

## Boundary

| | |
| --- | --- |
| **Owns** | 查询 Skill 图 Q01–Q11；`followup.py` 判断 DuckDB vs MySQL 重查；返回 `QuerySkillResult`。 |
| **In** | `skills/query/graph.py`、`coverage.py`、`followup.py`、`prompt/query_skeleton.yaml`、`llm/schemas.py`（`QuerySkeleton` 结构化输出）、`tests/test_query_skill.py`。 |
| **Out** | `interrupt()`、Coordinator 意图分类、写入、HTTP、前端、往 Catalog 加边。 |
| **Must not** | 调用 `interrupt()`（源码/AST 不得出现）；让 LLM 自创指标公式；Q10 修 `too_broad`/敏感列/fan-out/注入；在 Coordinator 里做 filter vs requery；把整表放进返回值。 |

**Files:**
- Create: `backend/app/skills/query/graph.py`
- Create: `backend/app/skills/query/coverage.py`
- Create: `backend/app/skills/query/followup.py`
- Create: `backend/app/prompt/query_skeleton.yaml`
- Create: `backend/app/llm/schemas.py`（`QuerySkeleton` 结构化输出）
- Create: `tests/test_query_skill.py`

**Interfaces:**
- Consumes: `QueryTask`, `RuntimeContext`
- Produces: `QuerySkillResult`（含 `hitl` 候选项时仍是普通返回值）
- **禁止** 在本图调用 `interrupt()`

节点：Q01 权限与口径 → Q02–Q07 RAG → Q08 LLM 骨架（只引 `metric_id`）→ Q09 编译为 `CompiledQuery` → Q10 网关（最多 2 次可修 `unsafe` 重生成骨架；`too_broad`/敏感列/fan-out/注入 **不修**）→ Q11 执行落盘。

召回前、执行前都 `reload_permissions()`。Skill 内部没有 HITL 暂停。

Follow-up（本模块独有职责）：`followup.py` 判断仅已有列 → DuckDB；否则合并上一轮 `QueryTask` 重进 Q01。时间是否重算见 Locked Decision（本 Skill 接收已经解析好的 `QueryTask.time_range`）。

Skill 私有 state 可含补检轮次、骨架修复次数；不把整表放进返回值。

- [ ] **Step 1:** 用假 LLM 固定输出骨架，断言最终 SQL 含审核公式且为参数化查询、网关拒绝时不执行 MySQL、follow-up 生成 `parent_result_id`、源码/AST 不含 `interrupt`。
