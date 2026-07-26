# Session Title / Delete / Examples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话可确认后硬删除；首轮结束后 LLM 生成 ≤10 字标题并经 SSE 推送；示例问题移到主区空态且点击仅填入。

**Architecture:** Memory store 增加 `delete_session` / `get_session_title`，标题截断改为 10 且 `set_session_title_if_empty` 返回是否写入；`memory/title.py` 负责短 LLM 摘要；`memory_save` 仅在 title 空时生成并返回 `session_title`；pipeline yield SSE；前端侧栏删除 + 空态示例 + 监听标题事件。

**Tech Stack:** Python 3.12（conda）· FastAPI · pytest · React · Vite · TypeScript · Tailwind

## Global Constraints

- 规格：`spec/2026-07-26-session-title-delete-examples-design.md`；实现后同步 `docs/04-接口与前端.md`
- 配置仅用根目录 `config.yaml`；禁止 `.env`
- **Python（强制）**：`/home/user/miniconda3/envs/python3.12/bin/python`（下文 `PY`）与同目录 `pip`；禁止系统 Python / `.venv`
- **禁止** git worktree / `.worktrees/`；只在本仓库 `main` 工作区改代码
- **Git commit：仅当用户明确要求时执行**；本计划 Commit 步骤默认跳过（可汇报建议 message）
- TDD：store / title / DELETE / memory_save 先写失败测试再实现
- 一次只改当前 Task 相关文件；不做顺手大重构
- 示例点击：仅填入，不自动发送；删除：confirm 后硬删

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/app/agent/memory/store.py` | `get_session_title` / `delete_session`；`set_session_title_if_empty` → bool、上限 10 |
| `backend/app/agent/memory/title.py` | `generate_session_title` |
| `backend/app/agent/memory/__init__.py` | 导出新符号 |
| `backend/app/agent/nodes/memory_save.py` | 空 title 时 LLM 写标题并返回 `session_title` |
| `backend/app/agent/pipeline.py` | MemorySave 后 yield `session_title` |
| `backend/app/api/sessions.py` | `DELETE /sessions/{session_id}` → 204 |
| `backend/tests/test_memory_store.py` | store 单测更新/新增 |
| `backend/tests/test_session_title.py` | `generate_session_title` + memory_save mock |
| `backend/tests/test_sessions_api.py` | DELETE + 更新 title 断言 |
| `frontend/src/api/sessions.ts` | `deleteSession` |
| `frontend/src/pages/AppWorkbench.tsx` | 删除 UX、空态示例、`session_title` |
| `docs/04-接口与前端.md` | API / 标题 / 示例位置同步 |

工作目录：

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
PY=/home/user/miniconda3/envs/python3.12/bin/python
```

前端：

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/frontend
npm run build
```

---

### Task 1: Memory store — get / delete / title≤10 + bool

**Files:**
- Modify: `backend/app/agent/memory/store.py`
- Modify: `backend/app/agent/memory/__init__.py`
- Modify: `backend/tests/test_memory_store.py`

**Interfaces:**
- Produces:
  - `get_session_title(session_id: str, user_id: str) -> str | None`
  - `delete_session(session_id: str, user_id: str) -> None`（归属失败抛 `MemoryError`）
  - `set_session_title_if_empty(session_id: str, user_id: str, title: str) -> bool`（`True` = 本次写入 title；截断 ≤10；空 title 跳过仍可刷新 `updated_at` 并返回 `False`）

- [ ] **Step 1: Write failing tests**

更新 `test_set_session_title_if_empty` 并追加：

```python
from app.agent.memory.store import (
    MemoryError,
    create_session,
    delete_session,
    get_session_title,
    list_sessions,
    list_turns,
    save_turn,
    set_session_title_if_empty,
)


