# Design: 会话删除 · LLM 标题 · 示例问题空态

日期：2026-07-26  
状态：待用户审阅  
范围：工作台会话 UX + Session DELETE + Memory 首轮标题生成 + SSE `session_title`；不改 Agent 主链路推理 / Guardrail / 沙箱。

## 1. 背景与目标

当前工作台侧栏有会话列表与示例问题，但：

- 会话不可删除
- 标题在首轮用「首问截断 ≤40 字」写入，缺省 UI 显示「新会话」不够统一
- 示例问题挤在侧栏，空会话主区引导弱

目标（已与用户确认）：

1. 侧栏会话支持删除（确认后硬删除）。
2. 新建会话 UI 标题为「新会话」；首轮对话任意完成后，用 LLM 生成 ≤10 字摘要作为 title。
3. 示例问题移到主对话区空态；点击仅填入输入框，不自动发送；有内容后不再显示。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 示例点击 | 仅填入输入框，不自动提交 |
| 删除交互 | 点击删除 → `window.confirm` → 硬删除会话与 turns |
| 标题触发 | 首轮任意完成即生成（含澄清、失败提示） |
| 标题实现 | Memory 写回路径同步 LLM；失败回退截断；SSE 推送新标题 |
| 标题长度 | ≤10 字；仅 `title` 为空时写一次 |

## 3. 架构总览

```text
POST /api/chat (既有 SSE)
  → … → MemorySave
      → save_turn
      → 若 title 空：generate_session_title → set_session_title_if_empty(≤10)
      → pipeline 推送 session_title（若本次写入）

DELETE /api/sessions/{session_id}
  → 校验归属 → 删 turns + session

/app (AppWorkbench)
  Sidebar: 用户 · 会话列表(+删除) · 数据表 · 退出   （无示例区）
  Main 空态: 引导文案 + 示例问题（点击填入）
  Main 有轮次: 时间线；监听 session_title 刷新列表标题
```

分层不变：标题生成放在 Memory 确定性写回旁路（短 LLM 调用）；删除为确定性 API；前端只消费 API / SSE。

## 4. 后端设计

### 4.1 DELETE /api/sessions/{session_id}

- 路由：`backend/app/api/sessions.py`
- 鉴权：JWT；仅当前用户会话
- 行为：`delete_session(session_id, user_id)`  
  - 先 `assert_session_owner`  
  - 删除该会话下全部 `session_turns`，再删 `chat_sessions` 行  
  - 不存在或不属于当前用户 → **404** `{detail: "Session not found"}`
- 响应：**204 No Content**

### 4.2 标题生成

新增 `backend/app/agent/memory/title.py`：

```text
generate_session_title(question: str, result_summary: str | None) -> str
```

- 调用既有 `chat_completion`，prompt 要求：根据用户问题与本轮结果摘要，输出**不超过 10 个字符**的会话标题；只输出标题，无引号/标点堆砌
- 后处理：`strip` + 敏感信息剥离（复用 `strip_sensitive`）+ `[:10]`
- LLM 异常 / 空串：回退 `strip_sensitive(question).strip()[:10]`，再空则 `"新会话"`（仅作写入值；UI 对 `null` 也显示「新会话」）

修改 `set_session_title_if_empty`：截断上限由 **40 → 10**（与产品一致）；返回 `bool`（`True` = 本次写入了 title）。

修改 `memory_save`：

1. `save_turn` 成功后  
2. 若会话 title 已非空 → 跳过 LLM，不返回 `session_title`  
3. 否则 `title = generate_session_title(question, result_summary)`  
4. `written = set_session_title_if_empty(session_id, user_id, title)`  
5. 若 `written`：节点返回 `{"session_title": title}`  

**不**在 title 已有值时再次调用 LLM。判断「已有 title」可通过 store 轻量查询，或先调用会返回 False 的写入前先读；推荐 `get_session_title(session_id, user_id) -> str | None`，空才调 LLM。

### 4.3 SSE `session_title`

