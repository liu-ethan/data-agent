# Spec 08：Frontend 数据分析工作台

状态：`Implemented`

对应里程碑：M0、M3、M5、M7

## 1. 范围

实现面向电商分析师和管理员的数据分析工作台。前端的单一核心任务是：提交自然语言问题，持续看到系统进度，检查证据和权限，阅读结果，并在需要时回答澄清问题。

前端不是营销首页，也不展示隐藏推理。所有可见状态都来自后端 API、SSE 和 Result/Artifact 契约。

## 2. 设计方向

视觉方向为“数据证据工作台”：像一张可追溯的分析台账，而不是聊天机器人或营销仪表盘。

### 2.1 设计选择

| 维度 | 选择 |
| --- | --- |
| 主色 | 墨黑 `#20262B`、纸白 `#F7F8F5`、青绿 `#0E7C7B` |
| 状态色 | 成功 `#2F8F6B`、警告 `#D49A2A`、拒绝/错误 `#D85C55` |
| 边界色 | 雾灰 `#D8E0DE`，用于分隔线和不可用状态 |
| 标题字体 | `Space Grotesk`，只用于页面标题和重要状态，不用于密集表格 |
| 正文字体 | `IBM Plex Sans`，用于对话、标签和按钮 |
| 数据字体 | `IBM Plex Mono`，用于 SQL、Trace、数值和时间 |
| 圆角和阴影 | 主要区域不做浮动卡片；重复结果项最多 `8px` 圆角，阴影只用于弹层和确认框 |

不使用渐变背景、装饰性圆球、模糊图片、营销 Hero 或大面积单一紫色/蓝色主题。

### 2.2 布局和签名元素

桌面端采用三栏工作台：

```text
┌──────────────────────────────────────────────────────────────┐
│ Brand / 当前线程 / 连接状态 / 用户菜单                       │
├───────────────┬──────────────────────────┬───────────────────┤
│ 线程列表       │ 对话与结果流             │ 证据栏            │
│ 新建问题       │ 用户问题                 │ 运行节点           │
│ 最近会话       │ SSE 运行状态             │ Action 时间线      │
│ 搜索线程       │ 最终回答                 │ QuerySpec 摘要     │
│               │ ResultTable / Chart      │ SQL/权限/版本      │
│               │                          │                   │
├───────────────┴──────────────────────────┴───────────────────┤
│ 输入框 / 发送 / 停止 / 澄清回复                               │
└──────────────────────────────────────────────────────────────┘
```

唯一签名元素是右侧“证据栏”：用一条垂直状态脊柱连接 `RETRIEVE -> GENERATE -> EXECUTE -> RESPOND`，每个节点只显示公开状态、耗时、版本和拒绝原因，不显示隐藏推理。它表达真实的系统执行顺序，不是装饰。

移动端将线程列表和证据栏变为抽屉；中心对话和结果区域保持主任务优先。任何固定宽度表格必须支持横向滚动，不得遮挡输入框或操作按钮。

桌面端工作台铺满视口：`width/height: 100%`，页面本身不随对话或结果变长；会话列表、对话流和证据栏在栏内滚动。`< 768px` 仍为单栏加抽屉。

## 3. 页面和路由

| 路由 | 能力 | MVP 状态 |
| --- | --- | --- |
| `/login` | 登录和错误状态 | M0 |
| `/app` | 当前线程、提问、运行状态和结果 | M3 |
| `/app/threads/:thread_id` | 打开已有线程 | M3，M5 后支持恢复 |
| `/app/results/:result_id` | 查看已授权结果、分页和导出 | M3 |
| `/app/settings` | 时区和确认后的长期偏好 | M5 |

M6 启用 WriteGateway 后，对话流中的 `WRITE_APPROVAL` Interrupt 展示 MutationPreview（before/after、预计影响行数）并提供确认/取消。

## 4. 前端状态契约

前端状态拆为四层：

| 状态 | 保存内容 | 持久化规则 |
| --- | --- | --- |
| `authState` | `user_id`、角色、token 状态、过期时间 | token 不写入 URL；`access_token` 写入 `localStorage`，刷新或同 origin 新页面在未过期时恢复会话；登出、过期或 401 时清除 |
| `threadState` | `thread_id`、消息、当前 `request_id`、运行状态 | 消息来自 API；不能把完整 SQL 结果复制进状态 |
| `runState` | 当前 Node、Action、预算、错误、SSE 连接状态 | 由 SSE 事件增量更新；断线后按 `request_id` 重连或显示恢复入口 |
| `artifactState` | `result_id`、`artifact_id`、类型、过期状态、分页游标 | 只保存引用和摘要；读取前重新经过后端权限校验 |

前端 TypeScript 类型必须从后端 OpenAPI 或共享 schema 生成，不手写第二套 `TaskFrame`、`ResultObservation`、`Interrupt` 或错误枚举。

