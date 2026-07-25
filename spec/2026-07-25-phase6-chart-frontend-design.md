# Phase 6：图表与前端完善 — Design Spec

> 产品规格以 `docs/` 为准；本文只描述 Phase 6 如何落地。  
> 对齐：`docs/06-开发计划.md` Phase 6、`docs/03-Agent设计.md` §2.9 / §3.2 `render_chart`、`docs/04-接口与前端.md` SSE / 工作台、`docs/01-需求总览.md` Recharts。  
> 承接：`spec/2026-07-25-phase5-repair-router-memory-design.md`（共享尾环 / Memory / Repair；明确 Chart 留 Phase 6）。

## 1. 已确认决策

| 决策 | 选择 |
|------|------|
| 图表规划算法 | **轻量 LLM** 产出 `type/x/y/title`；失败走确定性启发式，再不行 `table` |
| 挂载方式 | 主图固定节点 `ChartPlanner`；`render_chart` Tool **复用同一** `plan_chart` 逻辑 |
| 节点 vs Registry | 主路径节点**直调** `plan_chart`（不经 Registry）；显式 Tool 调用才有 `tool_*` / AuditLog |
| 触发条件 | 仅成功 SELECT 且 `rows` 非空才规划；写操作 / 空结果 / 执行失败 → `chart=None`，不调 LLM |
| LLM 失败降级 | 校验失败或异常 → 启发式 → `table`；**不**把 ChartPlanner 失败写成 pipeline `error` |
| 前端打磨 | **增量**：保留现有视觉语言；补 Recharts、Trace 可读性、角色徽章、写操作提示、图表区；登录页小幅加强品牌层次 |
| 图表库 | Recharts（与 `docs/01` 一致） |
| 偏好覆盖 | Phase 6 **不**用 `preferences_json.preferred_chart_types` 覆盖模型 |

## 2. 范围

### 做

- 实现 `chart_planner.plan_chart` + `ChartPlanner` 节点
- 共享尾环：`SQLExecutor ok → ChartPlanner → AnswerComposer`
- `render_chart` Tool 改为调用共享逻辑
- SSE：`chart`；写成功 `write_result`；`done` 携带 `repaired`（若尚未有则补）
- `AgentState`：`chart` / `is_write` / `affected_rows`；Executor 写路径写入后两者
- 前端：折线 / 柱状 / 饼图；Trace 优化；侧栏角色只读展示；admin 写操作明确提示
- 登录页 / 工作台增量打磨
- 最小必要同步 `docs/03`、`docs/04`、README（Chart / SSE / 写结果 UI）

### 不做

- 多 Y 轴、双图、下钻、自定义主题编辑器
- 用偏好 JSON 强制图表类型
- 登录页 / 工作台整页视觉重做
- ChartPlanner 经 Registry 调用（方案 2）
- MCP / 评测集 / eval 脚本（Phase 7）
- admin NL→写 SQL 生成器（仍仅 Guardrail/Sandbox 层支持写）

## 3. 主图拓扑

```text
START
  → MemoryLoad
  → IntentAnalyzer
  → SlotMerge
  → ClarificationChecker
  → ComplexityRouter
       ├─ need_clarification → ClarificationReply → MemorySave → END
       ├─ react              → ReActSubgraph → 共享尾环
       └─ coordinator        → SchemaRetriever → SQLGenerator → 共享尾环

共享尾环:
  SQLGuardrail
    ├─ error → MemorySave → END
    └─ ok    → SQLExecutor
                 ├─ ok              → ChartPlanner → AnswerComposer → MemorySave → END
                 ├─ fail & !repaired → SQLRepairer → SQLGuardrail
                 └─ fail & repaired  → MemorySave → END
```

约束：

1. ChartPlanner 在两模式共享尾环上，**不**做成 Coordinator 专属步骤。
2. 写操作仍走 `ok → ChartPlanner`；节点内短路跳过 LLM，`chart=None`，再进 AnswerComposer（写成功结论由 AnswerComposer 负责）。
3. 澄清路径不经过 ChartPlanner。

## 4. AgentState 增量

