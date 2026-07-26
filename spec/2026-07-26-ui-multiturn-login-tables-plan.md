# UI Multiturn / Login / Tables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 强化登录页 AI 叙事；将 `/app` 改为多会话 + 多轮时间线（折叠保留 SQL/结果/图表/Trace）；新增 `/app/tables` 只读分页浏览业务表。

**Architecture:** 后端扩展 Memory store + 薄 REST（sessions / tables）；前端工作台以 `turns[]` 为时间线状态机，SSE 写入当前 turn；数据表页用白名单 SELECT + LIMIT/OFFSET，复用敏感列规则。不改 Agent 推理主链路。

**Tech Stack:** Python 3.12（conda）· FastAPI · pytest · React · Vite · TypeScript · Tailwind · Recharts · react-router-dom

## Global Constraints

- 规格：`spec/2026-07-26-ui-multiturn-login-tables-design.md`；产品文档实现后同步 `docs/04-接口与前端.md`
- 配置仅用根目录 `config.yaml`；禁止 `.env`
- **Python（强制）**：`/home/user/miniconda3/envs/python3.12/bin/python`（下文 `PY`）与同目录 `pip`；禁止系统 Python / `.venv`
- **禁止** git worktree / `.worktrees/`；只在本仓库 `main` 工作区改代码
- **Git commit：仅当用户明确要求时执行**；本计划 Commit 步骤默认跳过（可汇报建议 message）
- TDD：Sessions / Tables / Memory 列表逻辑先写失败测试再实现
- 一次只改当前 Task 相关文件；不做顺手大重构
- Tables：`page_size` 固定 50；仅业务表；analyst 隐藏 `users` 敏感列
- 会话恢复：仅 `session_turns` 可用字段；不持久化完整 rows/chart/Trace

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/app/agent/memory/store.py` | `create_session` / `list_sessions` / `list_turns` / `touch_session_title` |
| `backend/app/agent/memory/__init__.py` | 导出新函数 |
| `backend/app/agent/nodes/memory_save.py` | 首轮写 title（若空） |
| `backend/app/api/sessions.py` | `GET/POST /sessions`、`GET /sessions/{id}/turns` |
| `backend/app/api/tables.py` | `GET /tables`、`GET /tables/{name}` |
| `backend/app/main.py` | 挂载 routers |
| `backend/tests/test_sessions_api.py` | Sessions API TDD |
| `backend/tests/test_tables_api.py` | Tables API TDD |
| `backend/tests/test_memory_store.py` | 增量 store 单测 |
| `frontend/src/api/sessions.ts` | sessions 客户端 |
| `frontend/src/api/tables.ts` | tables 客户端 |
| `frontend/src/components/TurnCard.tsx` | 单轮折叠卡片 |
| `frontend/src/pages/AppWorkbench.tsx` | 多会话 + 时间线工作台 |
| `frontend/src/pages/TablesPage.tsx` | 数据表浏览 |
| `frontend/src/pages/LoginPage.tsx` | 卖点 + 能力故事 |
| `frontend/src/App.tsx` | `/app/tables` 路由 |
| `docs/04-接口与前端.md` | API / 路由 / 交互同步 |

工作目录：仓库根。

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

### Task 1: Memory store — sessions / turns 查询与创建

**Files:**
- Modify: `backend/app/agent/memory/store.py`
- Modify: `backend/app/agent/memory/__init__.py`
- Modify: `backend/tests/test_memory_store.py`

**Interfaces:**
- Produces:
  - `create_session(user_id: str, session_id: str | None = None) -> dict` → `{id, title, updated_at, turn_count}`
  - `list_sessions(user_id: str) -> list[dict]` → 同上字段，按 `updated_at` 降序
  - `list_turns(session_id: str, user_id: str) -> list[dict]` → 最近 `MAX_TURNS_PER_SESSION` 轮，`turn_index` 升序；归属错误抛 `MemoryError`
  - `set_session_title_if_empty(session_id: str, user_id: str, title: str) -> None`

- [ ] **Step 1: Write failing tests**

在 `backend/tests/test_memory_store.py` 追加（沿用该文件现有 fixture / DB 初始化方式）：

```python
from app.agent.memory.store import (
    MemoryError,
    create_session,
    list_sessions,
    list_turns,
    save_turn,
    set_session_title_if_empty,
)