## 5. API、SSE 和交互规则

### 5.1 请求

前端调用：

- `POST /api/chat/stream`：启动一次运行。Body 为 `ChatRequest` JSON，用户消息不得放入 query string。
- `GET /api/chat/stream`：按 `request_id` 和 `Last-Event-ID` 重连已有运行，不得携带 `message`。
- `POST /api/chat`：非流式同步结果，供评测脚本使用。
- `GET /api/results/{result_id}`：分页读取已授权结果。
- [Spec 05](./05-memory-interrupts-and-artifacts.md) 的 resume 接口。
- `DELETE /api/threads/{thread_id}`：仅线程所有者可删除近期会话；成功返回 204。

所有请求使用：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
X-Request-ID: <request_id>
```

`VITE_API_BASE_URL` 是唯一允许的 API origin 配置；组件内不得硬编码 `localhost:8000`。

### 5.2 SSE

SSE 事件必须按 `request_id` 和 `thread_id` 归属当前运行：

- `run.started`：显示“正在理解问题”；
- `node.started`：更新证据栏当前节点；
- `node.completed`：显示公开状态、耗时、预算变化和错误码；
- `interrupt.created`：停止自动发送，展示候选选项和自由输入；
- `run.completed`：加载 `result_id` 或 `artifact_id`；
- `run.failed`：展示可操作的错误信息和重试入口。

SSE 断开时最多自动重连 2 次。重连只订阅同一 `request_id`，不得把用户消息再次写入 URL 或再 POST 一遍。超过次数后显示“连接已中断”和继续查看线程的入口。

### 5.3 结果和错误

- `EMPTY` 显示“没有符合条件的数据”，不能显示为 0；
- `REJECTED` 显示拒绝原因和可修改条件，不能显示 SQL 安全细节；
- `QUERY_TIMEOUT` 显示缩小时间范围或减少维度的建议；
- `PERMISSION_DENIED` 不展示被拒绝对象的名称、字段或行数据；
- `ARTIFACT_STALE` 要求重新运行或重新确认，不使用旧制品；
- 任何错误都显示 `trace_id`，便于排查。

## 6. 组件边界

| 组件 | 职责 | 禁止事项 |
| --- | --- | --- |
| `AppShell` | 布局、导航、全局连接和权限状态 | 不直接调用 MySQL 或拼接 API URL |
| `ThreadList` | 会话列表、搜索、新建、删除线程 | 不保存完整结果集 |
| `ChatComposer` | 输入、发送、停止、澄清回答 | 不自行判断 SQL 或权限 |
| `RunEvidenceRail` | Node/Action/耗时/版本公开状态 | 不展示隐藏 Prompt 或隐藏推理 |
| `ResultTable` | 分页、排序、列分类、空态 | 不允许客户端绕过后端获取全量结果 |
| `ChartRenderer` | 渲染白名单 ECharts DSL | 不执行 JavaScript 或模型生成代码 |
| `InterruptPanel` | 候选选择、文本回答、resume | 不重复提交同一 `client_request_id` |
| `TraceDrawer` | 展示 trace_id、公开 Trace 字段和错误码 | 不展示 secret、JWT、手机号、身份证或完整 SQL 结果 |

## 7. 跨域配置

### 7.1 后端配置

后端从配置读取精确的允许来源，不能写死在代码中：

```yaml
app:
  cors_origins:
    - http://localhost:5173

server:
  cors:
    allowed_methods: [GET, POST, DELETE, OPTIONS]
    allowed_headers: [Authorization, Content-Type, X-Request-ID, Last-Event-ID]
    allow_credentials: false
    max_age_seconds: 600
```

MVP 使用 `Authorization: Bearer`，因此 `allow_credentials` 为 `false`。如果以后切换 HttpOnly Cookie，必须同时改为 `true`，并保持精确 origin 白名单；任何情况下都禁止 `allow_origins = ["*"]`。

后端必须：

- 对允许 origin 返回匹配的 `Access-Control-Allow-Origin`；
- 对不允许 origin 不返回允许跨域头；
- 正确处理 `OPTIONS` 预检；
- 允许 `Authorization`、`Content-Type`、`X-Request-ID` 和 `Last-Event-ID`；
- 对 SSE 返回 `Content-Type: text/event-stream`，并保持同一跨域策略；
- 不把数据库密码、LLM key 或 JWT 放入跨域响应头。

### 7.2 前端开发地址

默认前端为 `http://localhost:5173`，后端为 `http://localhost:8000`。前端 API base URL 只能来自环境变量，例如 `VITE_API_BASE_URL`，不能在组件中散落硬编码。

生产环境必须使用明确的前端 origin 替换本地 origin，并在部署检查中验证预检、SSE 和 Bearer 请求。

## 8. 响应式和无障碍要求

