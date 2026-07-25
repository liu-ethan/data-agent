# Phase 2 鉴权 + 基础查询闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 JWT 注册/登录、鉴权 schema/examples、经最小 Guardrail 的 SSE chat 闭环，以及浅白色主题的营销登录页与可用工作台。

**Architecture:** `auth/` 管账号与 JWT；`security/sql_guardrail.py` 做默认路径唯一 SQL 校验；`agent/pipeline.py` 线性编排（生成 SQL → Guardrail → 执行 → 结论）并通过 `api/chat.py` 推 SSE；前端 React Router：`/` 登录注册、`/app` 工作台。

**Tech Stack:** Python 3.12（conda `python3.12`）· FastAPI · PyJWT · passlib/bcrypt · openai · pytest · httpx · React · Vite · TypeScript · Tailwind · react-router-dom

## Global Constraints

- 规格：`spec/2026-07-25-phase2-auth-chat-design.md`；产品文档：`docs/04`、`docs/06` Phase 2、`docs/03` Guardrail 子集
- 默认 chat 路径 SQL **必须**经 Guardrail；禁止默认可运行旁路直连
- 不上 LangGraph / Tool Registry / 完整沙箱 / audit.jsonl / 图表
- Phase 2 仅 `SELECT`/`WITH`（含 admin）
- 配置仅用根目录 `config.yaml`；禁止 `.env` 作为运行配置
- **Python（强制）**：`/home/user/miniconda3/envs/python3.12/bin/python` 与同目录 `pip`；下文记为 `PY` / `PIP`
- **Git commit：仅当用户明确要求时执行**；本计划 Commit 步骤默认跳过
- TDD：auth、Guardrail、鉴权、chat（mock LLM）先写失败测试再实现
- 前端：**浅白色主题**；实现营销页时遵循 `frontend-design` skill

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/requirements.txt` | 增加 PyJWT、passlib、bcrypt、openai |
| `backend/app/db/schema.py` | 导出 `SENSITIVE_USER_COLUMNS` |
| `backend/app/auth/passwords.py` | bcrypt hash/verify |
| `backend/app/auth/jwt.py` | 签发/解析 JWT |
| `backend/app/auth/deps.py` | `get_current_user` |
| `backend/app/auth/routes.py` | register / login / me |
| `backend/app/security/sql_guardrail.py` | 最小只读校验 |
| `backend/app/agent/state.py` | 轻量 AgentState |
| `backend/app/agent/llm.py` | OpenAI-compatible 客户端 |
| `backend/app/agent/sql_generator.py` | NL → SQL |
| `backend/app/agent/sql_executor.py` | Guardrail 后执行 |
| `backend/app/agent/answer_composer.py` | 结论 |
| `backend/app/agent/pipeline.py` | 线性编排 + 事件 |
| `backend/app/api/schema.py` | 鉴权 + analyst 过滤 |
| `backend/app/api/examples.py` | 示例问题 |
| `backend/app/api/chat.py` | SSE chat |
| `backend/app/db/init_db.py` | demo_analyst 真实 bcrypt |
| `backend/app/main.py` | 挂载路由 |
| `backend/tests/test_auth.py` | 鉴权测试 |
| `backend/tests/test_schema_api.py` | 更新为需登录 |
| `backend/tests/test_sql_guardrail.py` | Guardrail |
| `backend/tests/test_chat_api.py` | SSE chat（mock LLM） |
| `frontend/src/pages/*` | LoginPage / AppWorkbench |
| `frontend/src/auth/*` | token + ProtectedRoute |
| `frontend/src/api/*` | HTTP / SSE |
| `docs/04-接口与前端.md` | 去掉 Phase 1 schema 例外 |
| `README.md` | 登录/邀请码/demo 账号 |

---

### Task 1: 依赖 + 敏感列常量 + 密码 / JWT 工具

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/db/schema.py`
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/auth/passwords.py`
- Create: `backend/app/auth/jwt.py`
- Create: `backend/tests/test_auth_crypto.py`

**Interfaces:**
- Produces: `SENSITIVE_USER_COLUMNS: frozenset[str] = frozenset({"name","phone","email","id_card"})`
- Produces: `hash_password(password: str) -> str`, `verify_password(password: str, password_hash: str) -> bool`
- Produces: `create_access_token(*, user_id: str, username: str, role: str) -> str`, `decode_access_token(token: str) -> dict`（含 `sub`/`username`/`role`；无效抛异常）

- [ ] **Step 1: 更新依赖**

在 `backend/requirements.txt` 追加：

```text
PyJWT>=2.9.0
passlib[bcrypt]>=1.7.4
bcrypt>=4.0.0,<4.1.0
openai>=1.50.0
```

（`bcrypt` 版本上限避免 passlib 兼容问题；若安装后 verify 异常再微调。）

Run: `PIP=/home/user/miniconda3/envs/python3.12/bin/pip && $PIP install -r backend/requirements.txt`

- [ ] **Step 2: 写失败测试 `backend/tests/test_auth_crypto.py`**

```python
from app.auth.passwords import hash_password, verify_password
from app.auth.jwt import create_access_token, decode_access_token
from app.config import get_settings


def test_password_hash_roundtrip(tmp_db_path):
    get_settings.cache_clear()
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip(tmp_db_path):
    get_settings.cache_clear()
    token = create_access_token(user_id="1", username="alice", role="analyst")
    payload = decode_access_token(token)
    assert payload["sub"] == "1"
    assert payload["username"] == "alice"
    assert payload["role"] == "analyst"
```

- [ ] **Step 3: Run 确认失败**

Run: `cd backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_auth_crypto.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 4: 实现常量与模块**

`schema.py` 在 `APP_TABLES` 附近增加：

```python
SENSITIVE_USER_COLUMNS: frozenset[str] = frozenset(
    {"name", "phone", "email", "id_card"}
)
```

`auth/__init__.py` — 空。

`auth/passwords.py`:

```python
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd.verify(password, password_hash)
```

`auth/jwt.py`:

```python
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7


def create_access_token(*, user_id: str, username: str, role: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(days=TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
```

- [ ] **Step 5: Run 确认通过**

Run: `cd backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_auth_crypto.py -v`  
Expected: PASS

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 2: Auth 路由（register / login / me）+ demo 用户哈希

**Files:**
- Create: `backend/app/auth/deps.py`
- Create: `backend/app/auth/routes.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/db/init_db.py`

**Interfaces:**
- Produces: `get_current_user(authorization) -> dict` with keys `id` (str), `username`, `role`
- Produces: routes under `/api/auth/*`
- Produces: seed user `demo_analyst` / password `demo1234`（bcrypt）

- [ ] **Step 1: 写失败测试 `backend/tests/test_auth.py`**

```python
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


def test_register_analyst_returns_jwt(client):
    r = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "password123",
            "role": "analyst",
            "invite_code": None,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "analyst"


def test_register_admin_requires_invite(client):
    bad = client.post(
        "/api/auth/register",
        json={
            "username": "boss",
            "password": "password123",
            "role": "admin",
            "invite_code": "wrong",
        },
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/register",
        json={
            "username": "boss",
            "password": "password123",
            "role": "admin",
            "invite_code": "test-invite",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["user"]["role"] == "admin"


def test_login_and_me(client):
    client.post(
        "/api/auth/register",
        json={
            "username": "bob",
            "password": "password123",
            "role": "analyst",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "password123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "bob"
    assert client.get("/api/auth/me").status_code == 401


def test_demo_analyst_login(client):
    r = client.post(
        "/api/auth/login",
        json={"username": "demo_analyst", "password": "demo1234"},
    )
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "analyst"
```

- [ ] **Step 2: Run 确认失败**

Run: `cd backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_auth.py -v`  
Expected: FAIL

- [ ] **Step 3: 实现 deps + routes**

`deps.py`:

```python
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import decode_access_token
from app.db.database import get_connection

security = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_access_token(creds.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    user_id = payload.get("sub")
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, username, role FROM app_users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return {"id": str(row["id"]), "username": row["username"], "role": row["role"]}
```

`routes.py`：Pydantic 模型 `RegisterBody`（username, password, role, invite_code optional）、`LoginBody`；register 校验角色与邀请码；插入 `app_users`（id 用 `MAX(id)+1` 或 sqlite 自增若 DDL 支持——当前 DDL 为手工 id，用 `SELECT COALESCE(MAX(id),0)+1`）；返回 token。login 用 `verify_password`。me 返回 `get_current_user`。

Router: `APIRouter(prefix="/auth", tags=["auth"])`。

- [ ] **Step 4: 挂载 main + 修复 init_db demo 哈希**

`main.py`：

```python
from app.auth.routes import router as auth_router
# ...
application.include_router(auth_router, prefix="/api")
```

`init_db.py`：将 `"phase2-placeholder"` 替换为 `hash_password("demo1234")`（导入 `app.auth.passwords.hash_password`）。

- [ ] **Step 5: Run 确认通过**

Run: `cd backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_auth.py -v`  
Expected: PASS

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 3: Schema 鉴权 + analyst 敏感列过滤 + Examples API

**Files:**
- Modify: `backend/app/api/schema.py`
- Modify: `backend/tests/test_schema_api.py`
- Create: `backend/app/api/examples.py`
- Create: `backend/tests/test_examples_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `get_current_user`, `SENSITIVE_USER_COLUMNS`
- Produces: schema 需 Bearer；analyst 的 `users` 列不含敏感名
- Produces: `GET /api/examples` → `{ "examples": [ {"id": str, "question": str}, ... ] }` 长度 ≥ 15

- [ ] **Step 1: 改写 `test_schema_api.py` 为需登录**

```python
def _token(client, username="alice", role="analyst", invite=None):
    body = {"username": username, "password": "password123", "role": role}
    if role == "admin":
        body["invite_code"] = invite or "test-invite"
    r = client.post("/api/auth/register", json=body)
    return r.json()["access_token"]


def test_schema_requires_auth(client):
    assert client.get("/api/schema").status_code == 401


def test_schema_analyst_hides_sensitive_columns(client):
    token = _token(client, "alice", "analyst")
    r = client.get("/api/schema", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    users = next(t for t in r.json()["tables"] if t["name"] == "users")
    cols = {c["name"] for c in users["columns"]}
    assert "city" in cols
    assert cols.isdisjoint({"name", "phone", "email", "id_card"})


def test_schema_admin_sees_sensitive_columns(client):
    token = _token(client, "admin1", "admin")
    r = client.get("/api/schema", headers={"Authorization": f"Bearer {token}"})
    users = next(t for t in r.json()["tables"] if t["name"] == "users")
    cols = {c["name"] for c in users["columns"]}
    assert {"name", "phone", "email", "id_card"}.issubset(cols)
```

保留「仅业务表」断言（带 token）。

- [ ] **Step 2: Run 确认失败（401 或未过滤）**

- [ ] **Step 3: 更新 `schema.py`**

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from app.auth.deps import get_current_user
from app.db.schema import BUSINESS_TABLES, SENSITIVE_USER_COLUMNS
# ...

@router.get("/schema")
def get_schema(user: Annotated[dict, Depends(get_current_user)]):
    # 构建 tables；若 user["role"] == "analyst" and name == "users":
    #   过滤 cname not in SENSITIVE_USER_COLUMNS
```

- [ ] **Step 4: 实现 examples**

`examples.py`：硬编码 `docs/04` 的 15 条问题；路由需 `get_current_user`。

`test_examples_api.py`：未登录 401；登录后 `len(examples) >= 15`。

`main.py` 挂载 examples router。

- [ ] **Step 5: Run 全部相关测试 PASS**

Run: `cd backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_schema_api.py tests/test_examples_api.py -v`

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 4: 最小 SQL Guardrail

**Files:**
- Create: `backend/app/security/__init__.py`
- Create: `backend/app/security/sql_guardrail.py`
- Create: `backend/tests/test_sql_guardrail.py`

**Interfaces:**
- Produces: `class GuardrailResult: ok: bool; reason: str | None`
- Produces: `check_sql(sql: str, *, user_role: str) -> GuardrailResult`

规则（实现用 sql 大写规范化后检查）：

1. strip 后空 → 拒绝  
2. 去掉字符串字面量后再找 `;` → 多语句拒绝  
3. 必须以 `SELECT` 或 `WITH` 开头（允许前导 `--` 单行注释剥离）  
4. 禁词：`DROP` `ALTER` `TRUNCATE` `CREATE` `ATTACH` `DETACH` `INSERT` `UPDATE` `DELETE` `REPLACE` `PRAGMA`（Phase 2 只读）  
5. 出现任一 `APP_TABLES` 名或 `sqlite_master` → 拒绝  
6. `user_role == "analyst"`：若匹配 `(?i)\busers\.(name|phone|email|id_card)\b` 或（SQL 含 `\busers\b` 且含裸敏感列作选择列表——**最低要求**：限定名匹配必须拦；裸列名可用额外简单启发式：`SELECT` 列表中出现敏感列名且 FROM/JOIN 含 `users`）

- [ ] **Step 1: 写失败测试**

```python
from app.security.sql_guardrail import check_sql


def test_allows_simple_select():
    r = check_sql("SELECT channel, SUM(pay_amount) FROM orders GROUP BY channel", user_role="analyst")
    assert r.ok


def test_rejects_multi_statement():
    assert not check_sql("SELECT 1; SELECT 2", user_role="analyst").ok


def test_rejects_ddl():
    assert not check_sql("DROP TABLE orders", user_role="admin").ok


def test_rejects_app_table():
    assert not check_sql("SELECT * FROM app_users", user_role="admin").ok


def test_rejects_analyst_sensitive():
    assert not check_sql("SELECT users.name FROM users", user_role="analyst").ok


def test_admin_can_select_sensitive():
    assert check_sql("SELECT users.name FROM users LIMIT 10", user_role="admin").ok
```

- [ ] **Step 2: Run 确认失败 → 实现 `check_sql` → Run PASS**

- [ ] **Step 3: Commit（默认跳过）**

---

### Task 5: Agent 管线（LLM / 生成 / 执行 / 结论）

**Files:**
- Create: `backend/app/agent/__init__.py`
- Create: `backend/app/agent/state.py`
- Create: `backend/app/agent/llm.py`
- Create: `backend/app/agent/sql_generator.py`
- Create: `backend/app/agent/sql_executor.py`
- Create: `backend/app/agent/answer_composer.py`
- Create: `backend/app/agent/pipeline.py`
- Create: `backend/tests/test_sql_executor.py`

**Interfaces:**
- Produces: `AgentState` dataclass/TypedDict（见 design §6.1）
- Produces: `chat_completion(messages: list[dict], *, temperature: float = 0) -> str`
- Produces: `generate_sql(question, schema_tables, user_role) -> str`（抽 SQL，去 markdown fence）
- Produces: `execute_sql(sql, *, user_role) -> tuple[list[str], list[dict]]` — **内部先 `check_sql`，失败抛 `PermissionError` 或自定义 `GuardrailError`**
- Produces: `compose_answer(question, columns, rows) -> str`
- Produces: `iter_pipeline_events(state) -> Iterator[tuple[str, dict]]` 按 design 顺序 yield `(event, data)`

- [ ] **Step 1: 写 executor 测试（不依赖真 LLM）**

```python
import pytest
from app.db.init_db import init_database
from app.agent.sql_executor import execute_sql, GuardrailError


def test_execute_runs_after_guardrail(tmp_db_path):
    init_database(reset=True)
    cols, rows = execute_sql(
        "SELECT COUNT(*) AS c FROM orders",
        user_role="analyst",
    )
    assert "c" in cols
    assert rows[0]["c"] > 0


def test_execute_blocked_by_guardrail(tmp_db_path):
    init_database(reset=True)
    with pytest.raises(GuardrailError):
        execute_sql("SELECT * FROM app_users", user_role="admin")
```

- [ ] **Step 2: 实现各模块**

要点：

- `llm.py`：`from openai import OpenAI`；`base_url`/`api_key`/`model` 来自 settings；空 api_key 时抛清晰错误
- `sql_generator.py`：把 schema JSON 塞进 system prompt；user 为 question；解析回复中的 SQL
- `sql_executor.py`：`check_sql` → 若 SELECT 无 LIMIT 则包一层 `SELECT * FROM ({sql}) LIMIT 100`（或字符串追加，选一种并保持简单）→ `get_connection()` → fetch
- `answer_composer.py`：优先再调 LLM；失败则 `f"查询返回 {len(rows)} 行。"` 
- `pipeline.py`：按节点 emit `node_start`/`node_end`/`sql`/`rows`/`answer`/`error`/`done`；捕获异常为 `error` 事件
- schema 获取：复用与 `/api/schema` 相同的过滤逻辑（抽 `build_schema_tables(role) -> list` 到 `api/schema.py` 或 `db/schema_service.py`，避免复制）

- [ ] **Step 3: Run executor 测试 PASS**

- [ ] **Step 4: Commit（默认跳过）**

---

### Task 6: `POST /api/chat` SSE

**Files:**
- Create: `backend/app/api/chat.py`
- Create: `backend/tests/test_chat_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `get_current_user`, `iter_pipeline_events`
- Produces: SSE `text/event-stream`；事件名与 design §6.5 一致
- Request body: `{ "question": str, "session_id": str = "default" }`

- [ ] **Step 1: 写失败测试（mock generate_sql / compose_answer）**

```python
import importlib
from unittest.mock import patch

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


def _auth(client):
    r = client.post(
        "/api/auth/register",
        json={"username": "c1", "password": "password123", "role": "analyst"},
    )
    return r.json()["access_token"]


def test_chat_sse_happy_path(client):
    token = _auth(client)
    with patch("app.agent.sql_generator.generate_sql", return_value="SELECT COUNT(*) AS c FROM orders"), \
         patch("app.agent.answer_composer.compose_answer", return_value="订单很多"):
        with client.stream(
            "POST",
            "/api/chat",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
            json={"question": "有多少订单？", "session_id": "default"},
        ) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())
    assert "event: run_start" in text
    assert "event: sql" in text
    assert "event: rows" in text
    assert "event: answer" in text
    assert "event: done" in text


def test_chat_requires_auth(client):
    r = client.post("/api/chat", json={"question": "hi"})
    assert r.status_code == 401
```

- [ ] **Step 2: 实现 `chat.py`**

使用 `sse-starlette` **或** FastAPI `StreamingResponse` + 手工格式：

```text
event: run_start
data: {...}

```

（本阶段不强制新依赖；优先标准库格式化 SSE。）

从 `request_id` contextvar（若已有）取 id，否则 `uuid4`；`trace_id` 可等于 `request_id`。

注入 state：`user_id`/`user_role` 来自 `get_current_user`。

- [ ] **Step 3: Run PASS**

Run: `cd backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_chat_api.py -v`

- [ ] **Step 4: Commit（默认跳过）**

---

### Task 7: 前端鉴权骨架 + 浅白主题登录页

**Files:**
- Modify: `frontend/package.json`（加 `react-router-dom`）
- Create: `frontend/src/auth/token.ts`
- Create: `frontend/src/auth/AuthContext.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/pages/LoginPage.tsx`
- Modify: `frontend/src/main.tsx` / `App.tsx` / `index.css`
- Remove or stop using default Vite demo UI

**Interfaces:**
- Produces: `getToken()/setToken()/clearToken()` → `localStorage` key `daa_token`
- Produces: `apiFetch(path, options)` 自动带 Bearer
- Produces: 路由 `/` = LoginPage；浅白色营销视觉

- [ ] **Step 1: 安装路由**

Run: `cd frontend && npm install react-router-dom@6`

- [ ] **Step 2: 实现 token + api client**

`token.ts`：`daa_token` 读写删。

`client.ts`：

```typescript
import { appConfig } from '../config'
import { getToken } from '../auth/token'

export async function apiFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${appConfig.apiBaseUrl}${path}`, { ...init, headers })
  return res
}
```

- [ ] **Step 3: LoginPage（浅白主题）**

遵循 `frontend-design` skill。约束：

- 浅白底（如 `#FAFBFC` / `#FFFFFF`），深字，克制强调色（非紫、非暗色）
- 品牌 **data-analysis-agent** 为第一视口主信号
- 一句价值主张 + 登录/注册切换
- 注册：角色 select；`admin` 时显示邀请码
- 成功：`setToken` + `navigate('/app')`
- 已登录访问 `/` 时可跳转 `/app`

CSS 变量示例（写入 `index.css`）：

```css
:root {
  --bg: #f7f8fa;
  --surface: #ffffff;
  --text: #12141a;
  --muted: #5c6370;
  --accent: #0f6e56; /* 克制青绿，可调整但保持浅色系 */
  --border: #e6e8ec;
}
```

- [ ] **Step 4: 接好 Router**

`App.tsx`：`BrowserRouter` + routes `/` 与 `/app`（工作台可先占位组件，Task 8 补全）。

- [ ] **Step 5: 手工验证**

`npm run dev`：打开 `/` 应为浅白营销登录页；注册 analyst 能拿到 token。

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 8: 工作台 + SSE 消费

**Files:**
- Create: `frontend/src/pages/AppWorkbench.tsx`
- Create: `frontend/src/api/chat.ts`
- Create: `frontend/src/auth/ProtectedRoute.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: 未登录访问 `/app` → 重定向 `/`
- Produces: 左侧用户/示例/表列表/退出；右侧问答 + SQL + 表 + Trace
- Produces: `streamChat({question, sessionId, token, onEvent})`

