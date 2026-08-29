from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from backend.app.catalog.models import (
    CatalogSnapshot,
    MetricSpec,
    SchemaColumn,
    SchemaTable,
    TableRelation,
)
from backend.app.results.store import ResultStore
from backend.app.types import (
    CompiledQuery,
    PermissionSet,
    QueryTask,
    RuntimeContext,
    SkillErrorCode,
    TimeRange,
)
from scripts.init_sqlite import SQL_DIR, apply_sql

NOW = "2026-08-29T00:00:00+00:00"
TIME_RANGE = TimeRange(
    start="2026-08-01T00:00:00+00:00",
    end="2026-09-01T00:00:00+00:00",
    grain="month",
    label="2026-08",
    source="user",
)
GMV_SQL = (
    "SELECT oi.price, SUM(oi.price * oi.qty) AS gmv "
    "FROM fact_order_item oi "
    "JOIN fact_order o ON oi.order_id = o.id "
    "WHERE o.created_at >= :start AND o.created_at < :end "
    "GROUP BY oi.price"
)
GMV_PARAMS = {"start": "2026-08-01T00:00:00+00:00", "end": "2026-09-01T00:00:00+00:00"}

ITEM_ORDER = TableRelation(
    left_table="fact_order_item",
    right_table="fact_order",
    left_col="order_id",
    right_col="id",
    cardinality="many_to_one",
    source="fk",
    version=1,
)


def _table(name: str) -> SchemaTable:
    return SchemaTable(
        table_name=name,
        business_name=name,
        domain="orders",
        grain_description=name,
    )


def _col(table: str, name: str, data_type: str = "bigint") -> SchemaColumn:
    return SchemaColumn(table_name=table, column_name=name, data_type=data_type)


@pytest.fixture
def catalog() -> CatalogSnapshot:
    return CatalogSnapshot(
        catalog_version=1,
        tables=[_table(n) for n in ("fact_order", "fact_order_item", "dim_sku")],
        columns=[
            _col("fact_order", "id"),
            _col("fact_order", "status", "varchar"),
            _col("fact_order", "created_at", "datetime"),
            _col("fact_order_item", "id"),
            _col("fact_order_item", "order_id"),
            _col("fact_order_item", "sku_id"),
            _col("fact_order_item", "price", "decimal"),
            _col("fact_order_item", "qty"),
            _col("dim_sku", "id"),
            _col("dim_sku", "sku_name", "varchar"),
        ],
        relations=[ITEM_ORDER],
        metrics=[
            MetricSpec(
                metric_id="gmv",
                name="GMV",
                version=1,
                grain_table="fact_order_item",
                formula="SUM(oi.price * oi.qty)",
                time_field="fact_order.created_at",
                unit="CNY",
                filters=[],
                deps=["fact_order_item.price", "fact_order_item.qty", "fact_order.created_at"],
            )
        ],
        write_ops=[],
    )


@pytest.fixture
def task() -> QueryTask:
    return QueryTask(
        task_id="t1",
        metric_ids=["gmv"],
        dimensions=[],
        filters=[],
        time_range=TIME_RANGE,
        catalog_version=1,
        permission_version=1,
    )


@pytest.fixture
def ctx() -> RuntimeContext:
    return RuntimeContext(
        tenant_id="default",
        user_id="u1",
        role="analyst",
        request_time_utc=NOW,
        timezone="Asia/Shanghai",
        permissions=PermissionSet(
            tenant_id="default",
            user_id="u1",
            role="analyst",
            allowed_tables=["fact_order", "fact_order_item", "dim_sku"],
            allowed_columns=["fact_order.*", "fact_order_item.*", "dim_sku.*"],
            allowed_metrics=["gmv"],
            allowed_write_ops=[],
            catalog_version=1,
            permission_version=1,
        ),
        thread_id="th1",
    )


@pytest.fixture
def query() -> CompiledQuery:
    return CompiledQuery(sql=GMV_SQL, params=dict(GMV_PARAMS))


@pytest.fixture
def store(tmp_path: Path) -> ResultStore:
    db = tmp_path / "results.sqlite"
    apply_sql(db, SQL_DIR / "results.sql")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    return ResultStore(
        results_db=db,
        results_dir=results_dir,
        ttl_hours=1,
        max_rows=100,
        max_bytes=64 * 1024,
    )


def _sql_text(stmt) -> str:
    return str(getattr(stmt, "text", stmt))


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._idx = 0

    def keys(self) -> list[str]:
        return list(self._rows[0].keys()) if self._rows else []

    def mappings(self) -> FakeResult:
        return self

    def __iter__(self):
        return iter(self._rows)

    def fetchmany(self, size: int) -> list[dict]:
        chunk = self._rows[self._idx : self._idx + size]
        self._idx += size
        return chunk

    def scalar(self):
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))


