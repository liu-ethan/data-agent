# Task 2: 领域类型、服务端时间、权限上下文

> 先读 [../development-notes.md](../development-notes.md)。冲突以 Locked Decisions 为准。
>
> 依赖：T1 · 交给：T3–T16 · 里程碑：M1

## Boundary

| | |
| --- | --- |
| **Owns** | `backend/app/types.py` 落地核心契约；服务端把「今天/本月」解析成半开区间；每次请求从 `users.sqlite` 加载权限。 |
| **In** | `types.py`（形状必须与 development-notes §9 一致）、`runtime/time.py`、`runtime/permissions.py`、`runtime/context.py`、对应测试。 |
| **Out** | Coordinator 意图识别、HITL、Catalog 读写、MySQL 查询、会话 Checkpoint、前端。 |
| **Must not** | 增删 `Intent` 枚举（尤其不要加 `FOLLOWUP_FILTER` / `FOLLOWUP_REQUERY`）；把权限缓存进 Checkpoint；做真多租户（`tenant_id` 恒为 `"default"`）；用 LLM 解析相对时间；HITL 恢复时重新取「现在」。 |

**Files:**
- Create: `backend/app/types.py`（完整契约见 [development-notes.md](../development-notes.md) §9）
- Create: `backend/app/runtime/time.py`
- Create: `backend/app/runtime/permissions.py`
- Create: `backend/app/runtime/context.py`
- Create: `tests/test_time.py`
- Create: `tests/test_permissions.py`

**Interfaces:**
- Consumes: `Settings.app.timezone`
- Produces:
  - `resolve_time_range(text: str | None, request_time_utc: str, timezone: str) -> TimeRange`
  - `build_runtime_context(...) -> RuntimeContext`
  - `reload_permissions(user_id: str) -> PermissionSet`

规则：

- 「今天」「本月」「昨天」「近7天」等由规则解析为 `[start, end)`，不用 LLM。
- HITL **恢复**不得重新取「现在」。新用户消息：若本轮文本改写了时间则用本轮 `request_time_utc` 重算；若未提时间则沿用上一轮 `QueryTask.time_range`。
- `tenant_id` 恒为 `"default"`；`reload_permissions` 每次从 `users.sqlite` 读最新 `permission_version`。Checkpoint 里即使有旧权限也丢弃。
- `RuntimeContext` 不含数据库连接。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_time.py
from backend.app.runtime.time import resolve_time_range

def test_today_is_half_open_interval():
    tr = resolve_time_range("今天", "2026-08-28T16:00:00+00:00", "Asia/Shanghai")
    assert tr.start.startswith("2026-08-29") or tr.start.startswith("2026-08-28")
    # Asia/Shanghai = UTC+8, 16:00 UTC = 00:00 next calendar day
    assert tr.end > tr.start
    assert tr.grain == "day"

def test_this_month_exclusive_end():
    tr = resolve_time_range("本月", "2026-08-28T03:00:00+00:00", "Asia/Shanghai")
    assert tr.start.startswith("2026-08-01")
    assert tr.end.startswith("2026-09-01")
```

```python
# tests/test_permissions.py
from backend.app.runtime.permissions import reload_permissions

def test_permissions_are_reloaded_not_cached_from_checkpoint():
    p1 = reload_permissions("u1")
    p2 = reload_permissions("u1")
    assert p1.permission_version == p2.permission_version
    assert p1.allowed_tables
```

- [ ] **Step 2–4:** 实现半开区间时间解析与权限加载；跑 pytest 至通过。

- [ ] **Step 5: Commit** `feat: add runtime time resolution and permission context`