def test_create_and_list_sessions_ordered(memory_user_id):
    older = create_session(memory_user_id)
    newer = create_session(memory_user_id)
    sessions = list_sessions(memory_user_id)
    assert [s["id"] for s in sessions[:2]] == [newer["id"], older["id"]]
    assert sessions[0]["turn_count"] == 0
    assert sessions[0]["title"] is None


def test_list_turns_requires_owner(memory_user_id, other_user_id):
    sess = create_session(memory_user_id)
    save_turn(
        session_id=sess["id"],
        user_id=memory_user_id,
        question="q1",
        intent="sales_overview",
        sql_text="SELECT 1",
        slots={"metrics": ["gmv"], "filters": {}, "group_by": [], "time_range": None},
        result_summary="ok",
    )
    turns = list_turns(sess["id"], memory_user_id)
    assert len(turns) == 1
    assert turns[0]["question"] == "q1"
    assert turns[0]["sql_text"] == "SELECT 1"
    try:
        list_turns(sess["id"], other_user_id)
        assert False, "expected MemoryError"
    except MemoryError:
        pass


def test_set_session_title_if_empty(memory_user_id):
    sess = create_session(memory_user_id)
    set_session_title_if_empty(sess["id"], memory_user_id, "最近 30 天 GMV 趋势如何？多余会被截断")
    listed = list_sessions(memory_user_id)
    title = next(s["title"] for s in listed if s["id"] == sess["id"])
    assert title is not None
    assert len(title) <= 40
    set_session_title_if_empty(sess["id"], memory_user_id, "第二次不应覆盖")
    listed2 = list_sessions(memory_user_id)
    assert next(s["title"] for s in listed2 if s["id"] == sess["id"]) == title
```

若现有 fixture 名不同：复用 `test_memory_store.py` 里创建 `app_users` 的 helper；没有 `other_user_id` 则在测试内再插一条用户。

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
PY=/home/user/miniconda3/envs/python3.12/bin/python
$PY -m pytest tests/test_memory_store.py::test_create_and_list_sessions_ordered tests/test_memory_store.py::test_list_turns_requires_owner tests/test_memory_store.py::test_set_session_title_if_empty -v
```

Expected: FAIL（`create_session` / `list_sessions` 未定义）

- [ ] **Step 3: Implement store helpers**

在 `store.py` 增加（保持现有 `_now` / `_user_id` / `ensure_session` 风格）：

```python
import uuid

def create_session(user_id: str, session_id: str | None = None) -> dict:
    sid = str(session_id or f"sess_{uuid.uuid4().hex}")
    ensure_session(sid, user_id)
    return next(s for s in list_sessions(user_id) if s["id"] == sid)


def list_sessions(user_id: str) -> list[dict]:
    owner_id = _user_id(user_id)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.title, s.updated_at,
                   (SELECT COUNT(*) FROM session_turns t WHERE t.session_id = s.id) AS turn_count
            FROM chat_sessions s
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC, s.id DESC
            """,
            (owner_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "updated_at": row["updated_at"],
                "turn_count": int(row["turn_count"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def assert_session_owner(session_id: str, user_id: str) -> None:
    owner_id = _user_id(user_id)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT user_id FROM chat_sessions WHERE id = ?",
            (str(session_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise MemoryError("Session not found")
    if row["user_id"] != owner_id:
        raise MemoryError("Session belongs to another user")


def list_turns(session_id: str, user_id: str) -> list[dict]:
    assert_session_owner(session_id, user_id)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT turn_index, question, intent, sql_text, metrics_json,
                   time_range_json, filters_json, group_by_json,
                   result_summary, created_at
            FROM session_turns
            WHERE session_id = ?
            ORDER BY turn_index DESC
            LIMIT ?
            """,
            (str(session_id), MAX_TURNS_PER_SESSION),
        ).fetchall()
        ordered = list(reversed(rows))
        return [
            {
                "turn_index": row["turn_index"],
                "question": row["question"],
                "intent": row["intent"],
                "sql_text": row["sql_text"],
                "metrics": json.loads(row["metrics_json"] or "[]"),
                "time_range": json.loads(row["time_range_json"] or "null"),
                "filters": json.loads(row["filters_json"] or "{}"),
                "group_by": json.loads(row["group_by_json"] or "[]"),
                "result_summary": row["result_summary"],
                "created_at": row["created_at"],
            }
            for row in ordered
        ]
    finally:
        conn.close()


def set_session_title_if_empty(session_id: str, user_id: str, title: str) -> None:
    assert_session_owner(session_id, user_id)
    clipped = strip_sensitive(title).strip()[:40]
    if not clipped:
        return
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE chat_sessions
            SET title = COALESCE(NULLIF(title, ''), ?),
                updated_at = ?
            WHERE id = ?
            """,
            (clipped, _now(), str(session_id)),
        )
        # 若 title 已有值，仍应刷新 updated_at（追问时）——拆成两步更清晰：
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
            (_now(), str(session_id)),
        )
        conn.commit()
    finally:
        conn.close()
```