- [ ] **Step 1: ProtectedRoute**

无 token → `<Navigate to="/" replace />`。

- [ ] **Step 2: SSE 客户端**

用 `fetch` + `ReadableStream` 解析 `event:` / `data:` 行；按 event 回调。

- [ ] **Step 3: AppWorkbench UI（浅白、同一 CSS 变量）**

- mount 时：`GET /api/auth/me`、`/api/examples`、`/api/schema`
- 点击示例 → 填入 textarea
- 提交 → 清空上次结果 → SSE 更新 answer / sql / rows / trace 列表
- 退出：`clearToken()` → `/`

- [ ] **Step 4: 手工验证门禁与展示**

未登录打开 `/app` 应回 `/`；登录后侧栏显示角色；mock/真后端下能看到 SQL 与表。

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 9: 文档同步 + 验收

**Files:**
- Modify: `docs/04-接口与前端.md`
- Modify: `README.md`（若几乎为空则补 Phase 2 启动最小说明）

- [ ] **Step 1: 更新 docs/04**

- 删除/改写 Phase 1「`GET /api/schema` 暂不鉴权」例外
- 写明 Phase 2+ 需登录；analyst 隐藏敏感字段元数据
- 可注明注册成功直接返回 JWT

- [ ] **Step 2: README 启动说明**

