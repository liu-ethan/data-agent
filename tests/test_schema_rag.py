from __future__ import annotations

import ast
import inspect
import sqlite3
from pathlib import Path

from backend.app.catalog.models import (
    CatalogSnapshot,
    MetricSpec,
    SchemaColumn,
    SchemaTable,
    TableRelation,
)
from backend.app.types import (
    Ambiguous,
    FilterCond,
    PermissionSet,
    QueryTask,
    RuntimeContext,
    SchemaBundle,
    SchemaGap,
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
PAID = ["paid", "shipped", "completed"]


class FakeLlm:
    def __init__(self, table_queries: list[str]) -> None:
        self._table_queries = table_queries
        self.prompts: list[str] = []
        self.gap_calls = 0

    def table_queries(self, task: QueryTask, prompt: str) -> list[str]:
        self.prompts.append(prompt)
        return list(self._table_queries)

    def schema_gap(
        self,
        *,
        missing_concept: str,
        purpose: str,
        constraints: list[str],
        excluded: list[str],
        prompt: str,
    ) -> SchemaGap:
        self.gap_calls += 1
        self.prompts.append(prompt)
        return SchemaGap(
            missing_concept=missing_concept,
            purpose=purpose,
            constraints=constraints,
            excluded=excluded,
        )


class FakeEmbedder:
    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in text.lower().replace("_", " ").split():
            vec[hash(tok) % self.dim] += 1.0
        return vec


def _table(
    name: str,
    business: str,
    grain: str,
    *,
    domain: str = "订单",
    aliases: list[str] | None = None,
    comment: str | None = None,
) -> SchemaTable:
    return SchemaTable(
        table_name=name,
        business_name=business,
        domain=domain,
        grain_description=grain,
        comment=comment or grain,
        aliases=aliases or [],
    )


def _col(
    table: str,
    name: str,
    comment: str,
    *,
    data_type: str = "bigint",
    aliases: list[str] | None = None,
) -> SchemaColumn:
    return SchemaColumn(
        table_name=table,
        column_name=name,
        data_type=data_type,
        comment=comment,
        aliases=aliases or [],
    )


def _rel(left: str, lcol: str, right: str, rcol: str) -> TableRelation:
    return TableRelation(
        left_table=left,
        right_table=right,
        left_col=lcol,
        right_col=rcol,
        cardinality="many_to_one",
        source="fk",
        version=1,
    )


def _gmv_metric() -> MetricSpec:
    return MetricSpec(
        metric_id="gmv",
        name="GMV",
        version=1,
        grain_table="fact_order_item",
        formula="SUM(oi.price * oi.qty)",
        time_field="fact_order.created_at",
        unit="CNY",
        filters=[FilterCond(field="fact_order.status", op="in", value=PAID)],
        deps=[
            "fact_order_item.price",
            "fact_order_item.qty",
            "fact_order.status",
            "fact_order.created_at",
        ],
    )


def _refund_metric() -> MetricSpec:
    return MetricSpec(
        metric_id="refund_rate",
        name="退款率",
        version=1,
        grain_table="fact_refund",
        formula="SUM(r.amount) / NULLIF(SUM(oi.pay_amt), 0)",
        time_field="fact_refund.refunded_at",
        unit="ratio",
        filters=[FilterCond(field="fact_refund.status", op="=", value="success")],
        deps=["fact_refund.amount", "fact_order_item.pay_amt"],
    )


def _catalog(*, extra_relations: list[TableRelation] | None = None) -> CatalogSnapshot:
    relations = [
        _rel("fact_order_item", "order_id", "fact_order", "id"),
        _rel("fact_order", "store_id", "dim_store", "id"),
        _rel("fact_order_item", "sku_id", "dim_sku", "id"),
        _rel("fact_refund", "order_id", "fact_order", "id"),
        _rel("fact_refund", "order_item_id", "fact_order_item", "id"),
        _rel("fact_ad_spend", "campaign_id", "dim_campaign", "id"),
    ]
    if extra_relations:
        relations.extend(extra_relations)
    return CatalogSnapshot(
        catalog_version=1,
        tables=[
            _table("fact_order", "订单", "一行一笔订单", aliases=["订单表"]),
            _table(
                "fact_order_item",
                "订单行",
                "一行一个 SKU 明细，GMV grain",
                aliases=["GMV", "订单明细"],
            ),
            _table("fact_refund", "退款", "一行一笔退款", domain="退款", aliases=["退款单"]),
            _table(
                "fact_ad_spend",
                "广告花费",
                "一行=某活动某渠道某月花费",
                domain="营销",
                aliases=["投放花费"],
            ),
            _table("dim_store", "门店", "一行一个线下店或电商仓店", domain="门店"),
            _table("dim_sku", "SKU", "一行一个可售 SKU", domain="商品"),
            _table("dim_campaign", "营销活动", "一行一个投放活动", domain="营销"),
            _table("fact_payment", "支付", "一行一次支付尝试", domain="支付"),
        ],
        columns=[
            _col("fact_order", "id", "订单主键"),
            _col("fact_order", "status", "订单状态", data_type="varchar"),
            _col("fact_order", "created_at", "下单时间", data_type="datetime"),
            _col("fact_order", "store_id", "门店 ID"),
            _col("fact_order_item", "id", "订单行主键"),
            _col("fact_order_item", "order_id", "所属订单"),
            _col("fact_order_item", "sku_id", "SKU ID"),
            _col("fact_order_item", "price", "成交单价", data_type="decimal", aliases=["单价"]),
            _col("fact_order_item", "qty", "成交数量", aliases=["数量"]),
            _col("fact_order_item", "pay_amt", "实付金额", data_type="decimal"),
            _col("fact_order_item", "store_id", "下单门店（行上冗余）"),
            _col("fact_refund", "id", "退款主键"),
            _col("fact_refund", "order_id", "所属订单"),
            _col("fact_refund", "order_item_id", "所属订单行"),
            _col("fact_refund", "amount", "退款金额", data_type="decimal", aliases=["退款额"]),
            _col("fact_refund", "status", "退款状态", data_type="varchar"),
            _col("fact_refund", "refunded_at", "退款时间", data_type="datetime"),
            _col("fact_ad_spend", "id", "花费主键"),
            _col("fact_ad_spend", "campaign_id", "活动 ID"),
            _col("fact_ad_spend", "amount", "广告花费金额", data_type="decimal", aliases=["投放金额"]),
            _col("dim_store", "id", "门店主键"),
            _col("dim_store", "store_name", "门店名", data_type="varchar"),
            _col("dim_sku", "id", "SKU 主键"),
            _col("dim_campaign", "id", "活动主键"),
            _col("fact_payment", "id", "支付主键"),
            _col("fact_payment", "amount", "支付尝试金额", data_type="decimal"),
        ],
        relations=relations,
        metrics=[_gmv_metric(), _refund_metric()],
        write_ops=[],
    )


def _task(metric_ids: list[str], *, dimensions: list[str] | None = None) -> QueryTask:
    return QueryTask(
        task_id="t1",
        metric_ids=metric_ids,
        dimensions=dimensions or [],
        filters=[],
        time_range=TIME_RANGE,
        catalog_version=1,
        permission_version=1,
    )


def _ctx(tables: list[str], *, metrics: list[str] | None = None) -> RuntimeContext:
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
            allowed_tables=tables,
            allowed_columns=[f"data-agent-ecommerce.{t}.*" for t in tables],
            allowed_metrics=metrics or ["gmv", "refund_rate"],
            allowed_write_ops=[],
            catalog_version=1,
            permission_version=1,
        ),
        thread_id="th1",
    )


