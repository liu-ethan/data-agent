from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.app.catalog.metrics import get_metric
from backend.app.catalog.store import CatalogStore, list_reviewed_edges
from backend.app.catalog.sync import apply_information_schema, sync_from_mysql
from scripts.init_sqlite import ALL_TABLES, METRICS, RELATIONS, SQL_DIR, apply_sql, seed_catalog


@pytest.fixture
def catalog_db(tmp_path: Path) -> Path:
    db = tmp_path / "catalog.sqlite"
    apply_sql(db, SQL_DIR / "catalog.sql")
    seed_catalog(db)
    return db


def test_reviewed_graph_matches_seed_relations(catalog_db: Path):
    edges = list_reviewed_edges(catalog_db=catalog_db)
    expected = {(left, lcol, right, rcol) for left, lcol, right, rcol, *_ in RELATIONS}
    got = {(e.left_table, e.left_col, e.right_table, e.right_col) for e in edges}
    assert got == expected
    assert all(e.source == "fk" for e in edges)
    assert all(e.cardinality == "many_to_one" for e in edges)


def test_relation_graph_has_no_llm_guessed_edges(catalog_db: Path):
    edges = list_reviewed_edges(catalog_db=catalog_db)
    assert edges
    assert all(e.source in {"fk", "human"} for e in edges)
    assert not any(e.source == "llm" for e in edges)
    snapshot = CatalogStore(catalog_db).load()
    assert all(rel.source in {"fk", "human"} for rel in snapshot.relations)
    assert "llm" not in {rel.source for rel in snapshot.relations}


def test_ensure_physical_schema_syncs_when_columns_empty(catalog_db: Path, monkeypatch):
    from backend.app.catalog.sync import apply_information_schema, ensure_physical_schema

    called: list[Path] = []

    def fake_sync(*, catalog_db, engine=None):
        called.append(Path(catalog_db))
        return apply_information_schema(
            catalog_db,
            tables=[{"table_name": "dim_sku", "comment": "SKU"}],
            columns=[
                {
                    "table_name": "dim_sku",
                    "column_name": "sku_name",
                    "data_type": "varchar",
                    "comment": "SKU 商品名",
                }
            ],
            foreign_keys=[
                {
                    "left_table": "dim_sku",
                    "left_col": "category_id",
                    "right_table": "dim_category",
                    "right_col": "id",
                }
            ],
            mysql_database="data-agent-ecommerce",
        )

    monkeypatch.setattr("backend.app.catalog.sync.sync_from_mysql", fake_sync)
    version = ensure_physical_schema(catalog_db=catalog_db)
    assert called == [catalog_db]
    snap = CatalogStore(catalog_db).load()
    assert version == snap.catalog_version
    assert ("dim_sku", "sku_name") in {(c.table_name, c.column_name) for c in snap.columns}


def test_ensure_physical_schema_skips_when_columns_exist(catalog_db: Path, monkeypatch):
    from backend.app.catalog.sync import apply_information_schema, ensure_physical_schema

    apply_information_schema(
        catalog_db,
        tables=[{"table_name": "dim_sku", "comment": "SKU"}],
        columns=[
            {
                "table_name": "dim_sku",
                "column_name": "sku_name",
                "data_type": "varchar",
                "comment": "SKU 商品名",
            }
        ],
        foreign_keys=[],
        mysql_database="data-agent-ecommerce",
    )
    monkeypatch.setattr(
        "backend.app.catalog.sync.sync_from_mysql",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not sync")),
    )
    version = ensure_physical_schema(catalog_db=catalog_db)
    assert version == CatalogStore(catalog_db).load().catalog_version
    assert CatalogStore(catalog_db).load().columns


