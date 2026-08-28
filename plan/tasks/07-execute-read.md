# Task 7: 只读 MySQL 执行 Tool

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T3 T5 T6 · 交给：T10 · 里程碑：M2

## Boundary

| | |
| --- | --- |
| **Owns** | 用 reader 账号参数化流式执行已通过网关的 `CompiledQuery`，写入 Result Store，计算 `data_as_of`。 |
| **In** | `mysql/execute_read.py`、`tests/test_execute_read.py`。 |
| **Out** | 网关策略、MetricCompiler、查询 Skill 图、写库、非参数化 `text(sql)`。 |
| **Must not** | 跳过网关直接执行；把 `FilterCond.value` 拼进 SQL；用 writer 账号；失败后留下 `.parquet`（必须 `abort`）；自己发明第三套 `data_as_of` 公式。 |

**Files:**
- Create: `backend/app/mysql/execute_read.py`
- Create: `tests/test_execute_read.py`

**Interfaces:**
- Consumes: gateway 已通过的 `CompiledQuery` + `RuntimeContext`
- Produces: 调用 Result Store 后返回 `result_id`；并按 Locked Decision 8 写入 `data_as_of`

`data_as_of` = `min(request_time_utc, max(本次用到的各 grain 表 time_field 的 MAX()))`。空表则等于时间窗 `start`。

流程：`create_writing` → reader 连接设 `max_execution_time` → **参数化** stream cursor → 写 `.part` → `finalize`。超时/超限走 `abort`。查询失败可重试（幂等 SELECT）。禁止 `text(sql)` 无参数执行。
