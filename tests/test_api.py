from __future__ import annotations

import ast
import csv
import inspect
import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.app.results.store import ResultStore, ResultWriteMeta
from backend.app.types import PermissionSet, RuntimeContext, TimeRange
from scripts.init_sqlite import SQL_DIR, apply_sql, password_hash

NOW = "2026-08-29T00:00:00+00:00"
TIME_RANGE = TimeRange(
    start="2026-08-01T00:00:00+00:00",
    end="2026-09-01T00:00:00+00:00",
    grain="month",
    label="2026-08",
    source="user",
)
USERS_DDL = Path("migrations/sqlite/users.sql").read_text(encoding="utf-8")


def _seed_users(path: Path) -> Path:
    with sqlite3.connect(path) as conn:
        conn.executescript(USERS_DDL)
        for user_id, username, role, tables in (
            ("u-admin", "admin", "operator", ["dim_sku", "fact_order_item"]),
            ("u-analyst", "analyst", "analyst", ["fact_order_item"]),
        ):
            conn.execute(
                "INSERT INTO app_user VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    user_id,
                    username,
                    password_hash(username),
                    username,
                    role,
                    "default",
                    NOW,
                ),
            )
            write_ops = ["update_sku_status"] if role == "operator" else []
            conn.execute(
                "INSERT INTO user_permission VALUES (?, 1, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    json.dumps(tables),
                    json.dumps([f"data-agent-ecommerce.{t}.*" for t in tables]),
                    json.dumps(["gmv"]),
                    json.dumps(write_ops),
                    NOW,
                ),
            )
        conn.commit()
    return path


class FakeGraph:
    def __init__(self) -> None:
        self.threads: set[str] = set()

    def update_state(self, config, values):
        self.threads.add(config["configurable"]["thread_id"])


class FakeCoordinator:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_result: dict = {
            "answer": "GMV 为 100",
            "result_id": "r-fixed",
            "intent": "query",
        }

    def __call__(self, graph, message, ctx, *, resume=None):
        self.calls.append(
            {"message": message, "resume": resume, "thread_id": ctx.thread_id, "user_id": ctx.user_id}
        )
        if resume is not None:
            return {
                "answer": "已提交",
                "operation_id": "op-1",
                "intent": "write",
            }
        return dict(self.next_result)


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in body.split("\n\n"):
        event = None
        data = None
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                raw = line.partition(":")[2].strip()
                data = json.loads(raw) if raw else {}
        if event is not None:
            events.append((event, data or {}))
    return events


def _write_ready(store: ResultStore, ctx: RuntimeContext, rows: list[dict]) -> str:
    rid = store.create_writing(
        ResultWriteMeta(
            user_id=ctx.user_id,
            thread_id=ctx.thread_id,
            permission_version=ctx.permissions.permission_version,
            catalog_version=1,
            time_range=TIME_RANGE,
            request_time_utc=ctx.request_time_utc,
            metric_versions={"gmv": 1},
        )
    )
    store.append_rows(rid, rows)
    store.finalize(rid, data_as_of="2026-08-28T00:00:00+00:00")
    return rid


@pytest.fixture
def env(tmp_path: Path):
    users_db = _seed_users(tmp_path / "users.sqlite")
    runtime_db = tmp_path / "runtime.sqlite"
    apply_sql(runtime_db, SQL_DIR / "runtime.sql")
    results_db = tmp_path / "results.sqlite"
    apply_sql(results_db, SQL_DIR / "results.sql")
    store = ResultStore(
        results_db=results_db,
        results_dir=tmp_path / "results",
        ttl_hours=24,
        max_rows=3,
        max_bytes=64 * 1024,
    )
    graph = FakeGraph()
    coordinator = FakeCoordinator()
    from backend.app.main import create_app

    app = create_app(
        users_db=users_db,
        runtime_db=runtime_db,
        result_store=store,
        graph=graph,
        invoke_fn=coordinator,
        max_rows=3,
        timezone="Asia/Shanghai",
        request_time_utc=NOW,
        jwt_secret="t15-test-secret-t15-test-secret!",
        jwt_ttl_hours=24,
        title_fn=lambda message: "超长标题用来验证截断到十个字以上",
    )
    client = TestClient(app)
    return SimpleNamespace(
        client=client,
        app=app,
        users_db=users_db,
        runtime_db=runtime_db,
        store=store,
        graph=graph,
        coordinator=coordinator,
    )


def _login(client: TestClient, username: str = "admin", password: str | None = None) -> dict:
    res = client.post(
        "/api/auth/login",
        json={"username": username, "password": password or username},
    )
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_login_rejects_bad_password(env):
    res = env.client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert res.status_code == 401