```python
chart: dict | None              # {type, x, y, title} 或 None
is_write: bool                  # SQLExecutor 写入；默认 False
affected_rows: int | None       # 写成功时为非负整数；读路径 None
```

`SQLExecutor` 行为调整：

- 读成功：`columns` / `rows` / `is_write=False` / `affected_rows=None` / `error=None`
- 写成功：`columns=[]` / `rows=[]` / `is_write=True` / `affected_rows=<n>` / `error=None`
- 失败：保持现有 `error`；不伪造 chart

## 5. ChartPlanner

### 5.1 共享模块 `app/agent/chart_planner.py`

```python
def plan_chart(
    question: str,
    columns: list[str],
    rows: list[dict],
    *,
    slots: dict | None = None,
    title_hint: str = "",
) -> dict | None:
    ...
```

返回：

```json
{
  "type": "bar",
  "x": "channel",
  "y": "gmv",
  "title": "上月各渠道 GMV Top5"
}
```

或数据不足时 `None`（空 `columns` / 空 `rows`）。

**流程：**

1. 资格检查：无列或无行 → `None`
2. 若 `question` 非空：LLM `chat_completion`，`temperature=0`；输入含问题、列名、最多 **12** 行样例、可选 slots 摘要、`title_hint`；要求只输出上述 JSON。若 `question` 为空：跳过本步，直接启发式
3. 校验：
   - 可解析 JSON
   - `type ∈ {line, bar, pie, table}`
   - `x` / `y` ∈ `columns`（`table` 时 x/y 可为空串或首两列）
   - `line|bar|pie`：`y` 列在样例中至少一半可数值化
4. 失败 / 异常 → `_heuristic_chart(...)`；再不行 → `type=table`
5. **禁止**因规划失败设置 pipeline `error`

### 5.2 启发式（确定性）

优先级（高→低）：

1. 存在时间/日期语义列（列名含 `date`/`time`/`日`/`天` 或样例可解析为日期）且另有数值列 → `line`（x=时间列，y=第一数值列）
2. 列名或问题暗示占比（`rate`/`ratio`/`占比`/`比例`/`份额`）或单度量多类别 → `pie`（类别列 + 数值列；类别数 ≤ 12 更佳，过多则改 `bar`）
3. 类别列 + 数值列 → `bar`
4. 否则 → `table`

### 5.3 节点 `nodes/chart_planner.py`

```python
def chart_planner_node(state: AgentState) -> dict:
    if state.get("error") or state.get("is_write"):
        return {"chart": None}
    columns = state.get("columns") or []
    rows = state.get("rows") or []
    if not columns or not rows:
        return {"chart": None}
    chart = plan_chart(
        state.get("question") or "",
        columns,
        rows,
        slots=state.get("slots"),
    )
    return {"chart": chart}
```

`pipeline` `_summarize`：`skipped`（chart is None）或 `chart["type"]`。

### 5.4 `render_chart` Tool

- 入参：`columns`、`rows`、可选 `question`、`title`
- 调用 `plan_chart`；`question` 为空时 **跳过 LLM**，直接启发式（Tool 单测不依赖 API key）
- 无数据时返回 `{type:"table", x:"", y:"", title: title or ""}` 且 `ok=True`（兼容现有 Tool 测试）
- `risk_level=low`，`permission_policy=allow`（不变）

## 6. SSE

| event | 时机 | data |
|-------|------|------|
| `chart` | ChartPlanner 结束后 `merged.chart` 非空 | `{type,x,y,title}` |
| `rows` | SQLExecutor 读成功（现状） | `{columns,rows}` |
| `write_result` | SQLExecutor 写成功 | `{affected_rows, sql}` |
| `done` | 结束 | 现有字段 + `repaired: bool` |

说明：

- `chart.type === "table"` 仍推送 `chart` 事件；前端选择不绘图即可
- 写路径不推 `chart`（节点 `chart=None`）
- 主路径 ChartPlanner **不**产生 `tool_start`/`tool_end`（除非未来有人显式调 Tool）

## 7. 前端

### 7.1 依赖

- `frontend/package.json` 增加 `recharts`

### 7.2 组件