包含：`config.yaml`、`init_db`、uvicorn、`npm run dev`、注册/邀请码、`demo_analyst` / `demo1234`、默认 chat 经 Guardrail。

- [ ] **Step 3: 跑全量后端测试**

Run: `cd backend && /home/user/miniconda3/envs/python3.12/bin/python -m pytest -v`  
Expected: 全部 PASS

- [ ] **Step 4: 手工联调验收清单**

| 项 | 标准 |
|----|------|
| 注册 analyst / admin（邀请码） | 成功并发 JWT |
| 未登录 `/app` | 重定向 `/` |
| schema | analyst 无敏感列；admin 有 |
| chat | ≥5 个示例在真实 LLM 下返回回答+SQL+表（需有效 `config.yaml` llm） |
| 主题 | 浅白色，非暗色 |
| 安全 | 无跳过 Guardrail 的默认路径 |

- [ ] **Step 5: Commit（默认跳过）**

---

## Self-Review Checklist

1. **Spec coverage:** 鉴权、schema 过滤、examples、SSE chat、最小 Guardrail、线性管线、浅白前端、docs/README — 均有 Task  
2. **Placeholders:** 无 TBD；answer 事件为 `{text}`；Commit 明确可跳过  
3. **Type consistency:** `get_current_user` → `{id, username, role}`；`check_sql(..., user_role=)`；SSE 事件名与 design 一致  

## Execution Handoff

**Plan complete and saved to `spec/2026-07-25-phase2-auth-chat-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 每个 Task 派生子代理，Task 间审查，迭代快  

**2. Inline Execution** — 本会话用 executing-plans 按 Task 推进，设检查点  

**Which approach?**