def test_login_returns_token(env):
    res = env.client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    body = res.json()
    assert res.status_code == 200
    assert body["token"]
    assert body["user_id"] == "u-admin"
    assert body["role"] == "operator"


def test_unauthenticated_requests_are_401(env):
    assert env.client.get("/api/threads").status_code == 401
    assert env.client.post("/api/threads").status_code == 401


def test_create_and_list_threads_writes_runtime_not_task_tables(env):
    headers = _login(env.client)
    created = env.client.post("/api/threads", json={"title": "问数"}, headers=headers)
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]
    assert thread_id in env.graph.threads

    listed = env.client.get("/api/threads", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["threads"]
    assert rows[0]["thread_id"] == thread_id
    assert rows[0]["title"] == "新会话"

    other = _login(env.client, "analyst")
    assert env.client.get("/api/threads", headers=other).json()["threads"] == []

    with sqlite3.connect(env.runtime_db) as conn:
        threads = conn.execute("SELECT thread_id, user_id FROM thread").fetchall()
        tasks = conn.execute("SELECT COUNT(*) FROM task").fetchone()[0]
        hitl = conn.execute("SELECT COUNT(*) FROM hitl_interrupt").fetchone()[0]
    assert threads == [(thread_id, "u-admin")]
    assert tasks == 0
    assert hitl == 0


def test_messages_sse_emits_status_result_token_and_done(env):
    headers = _login(env.client)
    thread_id = env.client.post("/api/threads", headers=headers).json()["thread_id"]
    res = env.client.post(
        f"/api/threads/{thread_id}/messages",
        json={"message": "本月GMV"},
        headers=headers,
    )
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    events = _sse_events(res.text)
    names = [name for name, _ in events]
    assert "status" in names
    assert "result_ref" in names
    assert "token" in names
    assert names[-1] == "done"
    payload = dict(events)
    assert payload["result_ref"]["result_id"] == "r-fixed"
    assert "GMV" in payload["token"]["text"]
    assert env.coordinator.calls[-1]["resume"] is None
    assert env.coordinator.calls[-1]["message"] == "本月GMV"


def test_messages_sse_hides_parse_errors(env):
    def boom(*args, **kwargs):
        raise ValueError(
            "2 validation errors for IntentDraft\nrefer_previous_skus\n  Input should be a valid boolean"
        )

    env.app.state.invoke_fn = boom
    headers = _login(env.client)
    thread_id = env.client.post("/api/threads", headers=headers).json()["thread_id"]
    events = _sse_events(
        env.client.post(
            f"/api/threads/{thread_id}/messages",
            json={"message": "本月GMV"},
            headers=headers,
        ).text
    )
    payload = dict(events)
    assert payload["error"]["message"] == "模型输出无法解析，请再试一次。"
    assert "validation error" not in payload["error"]["message"].lower()


def test_messages_sse_emits_interrupt(env):
    env.coordinator.next_result = {
        "__interrupt__": [SimpleNamespace(value={"kind": "write_preview", "operation_id": "op-1"})]
    }
    headers = _login(env.client)
    thread_id = env.client.post("/api/threads", headers=headers).json()["thread_id"]
    events = _sse_events(
        env.client.post(
            f"/api/threads/{thread_id}/messages",
            json={"message": "下架"},
            headers=headers,
        ).text
    )
    names = [name for name, _ in events]
    assert "interrupt" in names
    payload = dict(events)["interrupt"]
    assert payload["operation_id"] == "op-1"
    assert payload["kind"] == "write_preview"


def test_resume_only_resumes_coordinator(env):
    headers = _login(env.client)
    thread_id = env.client.post("/api/threads", headers=headers).json()["thread_id"]
    res = env.client.post(
        f"/api/threads/{thread_id}/resume",
        json={"approved": True, "user_id": "u-admin"},
        headers=headers,
    )
    assert res.status_code == 200
    assert env.coordinator.calls[-1]["resume"] == {"approved": True, "user_id": "u-admin"}
    events = _sse_events(res.text)
    assert any(name == "done" for name, _ in events)


def test_resume_module_does_not_invoke_skills():
    from backend.app.api import interrupts

    src = inspect.getsource(interrupts)
    tree = ast.parse(src)
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "execute_write" not in attrs
    assert "run_query_skill" not in attrs
    assert "prepare_write" not in src
    assert "Command" in src or "resume" in src


def test_result_page_reads_parquet_not_mysql(env):
    headers = _login(env.client)
    ctx = RuntimeContext(
        tenant_id="default",
        user_id="u-admin",
        role="operator",
        request_time_utc=NOW,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id="u-admin",
            role="operator",
            allowed_tables=["dim_sku"],
            allowed_columns=[],
            allowed_metrics=["gmv"],
            allowed_write_ops=[],
            catalog_version=1,
            permission_version=1,
        ),
        thread_id="th-page",
    )
    rid = _write_ready(
        env.store,
        ctx,
        [{"sku_id": "s1", "gmv": 10}, {"sku_id": "s2", "gmv": 20}, {"sku_id": "s3", "gmv": 30}],
    )
    page = env.client.get(f"/api/results/{rid}?offset=1&limit=1", headers=headers)
    assert page.status_code == 200
    body = page.json()
    assert body["row_count"] == 3
    assert body["rows"] == [{"sku_id": "s2", "gmv": 20}]
    assert body["columns"] == ["sku_id", "gmv"]
    assert body["metric_versions"] == {"gmv": 1}