- `frontend/src/components/ResultChart.tsx`：按 `chart.type` 渲染 `LineChart` / `BarChart` / `PieChart`；数据来自当前 `rows`，用 `x`/`y` 映射
- `table` 或 `chart==null`：组件返回 `null`
- 工作台结果区顺序：回答 → SQL（含校验/修复徽章）→ **写操作横幅** → 表格 → **图表** → Trace

### 7.3 写操作 UI

- 收到 `write_result`：展示明确成功提示，文案含「写操作」与 `affected_rows`
- 写失败：沿用现有 `error` 事件展示
- Trace 中对高风险 `tool_end`（`risk_level=high` 或 status 含 write）做视觉标记

### 7.4 Trace

- 保留事件流；增强可读 summary（节点中文/短标签可选，但不强制改事件名）
- 流式开始自动展开；结束后用户可折叠
- 展示 `route_decision`、`chart`、`write_result` 条目

### 7.5 侧栏与登录页

- 侧栏：username + **只读 role 徽章**（不可客户端切换）— 已有展示则强化样式即可
- 登录页：小幅加强品牌层次（字号 / 氛围），不重做信息架构

## 8. 目录与文件

```text
backend/app/agent/
├── chart_planner.py                 # 新建：plan_chart + heuristic + validate
├── state.py                         # + chart / is_write / affected_rows
├── graph.py                         # 挂 ChartPlanner
├── pipeline.py                      # chart / write_result / done.repaired
└── nodes/
    ├── chart_planner.py             # 新建
    └── sql_executor_node.py         # 写结果字段

backend/app/tools/builtins.py        # render_chart → plan_chart

backend/tests/
├── test_chart_planner.py            # 新建
├── test_graph_compile.py            # 图含 ChartPlanner
└── test_phase5_pipeline.py 或 phase6  # chart / write_result SSE

frontend/
├── package.json                     # + recharts
├── src/components/ResultChart.tsx   # 新建
└── src/pages/AppWorkbench.tsx       # chart / write / trace
└── src/pages/LoginPage.tsx          # 增量

docs/03-Agent设计.md                 # ChartPlanner / 尾环顺序（若与现文不一致则改）
docs/04-接口与前端.md                 # write_result / chart 说明
README.md                            # Demo：图表与写操作提示
```

## 9. 测试计划

| 用例 | 期望 |
|------|------|
| LLM 返回合法 bar JSON | `plan_chart` 原样（经校验） |
| LLM 非法 type / 错列名 | 启发式结果，非抛错 |
| LLM `chat_completion` 抛错 | 启发式，非 pipeline error |
| 空 rows | `None` |
| 日期列+数值 | 启发式 → `line` |
| 类别+数值 TopN | 启发式 → `bar` |
| 占比列 | 启发式 → `pie`（或类别过多时 bar） |
| 节点 `is_write=True` | `chart=None`，不调 LLM |
| pipeline 读成功 | 事件含 `chart`（mock LLM） |
| pipeline 写成功 | 事件含 `write_result`，无 `chart` |
| `render_chart` Tool | `ok` 且 type ∈ 四类 |
| 前端 ResultChart | 三种图型挂载；table/null 不渲染 |

测试中 mock `chat_completion`，不依赖真实 API key。

## 10. 验收对照（Phase 6）

| 验收项 | 落地 |
|--------|------|
| 趋势问题展示折线图 | ChartPlanner → `line` + Recharts LineChart |
| TopN 展示柱状图 | → `bar` + BarChart |
| 占比展示饼图 | → `pie` + PieChart |
| 登录页营销视觉 | 保留并小幅加强 |
| 工作台可 Demo | 图 / 表 / SQL / Trace / 角色 |
| admin 写操作 UI 明确提示 | `write_result` + 横幅 |
| 侧栏角色只读 | 徽章展示，无切换控件 |

## 11. 实现顺序建议

1. `chart_planner` 纯函数 + 测试（含启发式与 mock LLM）
2. state / Executor 写字段 / ChartPlanner 节点 / graph 边
3. pipeline SSE（`chart` / `write_result` / `done.repaired`）
4. `render_chart` 复用
5. 前端 Recharts + 写横幅 + Trace
6. 登录页小幅打磨
7. docs / README 最小同步