修正 `set_session_title_if_empty` 语义（与测试一致）：**仅当 title 为空时写入**；`updated_at` 每次调用都刷新。实现示例：

```python
def set_session_title_if_empty(session_id: str, user_id: str, title: str) -> None:
    assert_session_owner(session_id, user_id)
    clipped = strip_sensitive(title).strip()[:40]
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title FROM chat_sessions WHERE id = ?",
            (str(session_id),),
        ).fetchone()
        new_title = row["title"] if row and row["title"] else (clipped or None)
        conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (new_title, _now(), str(session_id)),
        )
        conn.commit()
    finally:
        conn.close()
```

同时在 `save_turn` 末尾（commit 前）刷新 `chat_sessions.updated_at`：

```python
conn.execute(
    "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
    (_now(), str(session_id)),
)
```

导出：更新 `memory/__init__.py` 的 `__all__`。

- [ ] **Step 4: Run tests — expect PASS**

```bash
$PY -m pytest tests/test_memory_store.py::test_create_and_list_sessions_ordered tests/test_memory_store.py::test_list_turns_requires_owner tests/test_memory_store.py::test_set_session_title_if_empty -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

建议 message：`feat(memory): list/create sessions and turns for UI`

---

### Task 2: Sessions REST API + 首轮 title 回写

**Files:**
- Create: `backend/app/api/sessions.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/agent/nodes/memory_save.py`（首轮调用 `set_session_title_if_empty`）
- Create: `backend/tests/test_sessions_api.py`

**Interfaces:**
- Produces HTTP:
  - `GET /api/sessions` → `{sessions: [...]}`
  - `POST /api/sessions` → session dict
  - `GET /api/sessions/{session_id}/turns` → `{session_id, turns}`；不存在/非主人 → 404

- [ ] **Step 1: Write failing API tests**

```python
# backend/tests/test_sessions_api.py
import importlib
import pytest
from fastapi.testclient import TestClient
from app.db.init_db import init_database


@pytest.fixture()
def client(tmp_db_path):
    init_database(reset=True)
    from app.config import get_settings
    get_settings.cache_clear()
    import app.main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _token(client, username="alice"):
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123", "role": "analyst"},
    )
    return r.json()["access_token"]


def test_sessions_require_auth(client):
    assert client.get("/api/sessions").status_code == 401


def test_create_list_and_turns(client):
    token = _token(client)
    h = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/sessions", headers=h)
    assert created.status_code == 200
    sid = created.json()["id"]
    assert sid.startswith("sess_")

    listed = client.get("/api/sessions", headers=h)
    assert listed.status_code == 200
    assert any(s["id"] == sid for s in listed.json()["sessions"])

    turns = client.get(f"/api/sessions/{sid}/turns", headers=h)
    assert turns.status_code == 200
    assert turns.json()["turns"] == []

    # 经 chat 写一轮后 turns 非空 + title 回写：若本地 chat 过重，可直接调 store.save_turn + set_session_title
    from app.auth.deps import get_current_user
    # 更轻：用 store
    me = client.get("/api/auth/me", headers=h).json()
    from app.agent.memory.store import save_turn, set_session_title_if_empty
    save_turn(
        session_id=sid,
        user_id=str(me["id"]),
        question="上个月 GMV 最高的 5 个渠道是什么？",
        intent="channel_sales",
        sql_text="SELECT 1",
        slots={"metrics": ["gmv"], "filters": {}, "group_by": ["channel"], "time_range": None},
        result_summary="top channels",
    )
    set_session_title_if_empty(sid, str(me["id"]), "上个月 GMV 最高的 5 个渠道是什么？")
    turns2 = client.get(f"/api/sessions/{sid}/turns", headers=h).json()["turns"]
    assert len(turns2) == 1
    assert "GMV" in turns2[0]["question"]
    sessions = client.get("/api/sessions", headers=h).json()["sessions"]
    mine = next(s for s in sessions if s["id"] == sid)
    assert mine["title"]
    assert mine["turn_count"] == 1


