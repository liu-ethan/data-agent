# Task 6: Result Store（Parquet 生命周期 + DuckDB 受限筛选）

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T2 · 交给：T7 T10 T14 · 里程碑：M2

## Boundary

| | |
| --- | --- |
| **Owns** | 查询结果的 Parquet 生命周期、元数据（`results.sqlite`）、已有列上的 DuckDB 筛选。 |
| **In** | `results/store.py`、`parquet.py`、`duckdb_filter.py`、`tests/test_result_store.py`、`tests/test_duckdb_filter.py`。 |
| **Out** | HTTP CSV 路由（T14）、MySQL 执行、查询 Skill 的 followup 决策、会话 Checkpoint、把结果行写入 SQLite。 |
| **Must not** | 让 LLM 写任意 DuckDB SQL；按页回查业务库；权限版本变化后仍读旧文件；半写入留下 `.parquet`；无限明细导出（CSV 上限见 Locked Decision 8，路由本身属 T14）。 |

**Files:**
- Create: `backend/app/results/store.py`
- Create: `backend/app/results/parquet.py`
- Create: `backend/app/results/duckdb_filter.py`
- Create: `tests/test_result_store.py`
- Create: `tests/test_duckdb_filter.py`

**Interfaces:**
- `create_writing(meta) -> result_id`  （SQLite 行状态 `WRITING`，打开 `.part`）
- `finalize(result_id)` → 原子 `rename(.part → .parquet)`，状态 `READY`
- `abort(result_id)` → 删 `.part`，状态失败清理
- `read_page` 校验 `tenant_id == "default"`、所有者、实时权限版本、TTL、`READY`
- `filter_local(result_id, spec: LocalFilterSpec, ctx) -> result_id`：仅已有列上 filter/sort/select/topn
- 后台：TTL 扫描 `READY → EXPIRED → DELETED`；孤儿 `.part` 清理
- 读与 TTL 删除共用同一结果锁

CSV（T14）上限见 Locked Decision 8：`min(row_count, results.max_rows)`。

`LocalFilterSpec` 由规则生成，**禁止** LLM 写任意 DuckDB SQL。新结果必须带 `parent_result_id`。权限版本变化 → `PERMISSION_CHANGED`，拒绝读旧文件。过期 → `RESULT_EXPIRED`。

流式写入：超过 `max_rows` / `max_bytes` 则 `abort`。

- [ ] **Step 1:** 测试半写入崩溃后无 `.parquet`、finalize 后可读、过期拒绝、非法列筛选拒绝、权限版本变化拒绝。
