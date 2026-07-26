# Design: 登录亮点 · 多轮工作台 · 数据表浏览

日期：2026-07-26  
状态：待用户审阅  
范围：前端 UX + 必要的只读 Session / Tables API；不改 Agent 主链路推理逻辑。

## 1. 背景与目标

当前 `/` 营销登录页 AI 叙事偏弱；`/app` 为单轮覆盖式结果区，追问会清空上一轮 SQL / 结果 / 图表 / Trace；侧栏仅展示表名与行数，无法浏览全表数据。

目标（已与用户确认）：

1. 强化登录页 AI 亮点（首屏卖点 + 下方能力故事）。
2. `/app` 改为豆包式多轮时间线，且每轮仍可展示回答 / SQL / 查询结果 / 图表 / AGENT TRACE（折叠控制密度）。
3. 支持多会话列表（新建 / 切换）；历史恢复用现有 `session_turns` 可用字段（非完整回放）。
4. 新增数据表浏览页：业务表概览 → 点表看结构 + 分页行；`/app` 不再堆完整表信息。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 多轮排版 | 时间线展开；每轮卡片内 chips 折叠 SQL / 结果 / 图表 / Trace |
| 工作台骨架 | 左会话侧栏 + 右时间线，输入框沉底 |
| 会话历史恢复 | 用 `session_turns` 恢复问题 / SQL / result_summary 等；完整 rows / chart / Trace 仅当次前端保留 |
| 多会话 | 侧栏列表，可新建 / 切换 |
| 登录页 | 首屏 4 卖点 + ticker；下方「理解 → 执行 → 交付」故事 |
| 数据表页 | 概览全部业务表 → 点进结构 + 分页行；固定每页 50 条 |

## 3. 架构总览

```text
/ (LoginPage)
  营销首屏 + 能力故事 + 登录/注册（既有鉴权）

/app (AppWorkbench)
  Sidebar: 用户 · 会话列表 · 示例 · 「查看全部数据表」· 退出
  Main: turns[] 时间线 + 底部输入
  POST /api/chat (既有 SSE，session_id = 当前会话)

/app/tables (TablesPage)
  GET /api/tables → 概览
  GET /api/tables/{name}?page=&page_size=50 → 结构 + 行
```

分层不变：前端只消费 API；Session / Tables 为确定性代码路径，不调模型；权限与敏感列规则复用现有 Guardrail / schema 逻辑。

## 4. 前端设计

### 4.1 登录页 `/`

保留现有品牌色、字体与表单交互（登录 / 注册 / demo 账号 / admin 邀请码）。增强内容：

**首屏（第一视口）**

- 品牌名 `data-analysis-agent` 仍为主信号
- 一句价值主张（NL → 安全 SQL → 结论 / 表 / 图）
- 四宫格 AI 卖点：NL→安全 SQL；双路径编排（ReAct/Coordinator）；SSE Trace；多轮记忆
- 保留 / 强化 QueryTicker（问句 → SQL → Guardrail 通过）

**下方能力故事（可滚动）**

1. 理解：意图 + 槽位 / 澄清  
2. 执行：Schema Linking → 权限 → 沙箱 → 可修复  
3. 交付：结论 · 表 · 图 · Trace；多轮追问  

不做仪表盘堆砌；不引入第二套视觉皮肤。

### 4.2 工作台 `/app`

**左侧**

- 项目名 + 说明 + 用户（username / role）
- 会话区：`+ 新建`；列表项显示 title（缺省时用首问截断或「新会话」）与更新时间
- 示例问题列表（点击填入底部输入框，不自动发送）
- 按钮「查看全部数据表」→ `/app/tables`
- 退出登录
- **移除**侧栏完整数据表名+行数列表（改由数据表页承载）

**右侧时间线**

- `turns: TurnView[]`，每轮包含：question、answer、sql、sqlRepaired、guardrailPassed、columns/rows、chart、writeResult、trace、error、clarification、latency、streaming 标记，以及可选 `fromHistory`（来自 session_turns 恢复）
- 用户气泡右对齐；Agent 卡片左对齐，内含：
  - 分析结论（默认展开；流式追加）
  - chips：`SQL` / `查询结果` / `图表` / `AGENT TRACE`（各自独立展开/收起）
  - 写操作成功提示、澄清问题、错误提示仍在该轮卡片内
- 当前流式轮：默认展开结论 + SQL；其余默认折叠
- 历史恢复轮：展开结论（或 result_summary 作为结论占位）；无 rows/chart/完整 trace 时对应 chip 禁用或不渲染
- 底部固定输入区：多行文本 +「分析」按钮；提交后 append 新 turn，**不清空**既有 turns
- 同一会话继续使用 `POST /api/chat`，`session_id` 为当前选中会话 id

**会话切换**

- 新建：`POST /api/sessions` → 设为当前 → 清空 turns
- 切换：设当前 session → `GET /api/sessions/{id}/turns` 映射为 turns（摘要级）；不跨会话缓存富数据（切走即丢）。刷新后一律以服务端 turns 为准。

### 4.3 数据表页 `/app/tables`

- 需登录（与 `/app` 同鉴权守卫）
- 顶部：标题 +「返回工作台」
- 默认：业务表卡片/列表（name、column_count、row_count）
- 选中表：上方可折叠字段说明；下方表格；分页控件（上一页 / 下一页 / 页码）；`page_size` 固定 50
- 仅业务表；敏感列对 analyst 不可见（与 `/api/schema` 一致）
- 只读，无编辑

