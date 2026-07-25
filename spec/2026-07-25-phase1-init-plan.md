# Phase 1 项目初始化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地可启动的 FastAPI 后端 + Vite 前端脚手架、SQLite（8 业务表 + 5 应用表 + ≥1000 行种子）、JSON 日志骨架、不鉴权的 `GET /api/schema`。

**Architecture:** 标准库 `sqlite3` 手写 DDL/种子；FastAPI 暴露 schema；`app.log` 提供 JSON 日志与 `request_id` 中间件；前端仅为 Vite+React+TS+Tailwind 默认页。

**Tech Stack:** Python 3.12（conda `python3.12`）· FastAPI · uvicorn · pydantic-settings · pytest · httpx · React · Vite · TypeScript · TailwindCSS · SQLite

## Global Constraints

- 规格：`spec/2026-07-25-phase1-init-design.md`；产品文档：`docs/02`、`docs/04`、`docs/06`
- 目录模块名为 `log`（不是 `observability`）
- `/api/schema` Phase 1 **不鉴权**；仅返回业务表
- 不引入 SQLAlchemy / LangChain / LangGraph
- 不预建 `auth/`、`agent/`、`tools/`、`security/`
- 密钥不提交；使用 `backend/.env.example` + 本地 `backend/.env`
- **Git commit：仅当用户明确要求时执行**；本计划中的 Commit 步骤默认跳过
- TDD：有行为的后端逻辑先写失败测试再实现
- **Python（强制，见 AGENTS.md）**：仅用 `/home/user/miniconda3/envs/python3.12/bin/python`（及同目录 pip）；禁止系统 python / 仓库内 `.venv`

## File Map

| Path | Responsibility |
|------|----------------|
| `backend/requirements.txt` | 依赖 |
| `backend/.env.example` | 环境变量模板 |
| `backend/app/config.py` | Settings |
| `backend/app/db/schema.py` | DDL + BUSINESS_TABLES / APP_TABLES |
| `backend/app/db/database.py` | 连接 |
| `backend/app/db/init_db.py` | 建库 + 种子 |
| `backend/app/log/logging.py` | JSON 日志 + request_id |
| `backend/app/api/schema.py` | GET /api/schema |
| `backend/app/main.py` | App 入口 |
| `backend/tests/test_init_db.py` | DB 验收测试 |
| `backend/tests/test_schema_api.py` | schema API 测试 |
| `frontend/*` | Vite 脚手架 |
| `.gitignore` | 忽略 venv、node_modules、.db、.env |

---

### Task 1: 仓库忽略规则 + 后端依赖与配置