在 `pipeline.py` 的 `MemorySave` 节点结束后：若 delta 含 `session_title`，则 yield：

```text
event: session_title
data: {"session_id":"sess_xxx","title":"渠道GMV"}
```

`done` 事件不重复带 title；前端以 `session_title` 为准。

### 4.4 文档同步

更新 `docs/04-接口与前端.md`：

- Sessions：补充 DELETE；标题规则改为 LLM ≤10 + 回退；去掉「首问截断 ≤40」
- SSE 表增加 `session_title`
- 前端：示例在主区空态；侧栏可删会话；空 title 占位「新会话」

## 5. 前端设计

文件：`frontend/src/pages/AppWorkbench.tsx`（及 `api/sessions.ts`）。

### 5.1 会话删除

- 列表项增加删除控件（悬停或常显小按钮，避免误点主切换区）
- `confirm('确定删除该会话？删除后不可恢复。')`  
- 确认后 `DELETE /api/sessions/{id}`  
- 成功：从 `sessions` 移除；若删的是当前会话：  
  - 若列表仍有项 → 切换到 `updated_at` 最近的一条并加载 turns  
  - 若已空 → `POST /api/sessions` 新建并选中  
- 失败：侧栏错误提示

### 5.2 标题展示与刷新

- `session.title` 为空 / null → 显示「新会话」（侧栏与主区 header 一致）  
- **不再**用首问截断作为列表 title 乐观更新  
- SSE 收到 `session_title`：更新对应 session 的 `title`，并刷新 header  
- 首轮完成后若未收到事件：可在 `done` 后可选 `listSessions` 轻量刷新（非必须；有 SSE 即可）

### 5.3 示例问题空态

- 从侧栏移除「示例问题」整块  
- 当 `!loading && turns.length === 0`：主区展示引导文案 + 示例列表  
- 点击示例：`setQuestion(example.question)`，不调用 `streamChat`  
- 有 turns 或正在加载历史时不展示示例区  
- 文案调整为「可从下方示例选择，或在底部直接输入问题」

视觉：沿用现有 token（`accent` / `surface` / `muted`），示例为可点文本行，不做卡片堆叠，不引入新皮肤。

## 6. 错误处理

| 场景 | 行为 |
|------|------|
| LLM 标题失败 | 截断首问 ≤10；仍写 title；仍推 SSE（若有写入） |
| DELETE 404 | 前端提示「会话不存在或已删除」，刷新列表 |
| DELETE 当前会话后列表空 | 自动新建 |
| MemorySave 失败 | 既有行为：不写 turn / 不改 title；不推 `session_title` |
| 流中途 abort | 若未走到 MemorySave，标题保持「新会话」 |

## 7. 测试（TDD）

后端优先：

1. `test_delete_session_api`：创建 → 删 → 列表无；turns 404；非本人 404  
2. `test_set_session_title_if_empty`：上限改为 ≤10；二次不覆盖  
3. `test_generate_session_title`：mock LLM 返回长串 → 截断 10；LLM 抛错 → 回退截断  
4. `test_memory_save_sets_llm_title`：mock `generate_session_title` / `chat_completion`，断言首轮 title  
5. 可选：pipeline 在 MemorySave 后产出 `session_title` 事件

前端：手动验收（或现有 vitest 若有则补）——删除确认、空态示例、标题刷新。

## 8. 非目标

- 会话重命名手动编辑 UI  
- 软删除 / 回收站  
- 示例问题自动发送  
- 改 Intent / SQL / Guardrail / Chart 逻辑  
- 登录页与 Tables 页改动  

## 9. 验收标准

1. 侧栏可删会话；确认后硬删；删当前会切换或新建。  
2. 新会话显示「新会话」；首轮结束后侧栏/标题变为 ≤10 字摘要（LLM 或回退）。  
3. 示例仅在空会话主区；点击只填入；侧栏无示例区。  
4. `docs/04` 与实现一致；相关后端测试通过。