## 5. 后端 API

均需 `Authorization: Bearer <JWT>`。

### 5.1 Sessions

**`GET /api/sessions`**

```json
{
  "sessions": [
    {
      "id": "sess_xxx",
      "title": "最近 30 天 GMV 趋势",
      "updated_at": "2026-07-26 11:00:00",
      "turn_count": 2
    }
  ]
}
```

仅返回当前用户的会话，按 `updated_at` 降序。

**`POST /api/sessions`**

```json
{ "id": "sess_xxx", "title": null, "updated_at": "...", "turn_count": 0 }
```

服务端生成唯一 `session_id`（如 `sess_<uuid>`），写入 `chat_sessions`。

**`GET /api/sessions/{session_id}/turns`**

```json
{
  "session_id": "sess_xxx",
  "turns": [
    {
      "turn_index": 1,
      "question": "...",
      "intent": "...",
      "sql_text": "...",
      "result_summary": "...",
      "metrics": [],
      "time_range": null,
      "filters": {},
      "group_by": [],
      "created_at": "..."
    }
  ]
}
```

- 校验归属；不匹配 → 404/403  
- 返回最近 N 轮（与 Memory 一致，默认 10），按 `turn_index` 升序  
- **不**返回完整 rows / chart / node Trace

**标题回写**

- 首轮成功写入 `session_turns` 时，若 `chat_sessions.title` 为空，用问题截断（建议 ≤40 字）更新 title，并刷新 `updated_at`（可在现有 Memory 写回路径完成，避免新旁路）。

**与现有 chat 兼容**

- `POST /api/chat` 仍接受客户端 `session_id`；归属校验逻辑不变  
- 工作台默认不再使用固定 `default-<user_id>` 作为唯一会话；改为列表中的当前会话。若用户无会话，进入 `/app` 时自动 `POST /api/sessions` 建一个。

### 5.2 Tables

**`GET /api/tables`**

```json
{
  "tables": [
    { "name": "orders", "column_count": 9, "row_count": 1280 }
  ]
}
```

仅 `BUSINESS_TABLES`；`column_count` 对 analyst 排除敏感列。

**`GET /api/tables/{name}?page=1&page_size=50`**

```json
{
  "name": "orders",
  "columns": [{ "name": "id", "type": "INTEGER", "nullable": false }],
  "page": 1,
  "page_size": 50,
  "total_rows": 1280,
  "rows": [{ "id": 1, "order_date": "..." }]
}
```

规则：

- `name` 必须在业务表白名单；否则 404  
- `page_size` 固定为 50：客户端传入其它值时服务端仍按 50 执行并在响应中返回 `page_size: 50`  
- SELECT 白名单列 + `LIMIT/OFFSET`；禁止应用表  
- analyst：不返回敏感列数据与元数据  
- 不走 LLM / Tool Registry；本接口不做 AuditLog（避免与 Tool 审计语义混淆）

## 6. 前端状态与组件（建议拆分）

| 单元 | 职责 |
|------|------|
| `LoginPage` | 首屏卖点 + 能力故事 + 既有表单 |
| `AppWorkbench` | 会话列表状态、当前 session、turns、SSE 编排 |
| `TurnCard` | 单轮展示与 chips 折叠 |
| `SessionSidebar` | 会话列表 / 新建 / 示例 / 跳转数据表 |
| `TablesPage` | 概览 + 表详情分页 |
| `api/sessions.ts` / `api/tables.ts` | 薄封装 |

路由：在 `App.tsx` 增加受保护 `/app/tables`。

## 7. 文档同步（实现时）

更新 `docs/04-接口与前端.md`：

- 路由增加 `/app/tables`
- 工作台改为多轮时间线 + 会话列表；侧栏数据表列表改为入口按钮
- 登录页补充 AI 卖点 / 能力故事要求
- 新增 Sessions / Tables API 小节

不改动 Agent 编排文档，除非 Memory 写回 title 行为需在 `docs/03` 一句带过。

## 8. 验收标准

1. `/` 第一视口可见品牌 + 至少 4 条 AI 卖点 + 登录表单；向下滚动可见三段能力故事。  
2. `/app` 连续追问不删除上一轮卡片；每轮可独立展开/折叠 SQL、结果、图表、Trace。  
3. 可新建 / 切换会话；切换后时间线对应该会话；追问仍带正确 `session_id`。  
4. 刷新后可从服务端恢复会话列表与 turns；`display`（或对旧轮 SQL 重执行补齐）应恢复 rows/chart/Trace 供前端完整展示。  
5. `/app` 无完整表数据浏览；「查看全部数据表」进入 `/app/tables`。  
6. `/app/tables` 可看全部业务表概览；点表后以 50 条/页浏览；analyst 看不到敏感列。  
7. 既有 SSE 事件与权限行为不回退；示例问题仍可填入输入框。

## 9. 非目标

- 无限行数的全量结果落库（`display.rows` 上限 100，与沙箱 LIMIT 对齐）  
- 会话重命名 / 删除 / 搜索（可后补）  
- 数据表编辑、导出、自定义 page_size  
- 登录页暗黑主题或全新品牌体系重做  

## 10. 测试要点（TDD 优先 API）

- Sessions：归属隔离、新建、列表排序、turns 顺序与 N 上限、title 首问回写  
- Tables：非业务表 404、page_size=50、analyst 敏感列剥离、分页边界  
- 前端：多轮 append 不覆盖；切换会话加载 turns；登录页关键文案/区块存在（轻量断言即可）
