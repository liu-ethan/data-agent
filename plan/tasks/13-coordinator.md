# Task 13: Coordinator（意图、多轮、HITL 澄清、最终回答）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T2 T10 T12 · 交给：T14 T15 T16 · 里程碑：M4/M5

## Boundary

| | |
| --- | --- |
| **Owns** | 意图识别、任务结构化、**全仓库唯一** `interrupt()`、路由两个 Skill、候选项、`respond`、SqliteSaver 会话。 |
| **In** | `coordinator/graph.py`、`intent.py`、`hitl.py`、`respond.py`、`candidates.py`、`prompt/coordinator.yaml`、`prompt/response.yaml`、`tests/test_coordinator.py`、`tests/test_coordinator_interrupt.py`。`runtime.sqlite` 只 upsert `thread` 行。 |
| **Out** | SQL 编译/网关/执行、DuckDB vs MySQL 判断（属查询 Skill）、Skill 内部过程、HTTP 路由、前端、往 `task`/`hitl_interrupt` 表写正文。 |
| **Must not** | 在 HITL 节点里生成 `operation_id` 或写库（函数体只有 `interrupt()`）；把 `preview_rows` / Parquet 送进 Prompt；编造候选项；一句又查又写时拆成两个任务（应 `UNSUPPORTED`）；恢复时重取「现在」的时间窗；从 Checkpoint 恢复权限。 |

**Files:**
- Create: `backend/app/coordinator/graph.py`
- Create: `backend/app/coordinator/intent.py`
- Create: `backend/app/coordinator/hitl.py`
- Create: `backend/app/coordinator/respond.py`
- Create: `backend/app/coordinator/candidates.py`
- Create: `backend/app/prompt/coordinator.yaml`
- Create: `backend/app/prompt/response.yaml`
- Create: `tests/test_coordinator.py`
- Create: `tests/test_coordinator_interrupt.py`

**Interfaces:**
- Consumes: 用户消息 + `thread_id` + `RuntimeContext`
- Produces: 最终回答（数字必须带 `result_id` + 指标版本）或 HITL payload
- **全仓库唯一** 调用 `interrupt()` 的地方：Coordinator HITL 节点，函数体只有 `interrupt()`

节点：

1. LLM 识别意图 → `QUERY` / `WRITE` / `FOLLOWUP` / `CLARIFY` / `UNSUPPORTED`。一句里又查又写 → `UNSUPPORTED`。
2. `FOLLOWUP` 只表示继续上一轮查询，把上一轮 `QueryTask` + 本轮文本交给查询 Skill；**不**在 Coordinator 判断 DuckDB vs MySQL。本轮若改写时间则重算 `time_range`，否则沿用。
3. 模糊时 **先查真实候选项再 interrupt**：`candidates.py` 提供指标语义层、受限只读商品候选、可用数据时间范围。候选项必须带稳定 ID 且经过权限过滤；查不到只说未查到，禁止编造。
4. 查询 Skill 返回 `SCHEMA_GAP`/`AMBIGUOUS`/`TOO_BROAD`：Coordinator interrupt，不让 Skill 自己暂停。
5. 写入：调用 `prepare_write` → interrupt 展示预览 → resume 批准后 `execute_write`（同一 `operation_id`/`request_hash`）；拒绝则不写库。批准人必须是同一 operator。
6. `respond`：只把聚合标量、单位、时间窗、`data_as_of`、列名、`result_id`、指标版本送给 LLM。`preview_rows` 与 Parquet 不进 Prompt。数字无法在 summary 中找到则拒答，不编造。

用户用「这些 SKU」指上一轮结果：只从上一轮 READY 结果中名为 `sku_id` 或 `id` 且属于 `dim_sku` 的列取值填入 `WriteTask.object_ids`；列不存在或结果过期 → HITL / `RESULT_EXPIRED`。

SqliteSaver 按 `thread_id` 持久化。Skill 内部过程不进 Coordinator state。`runtime.sqlite` 只 upsert `thread` 行。

Checkpoint 只放：当前/上一轮 `QueryTask` 或 `WriteTask`、HITL payload、`result_id`、`operation_id`、`request_hash`。

- [ ] **Step 1:** 假 LLM 测：模糊商品名产生带 ID 的 HITL；「按门店筛一下」意图为 `FOLLOWUP` 且查询 Skill 走 DuckDB；「再加上退款率」意图为 `FOLLOWUP` 且查询 Skill 重查 MySQL；写入预览的 `interrupt` 只出现在 Coordinator；编造候选项的路径不存在；查询/写入 Skill 模块不含 `interrupt(`。