ALL_TABLES = [
    "fact_order",
    "fact_order_item",
    "fact_refund",
    "fact_ad_spend",
    "dim_store",
    "dim_sku",
    "dim_campaign",
    "fact_payment",
]


def _retrieve(task, ctx, catalog, llm, **kwargs):
    from backend.app.retrieval.schema_rag import retrieve_schema

    return retrieve_schema(task, ctx, catalog, llm=llm, **kwargs)


def test_gmv_recalls_fact_order_and_fact_order_item():
    catalog = _catalog()
    llm = FakeLlm(["GMV 成交总额 订单行 grain", "订单 状态 下单时间"])
    result = _retrieve(_task(["gmv"]), _ctx(ALL_TABLES), catalog, llm)

    assert isinstance(result, SchemaBundle)
    assert "fact_order" in result.tables
    assert "fact_order_item" in result.tables
    assert "fact_order_item.price" in result.columns
    assert "fact_order_item.qty" in result.columns
    assert "fact_order.status" in result.columns
    assert "fact_order.created_at" in result.columns
    pairs = {(j["left"], j["right"]) for j in result.joins}
    assert ("fact_order_item", "fact_order") in pairs
    assert result.catalog_version == 1


def test_gmv_recalls_grain_tables_when_llm_queries_are_irrelevant():
    catalog = _catalog()
    llm = FakeLlm(["无关词", "天气"])
    result = _retrieve(_task(["gmv"]), _ctx(ALL_TABLES), catalog, llm)

    assert isinstance(result, SchemaBundle)
    assert "fact_order" in result.tables
    assert "fact_order_item" in result.tables
    assert "fact_order_item.price" in result.columns
    assert "fact_order_item.qty" in result.columns
    assert "fact_order.status" in result.columns
    assert "fact_order.created_at" in result.columns