- 桌面布局断点：`>= 1200px` 三栏；`768px-1199px` 收起线程列表或证据栏；`< 768px` 单栏；
- 键盘可以完成新建线程、删除会话、发送、停止、选择澄清项、打开证据栏和下载结果；
- 所有图标按钮有可见 tooltip 或无障碍名称；
- 当前 Node、错误和 Interrupt 使用 `aria-live`；
- 颜色不是唯一状态表达方式，同时使用文字、图标或边框；
- 遵守 `prefers-reduced-motion`，SSE 状态动画只做轻量变化；
- 表格表头、焦点状态和错误信息必须可被屏幕阅读器识别。

## 9. 测试和验收

### 9.1 前端测试

- React Testing Library：组件状态、错误态、空态和权限态；
- SSE mock：正常完成、重连、超时、Interrupt 和失败；
- Playwright：登录、提问、结果查看、分页、澄清恢复和移动端布局；
- API 契约测试：前端类型与 OpenAPI/SSE 事件版本一致；
- 无障碍检查：键盘路径、焦点、`aria-live` 和对比度。

### 9.2 跨域测试

- 允许 origin 的 GET、POST 和 OPTIONS 预检通过；
- 不允许 origin 不返回 `Access-Control-Allow-Origin`；
- Bearer Authorization 预检通过；
- SSE 请求可以接收 `text/event-stream`；
- 配置 `allow_credentials = false` 时不返回 `Access-Control-Allow-Credentials: true`；
- 本地端口变化可以通过配置或环境变量调整，不改代码。

### 9.3 前端验收标准

- M0：React 应用可启动、可访问 `/login` 和 `/app`，API base URL 和 CORS 配置生效；已登录刷新不得退回 `/login`，除非 token 已过期或被 401 失效；
- M3：10 条单轮 Golden Case 可以在界面完成提问、查看 SSE、Trace 和结果表格；
- M5：多轮指代、Interrupt resume、CSV 和 ECharts DSL 可以恢复且不重复提交；
- M7：桌面和移动端关键路径通过 Playwright，错误、空结果、权限拒绝和连接断开均有可操作反馈；
- 前端不展示隐藏推理、完整 Prompt、敏感字段、密钥或未经授权的结果。

## 10. 测试证据

仓库内验收以测试为准，不把截图或手工录屏当作合入证据。

- Spec 08 §5/§6/§7/§8 不变量 (`frontend/src/workbench/spec08.test.tsx` 与 `frontend/src/main.test.tsx`)：三栏工作台与证据脊柱、`EMPTY` 不显示为 0、`PERMISSION_DENIED` 不含对象名、SSE 首次 POST 且重连 GET 不带 message、Interrupt 使用稳定 `client_request_id`、证据栏不含 Prompt、已登录刷新恢复会话。
- SSE 解析、`Last-Event-ID` 续传与 access token 本地恢复 (`frontend/src/client.test.ts`)。
- Playwright 登录、刷新保会话、提问、证据栏、澄清恢复、设置和移动端布局 (`frontend/e2e/workbench.spec.ts`)。
- CORS 预检、拒绝 origin、Bearer 头、`allow_credentials=false`、SSE `text/event-stream` (`tests/test_frontend_spec08.py`)。
- OpenAPI 发布 `RuntimeEvent` 与 `POST|GET /api/chat/stream` (`tests/test_api.py`)。

## 11. 模块拆分 (M0/M3/M5 重构后)

| 文件 | 职责 |
| --- | --- |
| `frontend/src/workbench/AppShell.tsx` | 顶栏、三栏/抽屉布局、连接状态、用户菜单 |
| `frontend/src/workbench/ThreadList.tsx` | 会话列表、搜索、新建、删除 |
| `frontend/src/workbench/ChatComposer.tsx` | 输入、发送、停止 |
| `frontend/src/workbench/RunEvidenceRail.tsx` | `RETRIEVE -> GENERATE -> EXECUTE -> RESPOND` 公开状态脊柱 |
| `frontend/src/workbench/ResultTable.tsx` | 分页、当前页排序、空态、CSV |
| `frontend/src/workbench/ChartRenderer.tsx` | 白名单 ECharts DSL |
| `frontend/src/workbench/InterruptPanel.tsx` | 澄清候选、MutationPreview 审批与 resume |
| `frontend/src/workbench/TraceDrawer.tsx` | `trace_id` 与公开错误码 |
| `frontend/src/client.ts` | Bearer、`X-Request-ID`、SSE 解析、access token 本地恢复 |
| `backend/app/api/app.py` | CORS 中间件、`POST` 启动流、`GET` 重连流 |

M6 的 MutationPreview 审批嵌在对话 Interrupt 中，不单独提供写入页面。旧 `frontend/src/dashboard/` 两栏聊天布局已删除。