def test_turns_other_user_404(client):
    t1 = _token(client, "u1")
    t2 = _token(client, "u2")
    sid = client.post("/api/sessions", headers={"Authorization": f"Bearer {t1}"}).json()["id"]
    r = client.get(
        f"/api/sessions/{sid}/turns",
        headers={"Authorization": f"Bearer {t2}"},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run — expect FAIL**

```bash
$PY -m pytest tests/test_sessions_api.py -v
```

Expected: FAIL（404/路由不存在）

- [ ] **Step 3: Implement API + wire + memory_save title**

`backend/app/api/sessions.py`:

```python
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from app.auth.deps import get_current_user
from app.agent.memory.store import (
    MemoryError,
    create_session,
    list_sessions,
    list_turns,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
def get_sessions(user: Annotated[dict, Depends(get_current_user)]):
    return {"sessions": list_sessions(user["id"])}


@router.post("")
def post_session(user: Annotated[dict, Depends(get_current_user)]):
    return create_session(user["id"])


@router.get("/{session_id}/turns")
def get_turns(session_id: str, user: Annotated[dict, Depends(get_current_user)]):
    try:
        turns = list_turns(session_id, user["id"])
    except MemoryError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return {"session_id": session_id, "turns": turns}
```

`main.py`：`include_router(sessions_router, prefix="/api")`。

在 `memory_save` 节点成功 `save_turn` 之后调用：

```python
from app.agent.memory.store import set_session_title_if_empty
set_session_title_if_empty(session_id, user_id, question)
```

- [ ] **Step 4: Run — expect PASS**

```bash
$PY -m pytest tests/test_sessions_api.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

建议 message：`feat(api): add sessions list/create/turns endpoints`

---

### Task 3: Tables REST API（概览 + 分页行）

**Files:**
- Create: `backend/app/api/tables.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_tables_api.py`

**Interfaces:**
- Produces:
  - `GET /api/tables` → `{tables: [{name, column_count, row_count}]}`
  - `GET /api/tables/{name}?page=1&page_size=50` → `{name, columns, page, page_size: 50, total_rows, rows}`
  - 非业务表 / 应用表 → 404；`page_size` 恒为 50

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_tables_api.py
import importlib
import pytest
from fastapi.testclient import TestClient
from app.db.init_db import init_database
from app.db.schema import BUSINESS_TABLES


@pytest.fixture()
def client(tmp_db_path):
    init_database(reset=True)
    from app.config import get_settings
    get_settings.cache_clear()
    import app.main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c


def _token(client, username="alice", role="analyst", invite=None):
    body = {"username": username, "password": "password123", "role": role}
    if role == "admin":
        body["invite_code"] = invite or "test-invite"
    return client.post("/api/auth/register", json=body).json()["access_token"]


def test_tables_require_auth(client):
    assert client.get("/api/tables").status_code == 401


def test_list_business_tables(client):
    token = _token(client)
    r = client.get("/api/tables", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tables"]}
    assert names == set(BUSINESS_TABLES)
    orders = next(t for t in r.json()["tables"] if t["name"] == "orders")
    assert orders["row_count"] > 0
    assert orders["column_count"] > 0


def test_table_rows_page_size_fixed_and_analyst_hides_sensitive(client):
    token = _token(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/tables/users?page=1&page_size=999", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["page_size"] == 50
    assert len(body["rows"]) <= 50
    cols = {c["name"] for c in body["columns"]}
    assert cols.isdisjoint({"name", "phone", "email", "id_card"})
    for row in body["rows"]:
        assert set(row).isdisjoint({"name", "phone", "email", "id_card"})


def test_admin_sees_sensitive_columns(client):
    token = _token(client, "admin1", "admin")
    r = client.get(
        "/api/tables/users?page=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    cols = {c["name"] for c in r.json()["columns"]}
    assert {"name", "phone", "email", "id_card"}.issubset(cols)


def test_app_table_404(client):
    token = _token(client)
    r = client.get(
        "/api/tables/app_users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


def test_unknown_table_404(client):
    token = _token(client)
    r = client.get(
        "/api/tables/not_a_table",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run — expect FAIL**

```bash
$PY -m pytest tests/test_tables_api.py -v
```

- [ ] **Step 3: Implement `tables.py`**

```python
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.auth.deps import get_current_user
from app.db.database import get_connection
from app.db.schema import BUSINESS_TABLES, SENSITIVE_USER_COLUMNS

router = APIRouter(prefix="/tables", tags=["tables"])
PAGE_SIZE = 50


def _columns_for(conn, name: str, role: str) -> list[dict]:
    cols = []
    for _cid, cname, ctype, notnull, _dflt, pk in conn.execute(f"PRAGMA table_info({name})"):
        if role == "analyst" and name == "users" and cname in SENSITIVE_USER_COLUMNS:
            continue
        cols.append({
            "name": cname,
            "type": ctype or "TEXT",
            "nullable": not bool(notnull) and not bool(pk),
        })
    return cols


@router.get("")
def list_tables(user: Annotated[dict, Depends(get_current_user)]):
    conn = get_connection()
    try:
        out = []
        for name in sorted(BUSINESS_TABLES):
            cols = _columns_for(conn, name, user["role"])
            total = conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"]
            out.append({"name": name, "column_count": len(cols), "row_count": int(total)})
        return {"tables": out}
    finally:
        conn.close()


@router.get("/{name}")
def get_table(
    name: str,
    user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE),
):
    if name not in BUSINESS_TABLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    page_size = PAGE_SIZE
    conn = get_connection()
    try:
        columns = _columns_for(conn, name, user["role"])
        col_names = [c["name"] for c in columns]
        if not col_names:
            raise HTTPException(status_code=404, detail="Table not found")
        quoted = ", ".join(f'"{c}"' for c in col_names)
        total = conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f'SELECT {quoted} FROM "{name}" LIMIT ? OFFSET ?',
            (page_size, offset),
        ).fetchall()
        return {
            "name": name,
            "columns": columns,
            "page": page,
            "page_size": page_size,
            "total_rows": int(total),
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()
```

注意：表名来自白名单常量，禁止把用户输入拼进未校验标识符；`name in BUSINESS_TABLES` 已保证。

挂载到 `main.py`。

- [ ] **Step 4: Run — expect PASS**

```bash
$PY -m pytest tests/test_tables_api.py -v
```

- [ ] **Step 5: Commit（默认跳过）**

建议 message：`feat(api): add paginated business table browser`

---

### Task 4: 登录页 AI 亮点（首屏 + 能力故事）

**Files:**
- Modify: `frontend/src/pages/LoginPage.tsx`
- Modify: `frontend/src/index.css`（仅当需要额外动画 utility；尽量复用现有）

**Interfaces:**
- 无后端依赖；视觉延续现有 CSS 变量（`--accent` 等）

- [ ] **Step 1: 增强首屏卖点区**

在品牌标题与 QueryTicker 之间插入四宫格（文案固定）：

1. NL → 安全 SQL — 权限校验 + 沙箱执行  
2. 双路径编排 — ReAct / Coordinator  
3. SSE Trace — 节点过程可观测  
4. 多轮记忆 — Session 槽位追问  

保留现有表单、demo 账号、注册角色逻辑。品牌名仍为第一视口主信号。

- [ ] **Step 2: 在 `main` 闭合后增加能力故事 section**

三段：理解 / 执行 / 交付（文案对齐 design §4.1）。使用现有 `border-line` / `bg-surface`，避免新紫色主题。

- [ ] **Step 3: 视觉自检**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/frontend
npm run build
```

Expected: build 成功。手动打开 `/`：第一视口见品牌+四卖点+表单；下滚见三段故事。

- [ ] **Step 4: Commit（默认跳过）**

建议 message：`feat(ui): enrich login page with AI highlights`

---

### Task 5: 前端 sessions/tables API 客户端

**Files:**
- Create: `frontend/src/api/sessions.ts`
- Create: `frontend/src/api/tables.ts`

**Interfaces:**
- Produces:
  - `listSessions(): Promise<SessionSummary[]>`
  - `createSession(): Promise<SessionSummary>`
  - `listSessionTurns(sessionId: string): Promise<SessionTurn[]>`
  - `listTables(): Promise<TableSummary[]>`
  - `getTablePage(name: string, page: number): Promise<TablePage>`

- [ ] **Step 1: Implement clients using `apiFetch`**

```typescript
// frontend/src/api/sessions.ts
import { apiFetch } from './client'

export interface SessionSummary {
  id: string
  title: string | null
  updated_at: string
  turn_count: number
}

export interface SessionTurn {
  turn_index: number
  question: string
  intent: string | null
  sql_text: string | null
  result_summary: string | null
  metrics: unknown[]
  time_range: unknown
  filters: Record<string, unknown>
  group_by: unknown[]
  created_at: string
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await apiFetch('/api/sessions')
  if (!res.ok) throw new Error('加载会话失败')
  const data = await res.json()
  return data.sessions ?? []
}

export async function createSession(): Promise<SessionSummary> {
  const res = await apiFetch('/api/sessions', { method: 'POST' })
  if (!res.ok) throw new Error('创建会话失败')
  return res.json()
}

export async function listSessionTurns(sessionId: string): Promise<SessionTurn[]> {
  const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns`)
  if (!res.ok) throw new Error('加载会话轮次失败')
  const data = await res.json()
  return data.turns ?? []
}
```

```typescript
// frontend/src/api/tables.ts
import { apiFetch } from './client'