def test_set_session_title_if_empty(memory_user_id):
    sess = create_session(memory_user_id)
    long_title = "最近三十天各渠道 GMV 趋势如何变化以及同比环比情况请详细分析并给出 actionable 建议"
    assert len(long_title) > 10
    written = set_session_title_if_empty(sess["id"], memory_user_id, long_title)
    assert written is True
    listed = list_sessions(memory_user_id)
    title = next(s["title"] for s in listed if s["id"] == sess["id"])
    assert title is not None
    assert len(title) == 10
    written2 = set_session_title_if_empty(sess["id"], memory_user_id, "第二次不应覆盖")
    assert written2 is False
    listed2 = list_sessions(memory_user_id)
    assert next(s["title"] for s in listed2 if s["id"] == sess["id"]) == title


def test_get_session_title_none_then_value(memory_user_id):
    sess = create_session(memory_user_id)
    assert get_session_title(sess["id"], memory_user_id) is None
    set_session_title_if_empty(sess["id"], memory_user_id, "渠道GMV")
    assert get_session_title(sess["id"], memory_user_id) == "渠道GMV"


def test_delete_session_removes_turns(memory_user_id, other_user_id):
    sess = create_session(memory_user_id)
    save_turn(
        session_id=sess["id"],
        user_id=memory_user_id,
        question="q",
        intent="x",
        sql_text=None,
        slots={"metrics": [], "filters": {}, "group_by": [], "time_range": None},
        result_summary="ok",
    )
    delete_session(sess["id"], memory_user_id)
    assert all(s["id"] != sess["id"] for s in list_sessions(memory_user_id))
    with pytest.raises(MemoryError):
        list_turns(sess["id"], memory_user_id)
    other = create_session(memory_user_id)
    with pytest.raises(MemoryError):
        delete_session(other["id"], other_user_id)
```

（`other_user_id` fixture 若不存在，按 `test_memory_store.py` 现有方式新增：另一个已注册用户 id。）

- [ ] **Step 2: Run tests to verify they fail**

```bash
$PY -m pytest tests/test_memory_store.py::test_set_session_title_if_empty tests/test_memory_store.py::test_get_session_title_none_then_value tests/test_memory_store.py::test_delete_session_removes_turns -v
```

Expected: FAIL（`get_session_title` / `delete_session` 未定义，或 title 仍为 40 / 返回 None）

- [ ] **Step 3: Implement store helpers**

在 `store.py`：

```python
def get_session_title(session_id: str, user_id: str) -> str | None:
    assert_session_owner(session_id, user_id)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ?",
            (str(session_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    title = (row["title"] or "").strip()
    return title or None


def set_session_title_if_empty(session_id: str, user_id: str, title: str) -> bool:
    assert_session_owner(session_id, user_id)
    clipped = strip_sensitive(title).strip()[:10]
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ?",
            (str(session_id),),
        ).fetchone()
        existing = (row["title"] or "").strip() if row else ""
        now = _now()
        if existing:
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (now, str(session_id)),
            )
            conn.commit()
            return False
        if not clipped:
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (now, str(session_id)),
            )
            conn.commit()
            return False
        conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (clipped, now, str(session_id)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_session(session_id: str, user_id: str) -> None:
    assert_session_owner(session_id, user_id)
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM session_turns WHERE session_id = ?",
            (str(session_id),),
        )
        conn.execute(
            "DELETE FROM chat_sessions WHERE id = ?",
            (str(session_id),),
        )
        conn.commit()
    finally:
        conn.close()
```

导出到 `__init__.py`：`get_session_title`、`delete_session`。

- [ ] **Step 4: Run tests to verify they pass**

```bash
$PY -m pytest tests/test_memory_store.py::test_set_session_title_if_empty tests/test_memory_store.py::test_get_session_title_none_then_value tests/test_memory_store.py::test_delete_session_removes_turns -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

建议 message：`feat(memory): session delete + title cap 10`

---

### Task 2: `generate_session_title` + memory_save 写 LLM 标题

**Files:**
- Create: `backend/app/agent/memory/title.py`
- Create: `backend/tests/test_session_title.py`
- Modify: `backend/app/agent/nodes/memory_save.py`
- Modify: `backend/tests/test_sessions_api.py`（`test_memory_save_sets_session_title`）