def test_csv_caps_rows_and_checks_owner_ttl(env):
    headers = _login(env.client)
    ctx = RuntimeContext(
        tenant_id="default",
        user_id="u-admin",
        role="operator",
        request_time_utc=NOW,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id="u-admin",
            role="operator",
            allowed_tables=["dim_sku"],
            allowed_columns=[],
            allowed_metrics=["gmv"],
            allowed_write_ops=[],
            catalog_version=1,
            permission_version=1,
        ),
        thread_id="th-csv",
    )
    rid = _write_ready(
        env.store,
        ctx,
        [{"sku_id": "s1", "gmv": 10}, {"sku_id": "s2", "gmv": 20}, {"sku_id": "s3", "gmv": 30}],
    )
    csv_res = env.client.get(f"/api/results/{rid}.csv", headers=headers)
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    table = list(csv.reader(io.StringIO(csv_res.text)))
    assert table[0] == ["sku_id", "gmv"]
    assert len(table) - 1 <= 3
    assert len(table) - 1 == 3

    other = _login(env.client, "analyst")
    assert env.client.get(f"/api/results/{rid}.csv", headers=other).status_code == 403
    assert env.client.get(f"/api/results/{rid}", headers=other).status_code == 403


def test_csv_rejects_expired_result(env, tmp_path):
    headers = _login(env.client)
    expired_db = tmp_path / "expired.sqlite"
    apply_sql(expired_db, SQL_DIR / "results.sql")
    expired_store = ResultStore(
        results_db=expired_db,
        results_dir=tmp_path / "expired-results",
        ttl_hours=1,
        max_rows=10,
        max_bytes=64 * 1024,
    )
    env.app.state.result_store = expired_store
    ctx = RuntimeContext(
        tenant_id="default",
        user_id="u-admin",
        role="operator",
        request_time_utc=NOW,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id="u-admin",
            role="operator",
            allowed_tables=["dim_sku"],
            allowed_columns=[],
            allowed_metrics=["gmv"],
            allowed_write_ops=[],
            catalog_version=1,
            permission_version=1,
        ),
        thread_id="th-exp",
    )
    rid = expired_store.create_writing(
        ResultWriteMeta(
            user_id=ctx.user_id,
            thread_id=ctx.thread_id,
            permission_version=1,
            catalog_version=1,
            time_range=TIME_RANGE,
            request_time_utc=NOW,
            metric_versions={"gmv": 1},
        )
    )
    expired_store.append_rows(rid, [{"sku_id": "s1", "gmv": 1}])
    expired_store.finalize(rid, data_as_of=NOW)
    env.app.state.request_time_utc = "2026-08-30T02:00:00+00:00"
    assert env.client.get(f"/api/results/{rid}.csv", headers=headers).status_code == 410
    assert env.client.get(f"/api/results/{rid}", headers=headers).status_code == 410


def test_login_token_is_jwt_with_24h_exp(env):
    res = env.client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    body = res.json()
    payload = jwt.decode(body["token"], options={"verify_signature": False})
    assert res.status_code == 200
    assert payload["sub"] == "u-admin"
    assert payload["role"] == "operator"
    assert payload["exp"] - payload["iat"] == 24 * 3600
    assert "permissions" not in payload
    assert body["role"] == "operator"