export interface TableSummary {
  name: string
  column_count: number
  row_count: number
}

export interface TablePage {
  name: string
  columns: { name: string; type: string; nullable: boolean }[]
  page: number
  page_size: number
  total_rows: number
  rows: Record<string, unknown>[]
}

export async function listTables(): Promise<TableSummary[]> {
  const res = await apiFetch('/api/tables')
  if (!res.ok) throw new Error('加载数据表失败')
  return (await res.json()).tables ?? []
}

export async function getTablePage(name: string, page: number): Promise<TablePage> {
  const res = await apiFetch(
    `/api/tables/${encodeURIComponent(name)}?page=${page}&page_size=50`,
  )
  if (!res.ok) throw new Error('加载表数据失败')
  return res.json()
}
```

- [ ] **Step 2: `npm run build` 确认类型通过**

- [ ] **Step 3: Commit（默认跳过）**

建议 message：`feat(frontend): add sessions and tables API clients`

---

### Task 6: `TurnCard` + 工作台多轮时间线 / 多会话

**Files:**
- Create: `frontend/src/components/TurnCard.tsx`
- Modify: `frontend/src/pages/AppWorkbench.tsx`（大幅改写，保持 SSE 事件语义）

**Interfaces:**
- Consumes: `listSessions` / `createSession` / `listSessionTurns` / `streamChat`
- Produces UI：侧栏会话列表；`turns: TurnView[]`；底部输入；跳转 `/app/tables`

`TurnView` 建议字段：

```typescript
type TraceEntry = { id: number; event: string; summary: string }
type TurnView = {
  id: string
  question: string
  answer: string
  sql: string
  sqlRepaired: boolean
  guardrailPassed: boolean
  columns: string[]
  rows: Record<string, unknown>[]
  chart: ChartConfig | null
  writeResult: { affected_rows: number | null; sql: string } | null
  trace: TraceEntry[]
  error: string | null
  errorTraceId: string | null
  clarificationHint: string | null
  latencyMs: number | null
  streaming: boolean
  fromHistory: boolean
  open: { sql: boolean; rows: boolean; chart: boolean; trace: boolean }
}
```

- [ ] **Step 1: 实现 `TurnCard`**

- 用户问题右对齐气泡  
- Agent 卡片：结论默认展开  
- chips 切换 `open.*`；无数据时不渲染对应 chip（历史轮无 rows/chart/trace）  
- 复用现有 SQL 徽章文案、`ResultChart`、`formatCell`、写操作提示、错误区  

- [ ] **Step 2: 重写 `AppWorkbench` 状态机**

关键行为：

1. mount：`listSessions()`；若空则 `createSession()`；选中最新；`listSessionTurns` → map 为 `fromHistory: true` 的 turns（`answer = result_summary ?? ''`，`sql = sql_text ?? ''`）  
2. 新建会话：`createSession` → 设当前 → `turns = []`  
3. 切换会话：切换 id → 拉取 turns（摘要级）；abort 进行中的 SSE  
4. 提交：`append` 新 turn（streaming=true，默认 `open.sql=true`）；**不要** `resetResult` 清空历史；`streamChat({ sessionId: currentSessionId, ... })` 的 `onEvent` 更新**当前 turn**（用函数式 `setTurns`）  
5. 示例问题：只 `setQuestion`  
6. 侧栏：去掉表列表；底部按钮 `navigate('/app/tables')`  
7. 输入区沉底（`flex` 主列 + 底部 form）

SSE 事件映射：把原先对各 `useState` 的赋值改为更新 `turns[activeIndex]` 对应字段；`pushTrace` 写入该 turn 的 `trace`。

- [ ] **Step 3: Build + 手动验收**

```bash
npm run build
```

手动：同会话追问两轮，第一轮卡片仍在；折叠 SQL/结果/图表/Trace；新建会话时间线空；切换回旧会话见摘要级历史。

- [ ] **Step 4: Commit（默认跳过）**

建议 message：`feat(ui): multiturn timeline workbench with sessions`

---

### Task 7: `TablesPage` + 路由

**Files:**
- Create: `frontend/src/pages/TablesPage.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `listTables` / `getTablePage`
- Route: `/app/tables` 包在 `ProtectedRoute` 内