def test_metric_dep_tables_included_without_gap_fill():
    catalog = _catalog()
    llm = FakeLlm(["fact_payment 支付尝试"])
    result = _retrieve(
        _task(["gmv"]),
        _ctx(ALL_TABLES),
        catalog,
        llm,
        table_top_k=1,
        max_gap_rounds=0,
    )

    assert isinstance(result, SchemaBundle)
    assert "fact_order" in result.tables
    assert "fact_order_item" in result.tables
    assert llm.gap_calls == 0


def test_same_name_amount_is_not_matched_across_tables():
    catalog = _catalog()
    llm = FakeLlm(["退款金额 refund amount", "订单行实付 pay_amt"])
    result = _retrieve(_task(["refund_rate"]), _ctx(ALL_TABLES), catalog, llm)

    assert isinstance(result, SchemaBundle)
    assert "fact_refund.amount" in result.columns
    assert "fact_ad_spend.amount" not in result.columns
    assert "fact_payment.amount" not in result.columns
    assert "fact_ad_spend" not in result.tables


def test_gap_fill_adds_owning_table_of_missing_column():
    catalog = _catalog()
    llm = FakeLlm(["支付 尝试 amount"])
    result = _retrieve(
        _task(["gmv"], dimensions=["dim_store.store_name"]),
        _ctx(ALL_TABLES),
        catalog,
        llm,
        table_top_k=1,
        column_top_k=10,
        max_gap_rounds=2,
    )

    assert isinstance(result, SchemaBundle)
    assert "dim_store" in result.tables
    assert "dim_store.store_name" in result.columns


def test_no_embedding_degrades_to_bm25_without_hard_fail():
    catalog = _catalog()
    llm = FakeLlm(["GMV 订单行", "订单 状态"])
    result = _retrieve(_task(["gmv"]), _ctx(ALL_TABLES), catalog, llm, embedder=None)

    assert isinstance(result, SchemaBundle)
    assert "fact_order_item" in result.tables


def test_catalog_version_change_rebuilds_vector_index(tmp_path: Path):
    from backend.app.retrieval.vector import ensure_index

    db = tmp_path / "embeddings.sqlite"
    apply_sql(db, SQL_DIR / "embeddings.sql")
    catalog = _catalog()
    embedder = FakeEmbedder()
    ensure_index(catalog, embeddings_db=db, embedder=embedder)
    assert embedder.calls >= 1
    first_calls = embedder.calls

    with sqlite3.connect(db) as conn:
        v1 = conn.execute(
            "SELECT COUNT(*) FROM table_embedding WHERE catalog_version = 1"
        ).fetchone()[0]
    assert v1 > 0

    catalog_v2 = catalog.model_copy(update={"catalog_version": 2})
    ensure_index(catalog_v2, embeddings_db=db, embedder=embedder)
    assert embedder.calls > first_calls

    with sqlite3.connect(db) as conn:
        manifest = conn.execute(
            "SELECT catalog_version FROM embedding_manifest ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        used = conn.execute(
            "SELECT COUNT(*) FROM table_embedding WHERE catalog_version = 2"
        ).fetchone()[0]
        stale = conn.execute(
            "SELECT COUNT(*) FROM table_embedding WHERE catalog_version = 1"
        ).fetchone()[0]
    assert manifest == 2
    assert used > 0
    assert stale == 0