**Interfaces:**
- Consumes: `get_session_title`、`set_session_title_if_empty`、`chat_completion`、`strip_sensitive`
- Produces:
  - `generate_session_title(question: str, result_summary: str | None) -> str`
  - `memory_save(...)` 在本次新写 title 时返回 `{"session_title": "<≤10>"}`，否则 `{}`（或仅无该 key）

- [ ] **Step 1: Write failing tests**

`backend/tests/test_session_title.py`：

```python
from unittest.mock import patch

from app.agent.memory.title import generate_session_title


def test_generate_session_title_clips_llm_output():
    with patch(
        "app.agent.memory.title.chat_completion",
        return_value="这是一个远远超过十个字的很长标题内容",
    ):
        title = generate_session_title("上个月各渠道 GMV", "渠道 A 领先")
    assert len(title) <= 10
    assert title


def test_generate_session_title_falls_back_on_llm_error():
    with patch(
        "app.agent.memory.title.chat_completion",
        side_effect=RuntimeError("llm down"),
    ):
        title = generate_session_title("各渠道GMV对比如何", None)
    assert title == "各渠道GMV对比如何"[:10]


def test_memory_save_returns_session_title(memory_user_id):
    from app.agent.memory.store import create_session, get_session_title
    from app.agent.nodes.memory_save import memory_save

    sess = create_session(memory_user_id)
    with patch(
        "app.agent.nodes.memory_save.generate_session_title",
        return_value="渠道GMV",
    ) as gen:
        out = memory_save(
            {
                "session_id": sess["id"],
                "user_id": memory_user_id,
                "question": "各渠道 GMV 对比",
                "intent": "channel_analysis",
                "slots": {
                    "metrics": ["gmv"],
                    "filters": {},
                    "group_by": ["channel"],
                },
                "generated_sql": "SELECT 1",
                "answer": "渠道 A 领先",
                "need_clarification": False,
            }
        )
    gen.assert_called_once()
    assert out.get("session_title") == "渠道GMV"
    assert get_session_title(sess["id"], memory_user_id) == "渠道GMV"

    with patch(
        "app.agent.nodes.memory_save.generate_session_title",
        return_value="不应再调用",
    ) as gen2:
        out2 = memory_save(
            {
                "session_id": sess["id"],
                "user_id": memory_user_id,
                "question": "再问一次",
                "intent": "channel_analysis",
                "slots": {"metrics": ["gmv"], "filters": {}, "group_by": []},
                "answer": "ok",
                "need_clarification": False,
            }
        )
    gen2.assert_not_called()
    assert "session_title" not in out2
```

（`memory_user_id` fixture：复用 `test_memory_store.py` 的 conftest / 同文件 fixture；若仅在该文件定义，则把 fixture 挪到 `conftest.py` 或在本文件复制最小 fixture。）

同时改 `test_sessions_api.py::test_memory_save_sets_session_title`：mock `generate_session_title` 返回 `"渠道GMV"`，断言 `mine["title"] == "渠道GMV"`。

- [ ] **Step 2: Run tests to verify they fail**

```bash
$PY -m pytest tests/test_session_title.py tests/test_sessions_api.py::test_memory_save_sets_session_title -v
```

Expected: FAIL（`title` 模块不存在或 memory_save 仍用首问全文）

- [ ] **Step 3: Implement title + memory_save**

`backend/app/agent/memory/title.py`：

```python
from __future__ import annotations

from app.agent.llm import chat_completion
from app.agent.memory.summarize import strip_sensitive

_MAX = 10


def generate_session_title(question: str, result_summary: str | None) -> str:
    q = strip_sensitive(question or "").strip()
    summary = strip_sensitive(result_summary or "").strip()
    fallback = (q[:_MAX] if q else "新会话")
    messages = [
        {
            "role": "system",
            "content": (
                "你是会话标题助手。根据用户问题与本轮结果摘要，"
                "生成不超过10个字符的中文标题。只输出标题本身，不要引号或解释。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{q}\n摘要：{summary or '（无）'}",
        },
    ]
    try:
        raw = chat_completion(messages, temperature=0)
    except Exception:
        return fallback
    title = strip_sensitive(raw or "").strip().strip("\"'「」").replace("\n", "")
    title = title[:_MAX]
    return title or fallback
```