- [ ] **Step 1: 实现页面**

- 顶部「返回工作台」→ `/app`  
- 未选表：展示全部业务表（name / column_count / row_count），点击选中  
- 选中表：可折叠字段列表；表格；分页（上一页/下一页）；`page_size` 展示 50  
- 加载/错误态简洁文案  

- [ ] **Step 2: 注册路由**

```tsx
<Route
  path="/app/tables"
  element={
    <ProtectedRoute>
      <TablesPage />
    </ProtectedRoute>
  }
/>
```

- [ ] **Step 3: `npm run build` + 手动点开分页**

- [ ] **Step 4: Commit（默认跳过）**

建议 message：`feat(ui): add business tables browser page`

---

### Task 8: 同步 `docs/04-接口与前端.md`

**Files:**
- Modify: `docs/04-接口与前端.md`

- [ ] **Step 1: 更新路由表**

增加 `/app/tables`。

- [ ] **Step 2: 重写 §2.1–2.4 与新增 API 小节**

按 design：登录卖点/故事；工作台会话列表 + 时间线 chips；侧栏改为「查看全部数据表」按钮；文档化：

- `GET/POST /api/sessions`
- `GET /api/sessions/{session_id}/turns`
- `GET /api/tables`
- `GET /api/tables/{name}`