def test_multiple_join_paths_return_ambiguous_not_shortest():
    catalog = _catalog(
        extra_relations=[_rel("fact_order_item", "store_id", "dim_store", "id")]
    )
    llm = FakeLlm(["GMV 订单行", "门店 store_name 门店名"])
    result = _retrieve(
        _task(["gmv"], dimensions=["dim_store.store_name"]),
        _ctx(ALL_TABLES),
        catalog,
        llm,
    )

    assert isinstance(result, Ambiguous)
    assert result.paths
    lengths = [len(p) for p in result.paths]
    assert min(lengths) < max(lengths)


def test_permission_filters_catalog_before_recall():
    catalog = _catalog()
    llm = FakeLlm(["广告花费 amount 投放"])
    allowed = ["fact_order", "fact_order_item"]
    result = _retrieve(_task(["gmv"]), _ctx(allowed, metrics=["gmv"]), catalog, llm)

    assert isinstance(result, SchemaBundle)
    assert "fact_ad_spend" not in result.tables
    assert "fact_ad_spend.amount" not in result.columns
    assert "fact_order_item" in result.tables


def test_vector_search_returns_empty_when_embedder_missing(tmp_path: Path):
    from backend.app.retrieval.vector import search_columns, search_tables

    db = tmp_path / "embeddings.sqlite"
    apply_sql(db, SQL_DIR / "embeddings.sql")
    assert search_tables("GMV", catalog_version=1, embeddings_db=db, embedder=None) == []
    assert search_columns("amount", catalog_version=1, embeddings_db=db, embedder=None) == []


def test_chat_llm_retrieval_survives_think_only_output():
    from backend.app.config import LlmSettings
    from backend.app.llm.client import ChatLlm

    llm = ChatLlm(LlmSettings(base_url="http://example.invalid", api_key="k", model="m"))
    llm._chat = lambda *a, **k: "<think>need order grain tables for GMV</think>"
    assert llm.table_queries(_task(["gmv"]), "p") == []
    gap = llm.schema_gap(
        missing_concept="fact_order.status",
        purpose="query_coverage",
        constraints=["fact_order.status"],
        excluded=["dim_sku"],
        prompt="p",
    )
    assert gap.missing_concept == "fact_order.status"
    assert gap.constraints == ["fact_order.status"]
    assert gap.excluded == ["dim_sku"]
    llm._chat = lambda *a, **k: "<think>drafting skeleton</think>"
    from backend.app.types import SchemaBundle

    empty = llm.query_skeleton(
        _task(["gmv"]),
        SchemaBundle(tables=[], columns=[], joins=[], catalog_version=1),
        "p",
    )
    assert empty.metric_ids == []
    assert empty.joins == []


def test_retrieve_survives_unparseable_schema_gap_llm():
    import json

    class BoomLlm(FakeLlm):
        def schema_gap(self, **kwargs):
            raise json.JSONDecodeError("Expecting value", " ", 0)

    result = _retrieve(
        _task(["gmv"]),
        _ctx(ALL_TABLES),
        _catalog(),
        BoomLlm(["无关词"]),
    )
    assert isinstance(result, (SchemaBundle, SchemaGap))


def test_retrieval_modules_do_not_call_interrupt():
    from backend.app.retrieval import bm25, schema_rag, vector

    for mod in (bm25, schema_rag, vector):
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert "interrupt" not in names
        assert "interrupt" not in attrs
        assert "interrupt(" not in src