class FakeConn:
    def __init__(self, engine: ScriptedEngine) -> None:
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execution_options(self, **kwargs) -> FakeConn:
        return self

    def execute(self, stmt, params=None):
        sql = _sql_text(stmt)
        self._engine.calls.append((sql, params))
        return self._engine.handle(sql, params)


class ScriptedEngine:
    def __init__(
        self,
        *,
        select_rows: list[dict] | None = None,
        max_time: datetime | None = datetime(2026, 8, 15, tzinfo=UTC),
        select_fails_left: int = 0,
    ) -> None:
        self.calls: list[tuple[str, object]] = []
        self.select_rows = select_rows or [{"price": 10, "gmv": 100}]
        self.max_time = max_time
        self.select_fails_left = select_fails_left

    def connect(self) -> FakeConn:
        return FakeConn(self)

    def handle(self, sql: str, params: object) -> FakeResult:
        low = " ".join(sql.lower().split())
        if "max_execution_time" in low:
            return FakeResult([])
        if low.startswith("explain"):
            return FakeResult([{"rows": 8}])
        if "max(" in low:
            return FakeResult([{"m": self.max_time}])
        if self.select_fails_left > 0:
            self.select_fails_left -= 1
            raise OperationalError("SELECT", {}, Exception("connection lost"))
        return FakeResult(list(self.select_rows))


def _run(engine, store, query, ctx, catalog, task, **kw) -> str:
    from backend.app.mysql.execute_read import execute_read

    return execute_read(
        query,
        ctx,
        task=task,
        catalog=catalog,
        store=store,
        allowed_joins=[ITEM_ORDER],
        engine=engine,
        timeout_seconds=30,
        **kw,
    )


def test_rejects_unsafe_sql_without_touching_mysql(store, catalog, task, ctx):
    from backend.app.mysql.execute_read import ExecuteReadError, execute_read

    engine = ScriptedEngine()
    unsafe = CompiledQuery(sql="SELECT * FROM fact_order", params={})
    with pytest.raises(ExecuteReadError) as exc:
        execute_read(
            unsafe,
            ctx,
            task=task,
            catalog=catalog,
            store=store,
            allowed_joins=[ITEM_ORDER],
            engine=engine,
        )
    assert exc.value.code == SkillErrorCode.UNSAFE_SQL
    assert engine.calls == []
    assert list(store.results_dir.glob("*.parquet")) == []
    assert list(store.results_dir.glob("*.part")) == []


def test_rejects_too_broad_without_executing_select(store, catalog, task, ctx):
    from backend.app.mysql.execute_read import ExecuteReadError, execute_read

    engine = ScriptedEngine()
    broad = CompiledQuery(sql="SELECT id FROM fact_order", params={})
    with pytest.raises(ExecuteReadError) as exc:
        execute_read(
            broad,
            ctx,
            task=task,
            catalog=catalog,
            store=store,
            allowed_joins=[],
            engine=engine,
        )
    assert exc.value.code == SkillErrorCode.TOO_BROAD
    assert engine.calls == []


def test_executes_with_bound_params_not_inlined_sql(store, catalog, task, ctx, query):
    engine = ScriptedEngine()
    rid = _run(engine, store, query, ctx, catalog, task)
    assert rid
    assert all(params is not None for _, params in engine.calls)
    select_calls = [
        (sql, params)
        for sql, params in engine.calls
        if "max(" not in sql.lower() and not sql.lower().lstrip().startswith("explain")
        and "max_execution_time" not in sql.lower()
    ]
    assert select_calls
    sql, params = select_calls[0]
    assert ":start" in sql and ":end" in sql
    assert "'2026-08-01" not in sql
    assert params == GMV_PARAMS


def test_sets_max_execution_time_on_reader_session(store, catalog, task, ctx, query):
    engine = ScriptedEngine()
    _run(engine, store, query, ctx, catalog, task)
    assert any(
        "max_execution_time" in sql.lower() and params == {"ms": 30_000}
        for sql, params in engine.calls
    )


def test_happy_path_writes_parquet_and_returns_result_id(store, catalog, task, ctx, query):
    engine = ScriptedEngine()
    rid = _run(engine, store, query, ctx, catalog, task)
    assert (store.results_dir / f"{rid}.parquet").exists()
    assert not (store.results_dir / f"{rid}.part").exists()
    page = store.read_page(rid, ctx)
    assert page.result_id == rid
    assert page.row_count == 1
    assert page.columns == ["price", "gmv"]
    assert page.preview_rows[0]["gmv"] == 100