- [ ] **Step 3: 对照 design §8 验收标准逐条自检，向用户汇报**

- [ ] **Step 4: Commit（默认跳过）**

建议 message：`docs: sync frontend multiturn and tables APIs`

---

## Spec Coverage Checklist

| Design 要求 | Task |
|-------------|------|
| 登录首屏 4 卖点 + ticker | 4 |
| 登录下方能力故事 | 4 |
| 多轮时间线 + chips | 6 |
| 多会话新建/切换 | 1,2,5,6 |
| session_turns 摘要恢复 | 1,2,6 |
| 不持久化 rows/chart/Trace | 6（非目标，不实现） |
| `/app` 去掉完整表列表，按钮跳转 | 6,7 |
| `/app/tables` 概览 + 50/页 | 3,5,7 |
| analyst 敏感列 | 3 |
| docs/04 同步 | 8 |
| 既有 SSE 行为不回退 | 6 |

## Self-Review Notes

- 无 TBD/TODO 占位  
- `assert_session_owner` 与 `ensure_session` 分离，避免 turns API 误创建会话  
- `page_size` 恒 50，与 design 一致  
- Commit 步骤默认跳过（仓库 AGENTS.md）

---

## Execution Handoff

Plan complete and saved to `spec/2026-07-26-ui-multiturn-login-tables-plan.md`.

**Two execution options:**

1. **Subagent-Driven（推荐）** — 每 Task 新开子代理，Task 间评审  
2. **Inline Execution** — 本会话按 executing-plans 连续执行并设检查点  

Which approach?
