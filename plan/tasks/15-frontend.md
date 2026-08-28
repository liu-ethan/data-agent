# Task 15: 前端工作台

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T14 · 交给：无（产品表面） · 里程碑：M5

文档：图表、分页、CSV 由前端实现，不包装成 Skill。MVP 做表格为主，图表仅为已有结果的简单可视化，不另做报告 Skill。

## Boundary

| | |
| --- | --- |
| **Owns** | Vite + React 工作台：登录、会话、流式对话、HITL、结果表、CSV、写入确认、简单图。 |
| **In** | `frontend/` 下列出的文件与 Playwright e2e。 |
| **Out** | 后端 Skill、Trace/推理抽屉、报告 Skill、改 API 契约、展示 Prompt/工具原始输出。 |
| **Must not** | 创建 `TraceDrawer.tsx`；编造候选项 ID；analyst 用户确认写入；把分页做成回查业务库。 |

**Files:**
- Create: `frontend/package.json`, `vite.config.ts`, `index.html`
- Create: `frontend/src/main.tsx`, `types.ts`, `client.ts`
- Create: `frontend/src/auth/AuthPage.tsx`
- Create: `frontend/src/workbench/AppShell.tsx`
- Create: `frontend/src/workbench/ThreadList.tsx`
- Create: `frontend/src/workbench/ConversationStream.tsx`
- Create: `frontend/src/workbench/ChatComposer.tsx`
- Create: `frontend/src/workbench/InterruptPanel.tsx`
- Create: `frontend/src/workbench/ResultTable.tsx`
- Create: `frontend/src/workbench/ChartRenderer.tsx`
- Create: `frontend/e2e/workbench.spec.ts`

不要创建 `TraceDrawer.tsx`。MVP 不展示 Prompt、工具原始输出或隐藏推理。

能力：登录、会话列表、流式对话、HITL 选项（真实 ID）、结果表分页、CSV 下载、写入预览确认（同一 operator）、展示指标口径/时间窗/`data_as_of`。图表仅为已有结果的简单可视化。

- [ ] **Step 1:** Playwright：登录 → 问一句 mock 查询 → 看到表格；写入预览点确认会调用 `/resume`。