`memory_save.py` 关键标题段：

```python
from app.agent.memory.title import generate_session_title

# save_turn 成功后：
out: dict = {}
try:
    existing = store.get_session_title(session_id, user_id)
    if not existing:
        title = generate_session_title(question, result_summary)
        if store.set_session_title_if_empty(session_id, user_id, title):
            out["session_title"] = title[:10]
except store.MemoryError:
    pass

# … preferences / append_summary 逻辑不变 …
return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
$PY -m pytest tests/test_session_title.py tests/test_sessions_api.py::test_memory_save_sets_session_title -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

建议 message：`feat(memory): LLM session title on first turn`

---

### Task 3: DELETE /api/sessions/{session_id}

**Files:**
- Modify: `backend/app/api/sessions.py`
- Modify: `backend/tests/test_sessions_api.py`

**Interfaces:**
- Consumes: `delete_session(session_id, user_id)`
- Produces: `DELETE /api/sessions/{session_id}` → **204**；不存在/非本人 → **404**

- [ ] **Step 1: Write failing tests**

```python
def test_delete_session_ok_and_404(client):
    token = _token(client, "del_user")
    h = {"Authorization": f"Bearer {token}"}
    sid = client.post("/api/sessions", headers=h).json()["id"]
    assert client.delete(f"/api/sessions/{sid}", headers=h).status_code == 204
    listed = client.get("/api/sessions", headers=h).json()["sessions"]
    assert all(s["id"] != sid for s in listed)
    assert client.delete(f"/api/sessions/{sid}", headers=h).status_code == 404
    assert client.get(f"/api/sessions/{sid}/turns", headers=h).status_code == 404


