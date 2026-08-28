# Task 14: HTTP API（鉴权、对话流、结果分页、HITL 恢复）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T6 T13 · 交给：T15 · 里程碑：M5

## Boundary

| | |
| --- | --- |
| **Owns** | FastAPI 路由：登录、会话、SSE 对话、HITL resume、结果分页与 CSV。 |
| **In** | `api/auth.py`、`chat.py`、`results.py`、`interrupts.py`，挂到 `main.py`；`tests/test_api.py`。 |
| **Out** | 前端页面、评测 runner、Skill 图、按页打 MySQL、往 `task`/`hitl_interrupt` 写正文。 |
| **Must not** | `POST /resume` 恢复 Skill 子图（只 `Command.RESUME` Coordinator）；CSV 无限导出；未校验 READY/所有者/权限/TTL 就读结果；用 Checkpoint 当写入提交证据。 |

**Files:**
- Create: `backend/app/api/auth.py`
- Create: `backend/app/api/chat.py`
- Create: `backend/app/api/results.py`
- Create: `backend/app/api/interrupts.py`
- Create: `backend/app/main.py`（挂路由）
- Create: `tests/test_api.py`

**API：**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/login` | 本地用户，返回 session/JWT |
| POST | `/api/threads` | 新建会话 |
| GET | `/api/threads` | 列表 |
| POST | `/api/threads/{id}/messages` | SSE：token、工具进度、HITL、最终答案 |
| POST | `/api/threads/{id}/resume` | 提交 HITL 决定 |
| GET | `/api/results/{result_id}` | 分页读 Parquet，query: `offset,limit` |
| GET | `/api/results/{result_id}.csv` | CSV，上限 `min(row_count, results.max_rows)`，校验 READY/所有者/权限/TTL |

SSE 事件类型：`token` | `status` | `interrupt` | `result_ref` | `error` | `done`。

分页只读已落盘文件，禁止按页回查业务库。

`POST /resume` 只 `Command.RESUME` Coordinator 图。不要按 Skill 分子 resume。

`GET /api/threads` 读 `runtime.sqlite.thread`；创建线程时同时写 thread 投影与 Checkpoint。不要读写 `task` / `hitl_interrupt` 表。

- [ ] **Step 5: Commit** `feat: add auth, sse chat, result paging, and hitl resume APIs`