def test_physical_sync_preserves_metrics_and_write_ops_and_bumps_version(catalog_db: Path):
    store = CatalogStore(catalog_db)
    gmv_before = store.get_metric("gmv").formula
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "UPDATE write_op SET sql_template = 'TAMPERED' "
            "WHERE operation_type = 'update_sku_status'"
        )
        conn.commit()

    version = apply_information_schema(
        catalog_db,
        tables=[
            {"table_name": "dim_sku", "comment": "SKU from mysql"},
            {"table_name": "da_write_receipt", "comment": "must not enter catalog"},
        ],
        columns=[
            {
                "table_name": "dim_sku",
                "column_name": "sku_name",
                "data_type": "varchar",
                "comment": "SKU 商品名",
            },
            {
                "table_name": "da_write_receipt",
                "column_name": "operation_id",
                "data_type": "varchar",
                "comment": "must not enter catalog",
            },
        ],
        foreign_keys=[
            {
                "left_table": "dim_sku",
                "left_col": "category_id",
                "right_table": "dim_category",
                "right_col": "id",
            }
        ],
        mysql_database="data-agent-ecommerce",
    )
    assert version == 2
    snap = store.load()
    assert snap.catalog_version == 2
    assert store.get_metric("gmv").formula == gmv_before
    tampered = next(op for op in snap.write_ops if op.operation_type == "update_sku_status")
    assert tampered.sql_template == "TAMPERED"
    names = {t.table_name for t in snap.tables}
    assert "dim_sku" in names
    assert "fact_order" in names
    assert "da_write_receipt" not in names
    sku = next(t for t in snap.tables if t.table_name == "dim_sku")
    assert sku.comment == "SKU from mysql"
    assert sku.domain == "商品"
    col_keys = {(c.table_name, c.column_name) for c in snap.columns}
    assert ("dim_sku", "sku_name") in col_keys
    assert ("da_write_receipt", "operation_id") not in col_keys
    edges = list_reviewed_edges(catalog_db=catalog_db)
    assert len(edges) == 1
    assert edges[0].source == "fk"
    assert edges[0].cardinality == "many_to_one"
    assert edges[0].left_table == "dim_sku"
    assert edges[0].right_table == "dim_category"


def _connect_reader_or_skip():
    if not Path("config.yaml").exists():
        pytest.skip("config.yaml missing")
    from backend.app.mysql.pool import get_engine

    try:
        engine = get_engine("reader")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:  # noqa: BLE001 — skip unless MySQL reader is reachable
        pytest.skip(f"MySQL reader unreachable: {exc}")


@pytest.mark.integration
def test_sync_from_mysql_imports_information_schema(catalog_db: Path):
    _connect_reader_or_skip()
    version = sync_from_mysql(catalog_db=catalog_db)
    assert version == 2
    snap = CatalogStore(catalog_db).load()
    assert snap.catalog_version == 2
    assert {t.table_name for t in snap.tables} == set(ALL_TABLES)
    assert "da_write_receipt" not in {t.table_name for t in snap.tables}
    assert "da_write_audit" not in {t.table_name for t in snap.tables}
    col_keys = {(c.table_name, c.column_name) for c in snap.columns}
    assert ("dim_sku", "sku_name") in col_keys
    assert ("dim_user", "nick_name") in col_keys
    sku_name = next(
        c for c in snap.columns if c.table_name == "dim_sku" and c.column_name == "sku_name"
    )
    assert sku_name.comment
    assert sku_name.is_sensitive is False
    expected_gmv = next(m for m in METRICS if m["metric_id"] == "gmv")
    assert get_metric("gmv", catalog_db=catalog_db).formula == expected_gmv["formula"]
    edges = list_reviewed_edges(catalog_db=catalog_db)
    assert all(e.source == "fk" for e in edges)
    assert all(e.cardinality == "many_to_one" for e in edges)
    expected = {(left, lcol, right, rcol) for left, lcol, right, rcol, *_ in RELATIONS}
    got = {(e.left_table, e.left_col, e.right_table, e.right_col) for e in edges}
    assert got == expected

    version2 = sync_from_mysql(catalog_db=catalog_db)
    assert version2 == 3
    assert CatalogStore(catalog_db).load().catalog_version == 3