def test_delete_session_other_user_404(client):
    t1 = _token(client, "del_u1")
    t2 = _token(client, "del_u2")
    sid = client.post(
        "/api/sessions", headers={"Authorization": f"Bearer {t1}"}
    ).json()["id"]
    r = client.delete(
        f"/api/sessions/{sid}",
        headers={"Authorization": f"Bearer {t2}"},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
$PY -m pytest tests/test_sessions_api.py::test_delete_session_ok_and_404 tests/test_sessions_api.py::test_delete_session_other_user_404 -v
```

Expected: FAIL（405 / 404 路由不存在）

- [ ] **Step 3: Implement DELETE**

```python
from fastapi import Response
from app.agent.memory.store import delete_session

@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_session(
    session_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    try:
        delete_session(session_id, user["id"])
    except MemoryError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
$PY -m pytest tests/test_sessions_api.py::test_delete_session_ok_and_404 tests/test_sessions_api.py::test_delete_session_other_user_404 -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

建议 message：`feat(api): DELETE /api/sessions/{id}`

---

### Task 4: Pipeline SSE `session_title`

**Files:**
- Modify: `backend/app/agent/pipeline.py`
- Modify: `backend/tests/test_session_title.py`（或现有 pipeline 测试文件追加一条）

**Interfaces:**
- Consumes: MemorySave delta `session_title`
- Produces: SSE 事件 `("session_title", {"session_id", "title"})`

- [ ] **Step 1: Write failing test**

在 `test_session_title.py` 追加（mock 整图过重时，可直接测 pipeline 内 MemorySave 分支：构造最小 graph 或 patch `iter_pipeline_events` 依赖）。推荐轻量方式——单测提取逻辑不现实则测：

```python
def test_pipeline_emits_session_title_after_memory_save(memory_user_id):
    from app.agent.pipeline import iter_pipeline_events

    sess_id = f"sess_title_{memory_user_id}"
    # 使用既有 create_session 拿真实 id
    from app.agent.memory.store import create_session

    sess = create_session(memory_user_id)
    state = {
        "question": "各渠道 GMV",
        "session_id": sess["id"],
        "user_id": memory_user_id,
        "user_role": "analyst",
        "request_id": "req_t",
        "trace_id": "tr_t",
        "need_clarification": True,
        "clarification_question": "请补充时间范围",
        "repaired": False,
        "agent_trace": [],
        "slots": {"metrics": [], "filters": {}, "group_by": [], "time_range": None},
    }
    with patch(
        "app.agent.nodes.memory_save.generate_session_title",
        return_value="需澄清",
    ), patch(
        "app.agent.pipeline.build_graph"
    ) as build:
        # 若 build_graph 难 mock：改为只断言 memory_save 返回值已被 pipeline 消费的集成路径
        ...
```

**更稳妥的最小测法**（推荐实现时采用）：在 `pipeline.py` 的 `if node == "MemorySave":` 块内，在 patch_latest_turn_display 之后增加：

```python
if merged.get("session_title"):
    yield (
        "session_title",
        {
            "session_id": str(merged.get("session_id") or ""),
            "title": str(merged["session_title"]),
        },
    )
```

并用单元测试直接调用该分支不现实时：新增 `tests/test_pipeline_session_title.py`，patch graph 使唯一节点为 MemorySave，或对 `iter_pipeline_events` 用现有 `test_graph_pipeline` 风格 fixture。

若仓库已有「跑澄清路径」的集成测，优先复用并断言事件流中含 `session_title`。

具体最小实现步骤（本 Task 以代码补丁为准）：

```python
# pipeline.py, inside `if node == "MemorySave":` after patch_latest_turn_display try/except:
title = merged.get("session_title")
if title:
    yield (
        "session_title",
        {
            "session_id": str(merged.get("session_id") or ""),
            "title": str(title)[:10],
        },
    )
```

测试（mock LLM + 强制澄清短路径）：

```python
def test_iter_pipeline_events_includes_session_title(client, memory_user_id):
    # 若无 memory_user_id 与 client 混用，用 sessions API 创建后直接调 iter_pipeline_events
    ...
```

实现者：参考 `backend/tests/test_phase6_pipeline.py` / `test_graph_pipeline.py` 的 patch 模式；断言：

```python
events = list(iter_pipeline_events(state))
assert any(e[0] == "session_title" and e[1].get("title") for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
$PY -m pytest tests/test_session_title.py -k session_title -v
```

Expected: FAIL（事件未出现）

- [ ] **Step 3: Add yield in pipeline**

按上面补丁修改 `pipeline.py`。确认 LangGraph 节点返回的 delta 会 merge 进 `merged`（现有 `memory_save` 返回 dict 会被 merge——与其它节点一致）。

- [ ] **Step 4: Run test to verify it passes**

```bash
$PY -m pytest tests/test_session_title.py -k session_title -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

建议 message：`feat(sse): emit session_title after MemorySave`

---

### Task 5: Frontend — deleteSession + 侧栏删除 + 空态示例 + session_title

**Files:**
- Modify: `frontend/src/api/sessions.ts`
- Modify: `frontend/src/pages/AppWorkbench.tsx`

**Interfaces:**
- Consumes: `DELETE /api/sessions/{id}`、SSE `session_title`
- Produces: UI 行为符合 design §5

- [ ] **Step 1: Add `deleteSession`**

```typescript
export async function deleteSession(sessionId: string): Promise<void> {
  const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error('删除会话失败')
}
```

- [ ] **Step 2: AppWorkbench — 删除**

- import `deleteSession`
- 会话列表项：主按钮切换；旁侧删除按钮 `aria-label="删除会话"`，`stopPropagation` / 独立 button
- `handleDeleteSession(sessionId)`：
  1. `if (!window.confirm('确定删除该会话？删除后不可恢复。')) return`
  2. `await deleteSession(sessionId)`
  3. 从 `sessions` 过滤掉
  4. 若 `sessionId === currentSessionId`：取剩余列表第一项并 `handleSwitchSession`；若空则 `createSession` 并设为当前、`turns=[]`
  5. 错误写入 `sideError`

- [ ] **Step 3: AppWorkbench — 标题与 SSE**

- `currentTitle = currentSession?.title || '新会话'`（不再用 `turns[0]?.question`）
- 侧栏：`session.title || '新会话'`（active 时也不再用首问兜底）
- 去掉流结束后乐观 `title: session.title || submittedQuestion.slice(0, 40)`，改为保留原 title（或仍为 null）
- 在 `onEvent` switch 增加：

```typescript
case 'session_title': {
  const title = String(data.title ?? '').slice(0, 10)
  const sid = String(data.session_id ?? currentSessionId ?? '')
  if (!title || !sid) break
  setSessions((previous) =>
    previous.map((item) =>
      item.id === sid ? { ...item, title } : item,
    ),
  )
  break
}
```

- [ ] **Step 4: AppWorkbench — 示例空态**

- 删除侧栏「示例问题」`<section>`
- 空态（`!loading && turns.length === 0`）改为：

```tsx
<div className="py-12">
  <div className="text-center">
    <p className="font-display text-xl">从一个经营问题开始</p>
    <p className="mt-2 text-sm text-muted">
      可从下方示例选择，或在底部直接输入问题。
    </p>
  </div>
  <ul className="mx-auto mt-8 max-w-xl space-y-1">
    {examples.map((example) => (
      <li key={example.id}>
        <button
          type="button"
          onClick={() => setQuestion(example.question)}
          className="w-full rounded-md px-3 py-2 text-left text-sm leading-snug text-ink transition-colors hover:bg-accent-soft hover:text-accent"
        >
          {example.question}
        </button>
      </li>
    ))}
  </ul>
</div>
```

- 点击**只** `setQuestion`，不调用提交

- [ ] **Step 5: Build / smoke**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/frontend && npm run build
```

Expected: 编译成功

- [ ] **Step 6: Commit（默认跳过）**

建议 message：`feat(ui): session delete, LLM title, empty-state examples`

---

### Task 6: 同步 docs/04 + 回归测试

**Files:**
- Modify: `docs/04-接口与前端.md`

- [ ] **Step 1: 更新文档要点**

1. SSE 表增加 `session_title`：`{session_id, title}`，MemorySave 首次写标题后推送  
2. Sessions：增加 **DELETE /api/sessions/{session_id}** → 204；404 文案  
3. 标题：首轮任意完成写入 turns 后，若 title 空则 LLM ≤10 字（失败回退截断）；删除「首问截断 ≤40」  
4. 侧栏：列表可删（确认后硬删）；空 title 占位「新会话」；**移除**侧栏示例问题描述  
5. 主区空态：展示示例；点击仅填入；有 turns 后不显示  
6. §2.4 示例位置改为「主区空态」

- [ ] **Step 2: 跑相关后端回归**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
$PY -m pytest tests/test_memory_store.py tests/test_session_title.py tests/test_sessions_api.py -v
```

Expected: PASS

- [ ] **Step 3: Commit（默认跳过）**

建议 message：`docs: session delete, title, examples UX`

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| DELETE 硬删 + 404 | Task 1, 3 |
| 确认后删除 UI + 删当前切换/新建 | Task 5 |
| title 空显示「新会话」 | Task 5 |
| 首轮任意完成 LLM ≤10 | Task 2 |
| LLM 失败回退截断 | Task 2 |
| 仅空 title 写一次 | Task 1, 2 |
| SSE `session_title` | Task 4, 5 |
| 示例移主区空态、点击仅填入 | Task 5 |
| docs/04 同步 | Task 6 |

## Self-review notes

- 无 TBD；`set_session_title_if_empty` 返回类型在 Task 1/2 一致为 `bool`
- `test_memory_save_sets_session_title` 必须随 LLM 路径更新，避免假绿
- Commit 步骤按仓库约定默认跳过