def test_me_works_on_fresh_app_without_session_dict(env, tmp_path):
    token = env.client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    from backend.app.main import create_app

    store = ResultStore(
        results_db=tmp_path / "fresh-results.sqlite",
        results_dir=tmp_path / "fresh-results",
        ttl_hours=24,
        max_rows=3,
        max_bytes=64 * 1024,
    )
    apply_sql(tmp_path / "fresh-results.sqlite", SQL_DIR / "results.sql")
    fresh = create_app(
        users_db=env.users_db,
        runtime_db=env.runtime_db,
        result_store=store,
        graph=FakeGraph(),
        invoke_fn=FakeCoordinator(),
        max_rows=3,
        timezone="Asia/Shanghai",
        request_time_utc=NOW,
        jwt_secret="t15-test-secret-t15-test-secret!",
        jwt_ttl_hours=24,
    )
    fresh.state.sessions = {}
    client = TestClient(fresh)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["user_id"] == "u-admin"
    assert body["role"] == "operator"
    assert "token" not in body


def test_expired_jwt_is_401(env):
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "u-admin",
            "username": "admin",
            "role": "operator",
            "iat": int((now - timedelta(hours=25)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        "t15-test-secret-t15-test-secret!",
        algorithm="HS256",
    )
    res = env.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_analyst_login_jwt_role(env):
    res = env.client.post("/api/auth/login", json={"username": "analyst", "password": "analyst"})
    payload = jwt.decode(res.json()["token"], options={"verify_signature": False})
    assert res.status_code == 200
    assert payload["role"] == "analyst"
    assert res.json()["role"] == "analyst"


def test_register_writes_users_sqlite_not_admin_role(env):
    res = env.client.post(
        "/api/auth/register",
        json={"username": "ops2", "password": "secret1", "role": "operator"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["token"]
    assert body["role"] == "operator"
    payload = jwt.decode(body["token"], options={"verify_signature": False})
    assert payload["role"] == "operator"
    with sqlite3.connect(env.users_db) as conn:
        row = conn.execute(
            "SELECT username, role FROM app_user WHERE username = ?",
            ("ops2",),
        ).fetchone()
        perms = conn.execute(
            """SELECT allowed_write_ops_json FROM user_permission
               WHERE user_id = (SELECT user_id FROM app_user WHERE username = ?)""",
            ("ops2",),
        ).fetchone()
    assert row == ("ops2", "operator")
    assert row[1] != "admin"
    assert "update_sku_status" in json.loads(perms[0])


def test_register_rejects_admin_role(env):
    res = env.client.post(
        "/api/auth/register",
        json={"username": "bad", "password": "secret1", "role": "admin"},
    )
    assert res.status_code == 422


def test_register_duplicate_username_is_409(env):
    res = env.client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "secret1", "role": "analyst"},
    )
    assert res.status_code == 409


def test_delete_thread_owner_only(env):
    headers = _login(env.client)
    thread_id = env.client.post("/api/threads", headers=headers).json()["thread_id"]
    other = _login(env.client, "analyst")
    assert env.client.delete(f"/api/threads/{thread_id}", headers=other).status_code == 404
    deleted = env.client.delete(f"/api/threads/{thread_id}", headers=headers)
    assert deleted.status_code == 204
    with sqlite3.connect(env.runtime_db) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM thread WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
        tasks = conn.execute("SELECT COUNT(*) FROM task").fetchone()[0]
        hitl = conn.execute("SELECT COUNT(*) FROM hitl_interrupt").fetchone()[0]
    assert remaining == 0
    assert tasks == 0
    assert hitl == 0
    assert env.client.get("/api/threads", headers=headers).json()["threads"] == []


def test_clip_title_strips_minimax_think_block():
    from backend.app.api.chat import clip_title

    raw = "<think>\nThe user asks about GMV\n</think>\n本月GMV"
    title = clip_title(raw)
    assert title == "本月GMV"
    assert not title.startswith("think")


def test_title_fn_truncated_and_message_does_not_become_title(env):
    from backend.app.coordinator.graph import upsert_thread

    headers = _login(env.client)
    thread_id = env.client.post("/api/threads", headers=headers).json()["thread_id"]
    raw = "本月GMV是多少请问一下详细的品类拆分情况"
    env.client.post(
        f"/api/threads/{thread_id}/messages",
        json={"message": raw},
        headers=headers,
    )
    upsert_thread(env.runtime_db, thread_id, "u-admin", raw, NOW)
    with sqlite3.connect(env.runtime_db) as conn:
        title = conn.execute(
            "SELECT title FROM thread WHERE thread_id = ?", (thread_id,)
        ).fetchone()[0]
    assert title != raw
    assert title != "新会话"
    assert len(title) <= 10
    src = Path("backend/app/coordinator/graph.py").read_text(encoding="utf-8")
    assert 'title = (state.get("message") or "")[:40]' not in src


def test_create_app_from_config_compiles_coordinator_graph():
    from backend.app.main import create_app

    app = create_app()
    assert app.state.graph is not None
    assert hasattr(app.state.graph, "invoke")