def test_data_as_of_is_min_of_request_time_and_grain_max(store, catalog, task, ctx, query):
    engine = ScriptedEngine(max_time=datetime(2026, 8, 15, tzinfo=UTC))
    rid = _run(engine, store, query, ctx, catalog, task)
    assert store.read_page(rid, ctx).data_as_of == "2026-08-15T00:00:00+00:00"

    engine_future = ScriptedEngine(max_time=datetime(2026, 12, 1, tzinfo=UTC))
    rid2 = _run(engine_future, store, query, ctx, catalog, task)
    assert store.read_page(rid2, ctx).data_as_of == NOW


def test_data_as_of_empty_table_uses_time_range_start(store, catalog, task, ctx, query):
    engine = ScriptedEngine(max_time=None)
    rid = _run(engine, store, query, ctx, catalog, task)
    assert store.read_page(rid, ctx).data_as_of == TIME_RANGE.start


def test_query_failure_aborts_without_parquet(store, catalog, task, ctx, query):
    from backend.app.mysql.execute_read import ExecuteReadError

    engine = ScriptedEngine(select_fails_left=5)
    with pytest.raises(ExecuteReadError) as exc:
        _run(engine, store, query, ctx, catalog, task, max_retries=1)
    assert exc.value.code == SkillErrorCode.REJECTED
    assert list(store.results_dir.glob("*.parquet")) == []
    assert list(store.results_dir.glob("*.part")) == []


def test_query_failure_retries_then_succeeds(store, catalog, task, ctx, query):
    engine = ScriptedEngine(select_fails_left=1)
    rid = _run(engine, store, query, ctx, catalog, task, max_retries=1)
    page = store.read_page(rid, ctx)
    assert page.row_count == 1
    assert (store.results_dir / f"{rid}.parquet").exists()


def test_row_limit_aborts_without_parquet(tmp_path: Path, catalog, task, ctx, query):
    from backend.app.mysql.execute_read import ExecuteReadError

    db = tmp_path / "results.sqlite"
    apply_sql(db, SQL_DIR / "results.sql")
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    tight = ResultStore(
        results_db=db,
        results_dir=results_dir,
        ttl_hours=1,
        max_rows=1,
        max_bytes=64 * 1024,
    )
    engine = ScriptedEngine(select_rows=[{"price": 1, "gmv": 1}, {"price": 2, "gmv": 2}])
    with pytest.raises(ExecuteReadError) as exc:
        _run(engine, tight, query, ctx, catalog, task)
    assert exc.value.code == SkillErrorCode.TOO_BROAD
    assert list(results_dir.glob("*.parquet")) == []
    assert list(results_dir.glob("*.part")) == []


def test_defaults_to_reader_engine_not_writer(monkeypatch, store, catalog, task, ctx, query):
    engine = ScriptedEngine()
    roles: list[str] = []

    def fake_get_engine(role: str):
        roles.append(role)
        return engine

    monkeypatch.setattr("backend.app.mysql.pool.get_engine", fake_get_engine)
    from backend.app.mysql.execute_read import execute_read

    execute_read(
        query,
        ctx,
        task=task,
        catalog=catalog,
        store=store,
        allowed_joins=[ITEM_ORDER],
        timeout_seconds=30,
    )
    assert roles == ["reader"]


def _connect_or_skip():
    if not Path("config.yaml").exists():
        pytest.skip("config.yaml missing")
    try:
        from sqlalchemy import text

        from backend.app.mysql.pool import get_engine

        engine = get_engine("reader")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL reader unreachable: {exc}")


@pytest.mark.integration
def test_execute_read_against_mysql_reader(store, catalog, task, ctx):
    from backend.app.mysql.execute_read import execute_read
    from backend.app.mysql.pool import get_engine

    _connect_or_skip()
    writer = get_engine("writer")
    reader = get_engine("reader")
    assert reader.url.username != writer.url.username

    sql = (
        "SELECT o.id, o.status FROM fact_order o "
        "WHERE o.created_at >= :start AND o.created_at < :end LIMIT 5"
    )
    query = CompiledQuery(sql=sql, params=dict(GMV_PARAMS))
    rid = execute_read(
        query,
        ctx,
        task=task,
        catalog=catalog,
        store=store,
        allowed_joins=[],
        engine=reader,
        timeout_seconds=30,
    )
    page = store.read_page(rid, ctx)
    assert page.result_id == rid
    assert page.row_count >= 0
    assert (store.results_dir / f"{rid}.parquet").exists()
    assert page.data_as_of
    assert page.data_as_of <= ctx.request_time_utc or page.data_as_of == TIME_RANGE.start