**Files:**
- Modify: `.gitignore`
- Create: `backend/requirements.txt`
- Create: `backend/.env.example`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/conftest.py`

**Interfaces:**
- Produces: `Settings` with `openai_api_key`, `openai_base_url`, `openai_model`, `jwt_secret`, `admin_invite_code`, `database_path` (default `data/ecommerce.db`); `get_settings()` cached

- [ ] **Step 1: Update `.gitignore`**

Replace/extend so these are ignored:

```gitignore
.env
**/.env
!.env.example
!**/.env.example
.venv/
**/__pycache__/
*.pyc
.pytest_cache/
node_modules/
dist/
backend/data/*.db
*.db
```

- [ ] **Step 2: Write `backend/requirements.txt`**

```text
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
pydantic-settings>=2.6.0
python-dotenv>=1.0.0
pytest>=8.0.0
httpx>=0.27.0
```

- [ ] **Step 3: Write `backend/.env.example`**

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
JWT_SECRET=change-me
ADMIN_INVITE_CODE=your-invite-code
DATABASE_PATH=data/ecommerce.db
```

- [ ] **Step 4: Write config**

`backend/app/__init__.py` — empty.

`backend/app/config.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    jwt_secret: str = Field(default="change-me")
    admin_invite_code: str = Field(default="your-invite-code")
    database_path: str = "data/ecommerce.db"

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Write `backend/tests/conftest.py`**

```python
import os
from pathlib import Path

import pytest

# Ensure tests load example env if .env missing
BACKEND_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ADMIN_INVITE_CODE", "test-invite")


@pytest.fixture()
def tmp_db_path(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_file))
    from app.config import get_settings

    get_settings.cache_clear()
    yield db_file
    get_settings.cache_clear()
```

- [ ] **Step 6: Install into conda env `python3.12`（勿建 .venv）**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent/backend
cp -n .env.example .env
/home/user/miniconda3/envs/python3.12/bin/pip install -r requirements.txt
```

Expected: pip succeeds; `.env` exists locally.

- [ ] **Step 7: Commit (skip unless user requested)**

---

### Task 2: Schema DDL + 连接（TDD）

**Files:**
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/db/schema.py`
- Create: `backend/app/db/database.py`
- Create: `backend/tests/test_init_db.py` (partial — table presence; expand in Task 3)

**Interfaces:**
- Produces: `BUSINESS_TABLES`, `APP_TABLES`, `ALL_TABLES`, `DDL_STATEMENTS: list[str]`, `get_connection() -> sqlite3.Connection`

- [ ] **Step 1: Write failing test for table constants and create-tables**

`backend/tests/test_init_db.py`:

```python
import sqlite3

from app.db.schema import APP_TABLES, BUSINESS_TABLES, DDL_STATEMENTS


def test_business_and_app_table_sets():
    assert BUSINESS_TABLES == frozenset(
        {
            "users",
            "products",
            "orders",
            "order_items",
            "payments",
            "refunds",
            "campaigns",
            "traffic_logs",
        }
    )
    assert APP_TABLES == frozenset(
        {
            "app_users",
            "chat_sessions",
            "session_turns",
            "user_preferences",
            "user_analysis_summaries",
        }
    )
    assert BUSINESS_TABLES.isdisjoint(APP_TABLES)


def test_ddl_creates_all_tables(tmp_db_path):
    from app.db.database import get_connection
    from app.db.schema import apply_schema

    conn = get_connection()
    try:
        apply_schema(conn)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()

    assert BUSINESS_TABLES | APP_TABLES <= names


def test_users_has_sensitive_columns(tmp_db_path):
    from app.db.database import get_connection
    from app.db.schema import apply_schema

    conn = get_connection()
    try:
        apply_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    finally:
        conn.close()

    assert {"name", "phone", "email", "id_card"} <= cols
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd backend
/home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_init_db.py -v
```

Expected: FAIL (modules missing)

- [ ] **Step 3: Implement `schema.py` + `database.py`**

`backend/app/db/__init__.py` — empty.

`backend/app/db/schema.py` — define frozensets and DDL covering all fields from `docs/02` (INTEGER/TEXT/REAL as appropriate; PKs on `id`; `user_preferences.user_id` PK). Expose:

```python
def apply_schema(conn: sqlite3.Connection) -> None:
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
```

Minimal type choices:
- ids: INTEGER PRIMARY KEY
- amounts/prices: REAL
- dates: TEXT (ISO date / datetime)
- flags: INTEGER (0/1)
- JSON columns: TEXT

`backend/app/db/database.py`:

```python
import sqlite3
from pathlib import Path

from app.config import get_settings


def get_connection() -> sqlite3.Connection:
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
```

- [ ] **Step 4: Run tests — expect PASS** for the three tests above

```bash
pytest tests/test_init_db.py::test_business_and_app_table_sets tests/test_init_db.py::test_ddl_creates_all_tables tests/test_init_db.py::test_users_has_sensitive_columns -v
```

Expected: PASS

---

### Task 3: 种子数据 + `init_db` CLI（TDD）

**Files:**
- Create: `backend/app/db/init_db.py`
- Modify: `backend/tests/test_init_db.py`

**Interfaces:**
- Produces: `init_database(reset: bool = True) -> Path` — deletes existing DB if reset, applies schema, seeds; runnable as `python -m app.db.init_db`

- [ ] **Step 1: Add failing seed tests**

Append to `test_init_db.py`:

```python
def test_seed_row_count_and_coverage(tmp_db_path):
    from app.db.init_db import init_database

    init_database(reset=True)
    conn = sqlite3.connect(tmp_db_path)
    try:
        total = 0
        for table in BUSINESS_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            total += n
        assert total >= 1000

        # time span: orders within last 180 days relative to seed "today"
        min_d, max_d = conn.execute(
            "SELECT MIN(order_date), MAX(order_date) FROM orders"
        ).fetchone()
        assert min_d is not None and max_d is not None

        channels = {
            r[0] for r in conn.execute("SELECT DISTINCT channel FROM orders")
        }
        assert len(channels) >= 3

        # demo analyst optional but preferred
        n_app = conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]
        assert n_app >= 1
    finally:
        conn.close()
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/test_init_db.py::test_seed_row_count_and_coverage -v
```

Expected: FAIL (`init_database` missing)

- [ ] **Step 3: Implement `init_db.py`**

Requirements for seed generator (deterministic `random.Random(42)`):

- ~80 users, ~40 products, ~400 orders, ~800 order_items, ~350 payments, ~80 refunds, ~15 campaigns, ~400 traffic_logs (合计 ≥ 1000)
- dates: `datetime.date.today()` minus 0..179 days
- channels e.g. `["抖音","天猫","京东","官网","微信"]`
- cities/provinces pairs; categories/brands; payment methods; refund reasons
- users sensitive fields masked e.g. phone `138****1234`, id_card `110***********1234`
- insert demo `app_users` row: username `demo_analyst`, role `analyst`, password_hash `phase2-placeholder`
- `if __name__ == "__main__"` / module main: call `init_database(reset=True)` and print path + counts

Structure sketch:

```python
"""Build or rebuild SQLite DB. Re-running overwrites the local DB file."""

def init_database(*, reset: bool = True) -> Path:
    settings = get_settings()
    path = settings.db_path
    if reset and path.exists():
        path.unlink()
    conn = get_connection()
    try:
        apply_schema(conn)
        seed(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def seed(conn: sqlite3.Connection) -> None:
    ...
```

Also add `backend/app/db/__main__.py` or make `init_db.py` runnable:

```python
# app/db/__main__.py
from app.db.init_db import init_database

if __name__ == "__main__":
    path = init_database(reset=True)
    print(f"Initialized database at {path}")
```

Prefer `python -m app.db.init_db` via:

```python
# end of init_db.py
if __name__ == "__main__":
    p = init_database(reset=True)
    print(f"Initialized database at {p}")
```

And document README uses `python -m app.db.init_db` — ensure package path works when cwd is `backend` (PYTHONPATH=. or `pip install -e .` optional). Simplest: run as `cd backend && PYTHONPATH=. python -m app.db.init_db` OR add empty note that uvicorn/pytest run from `backend` with `pythonpath = .` in `pytest.ini`:

`backend/pytest.ini`:

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 4: Run all init_db tests — PASS**

```bash
cd backend
/home/user/miniconda3/envs/python3.12/bin/python -m pytest tests/test_init_db.py -v
/home/user/miniconda3/envs/python3.12/bin/python -m app.db.init_db
```

Expected: all PASS; `data/ecommerce.db` created.

---

### Task 4: JSON 日志骨架（`app.log`）

**Files:**
- Create: `backend/app/log/__init__.py`
- Create: `backend/app/log/logging.py`
- Create: `backend/tests/test_logging.py`

**Interfaces:**
- Produces: `get_request_id() -> str | None`, `set_request_id(id: str)`, `log_event(level, event, **fields)`, `RequestIdMiddleware`

- [ ] **Step 1: Write failing test**

```python
import json
from app.log.logging import log_event, set_request_id, get_request_id


def test_log_event_emits_json(capsys):
    set_request_id("req_test_1")
    log_event("INFO", "schema_served", detail={"tables": 8})
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "schema_served"
    assert payload["request_id"] == "req_test_1"
    assert payload["level"] == "INFO"
    assert "ts" in payload
```

- [ ] **Step 2: Run — FAIL**

```bash
pytest tests/test_logging.py -v
```

- [ ] **Step 3: Implement `app/log/logging.py`**

Use `contextvars.ContextVar` for request_id; print one JSON object per line to stdout (`ensure_ascii=False`). Middleware (Starlette):

```python
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:12]}"
        set_request_id(rid)
        started = time.perf_counter()
        log_event("INFO", "request_start", path=str(request.url.path), method=request.method)
        response = await call_next(request)
        ms = int((time.perf_counter() - started) * 1000)
        log_event("INFO", "request_end", path=str(request.url.path), status=response.status_code, latency_ms=ms)
        response.headers["X-Request-Id"] = rid
        return response
```

- [ ] **Step 4: Run — PASS**

```bash
pytest tests/test_logging.py -v
```

---

### Task 5: `GET /api/schema` + FastAPI app（TDD）

**Files:**
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/schema.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/test_schema_api.py`

**Interfaces:**
- Produces: FastAPI `app`; `GET /api/schema` → `{tables: [{name, columns: [{name, type, nullable}]}]}`; `GET /health` → `{status: ok}`

- [ ] **Step 1: Write failing API tests**

```python
import pytest
from fastapi.testclient import TestClient

from app.db.init_db import init_database
from app.db.schema import APP_TABLES, BUSINESS_TABLES


@pytest.fixture()
def client(tmp_db_path):
    init_database(reset=True)
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_schema_returns_only_business_tables(client):
    r = client.get("/api/schema")
    assert r.status_code == 200
    tables = r.json()["tables"]
    names = {t["name"] for t in tables}
    assert names == set(BUSINESS_TABLES)
    assert names.isdisjoint(APP_TABLES)
    orders = next(t for t in tables if t["name"] == "orders")
    col_names = {c["name"] for c in orders["columns"]}
    assert "pay_amount" in col_names
    assert "X-Request-Id" in r.headers


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [ ] **Step 2: Run — FAIL**

```bash
pytest tests/test_schema_api.py -v
```

- [ ] **Step 3: Implement schema route + main**

`backend/app/api/schema.py`:

```python
import sqlite3
from fastapi import APIRouter
from app.db.database import get_connection
from app.db.schema import BUSINESS_TABLES
from app.log.logging import log_event

router = APIRouter()

@router.get("/schema")
def get_schema():
    conn = get_connection()
    try:
        tables = []
        for name in sorted(BUSINESS_TABLES):
            cols = []
            for cid, cname, ctype, notnull, dflt, pk in conn.execute(
                f"PRAGMA table_info({name})"
            ):
                cols.append(
                    {
                        "name": cname,
                        "type": ctype or "TEXT",
                        "nullable": not bool(notnull) and not bool(pk),
                    }
                )
            tables.append({"name": name, "columns": cols})
        log_event("INFO", "schema_served", detail={"tables": len(tables)})
        return {"tables": tables}
    finally:
        conn.close()
```

`backend/app/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.schema import router as schema_router
from app.log.logging import RequestIdMiddleware

app = FastAPI(title="data-analysis-agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)
app.include_router(schema_router, prefix="/api")

@app.get("/health")
def health():
    return {"status": "ok"}
```

Note: middleware order — last added runs first for requests; ensure RequestIdMiddleware wraps requests. If order wrong, swap add order so request_id is set for schema handler.

- [ ] **Step 4: Run all backend tests — PASS**

```bash
pytest -v
```

- [ ] **Step 5: Manual smoke**

```bash
python -m app.db.init_db
uvicorn app.main:app --port 8000
# other terminal:
curl -s http://127.0.0.1:8000/api/schema | python -m json.tool | head
```

Expected: 8 tables; no app_* names.

---

### Task 6: 前端 Vite + Tailwind 脚手架

**Files:**
- Create: `frontend/` via Vite

- [ ] **Step 1: Scaffold**

```bash
cd /home/user/user_folder/tencent-docs/data-analysis-agent
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss @tailwindcss/vite
```

- [ ] **Step 2: Wire Tailwind (Vite plugin)**

Follow current Tailwind v4 Vite guide: add `@tailwindcss/vite` plugin in `vite.config.ts`; in `src/index.css` put `@import "tailwindcss";`. Keep default App content (may add a single class for smoke).

- [ ] **Step 3: Verify start**

```bash
npm run dev -- --host 127.0.0.1 --port 5173
```

Expected: page loads. Stop after verify.

---

### Task 7: README 启动说明微调 + 总验收

**Files:**
- Modify: `README.md`（仅「本地启动」段落，标明 Phase 1 已可执行后端/前端脚手架；chat/登录仍为目标形态）

- [ ] **Step 1: Update README 状态说明**

Clarify:
- Phase 1 已具备：`backend/`、`frontend/` 脚手架、`init_db`、`GET /api/schema`（暂不鉴权）
- 登录 / chat / Agent 仍按后续 Phase

Keep commands aligned with this plan (`cp backend/.env.example backend/.env` 等).

- [ ] **Step 2: Full acceptance checklist**

```bash
cd backend
/home/user/miniconda3/envs/python3.12/bin/python -m pytest -v
/home/user/miniconda3/envs/python3.12/bin/python -m app.db.init_db
# count tables / rows
python - <<'PY'
import sqlite3
from app.db.schema import BUSINESS_TABLES, APP_TABLES
from app.config import get_settings
conn = sqlite3.connect(get_settings().db_path)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert BUSINESS_TABLES | APP_TABLES <= tables
total = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in BUSINESS_TABLES)
print("business_rows", total)
assert total >= 1000
PY
uvicorn app.main:app --port 8000 &
sleep 1
curl -s http://127.0.0.1:8000/api/schema | python -c "import sys,json; d=json.load(sys.stdin); assert len(d['tables'])==8; print('schema_ok')"
cd ../frontend && npm run build
```

Expected: tests pass; schema_ok; frontend build ok.

---

## Self-Review Notes

1. Spec coverage: scaffold, env, SQLite 13 tables, seed ≥1000, log skeleton, `/api/schema` only business, frontend start — all tasked.
2. No TBD placeholders.
3. Naming: `app.log` throughout; `RequestIdMiddleware` shared by main.
4. Commits skipped by global constraint unless user asks.
