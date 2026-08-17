from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from backend.app.repositories.catalog import MySQLCatalogRepository
from backend.app.services.schema_catalog import (
    MySQLSchemaCollector,
    classify_field,
    content_version,
    lexical_tokens,
)


def information_schema_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS information_schema")
        connection.exec_driver_sql("""
            CREATE TABLE information_schema.TABLES(
              TABLE_SCHEMA TEXT,TABLE_NAME TEXT,TABLE_TYPE TEXT,TABLE_COMMENT TEXT)
        """)
        connection.exec_driver_sql("""
            CREATE TABLE information_schema.COLUMNS(
              TABLE_SCHEMA TEXT,TABLE_NAME TEXT,COLUMN_NAME TEXT,
              ORDINAL_POSITION INT,DATA_TYPE TEXT,COLUMN_TYPE TEXT,
              IS_NULLABLE TEXT,COLUMN_KEY TEXT,COLUMN_COMMENT TEXT)
        """)
        connection.exec_driver_sql("""
            CREATE TABLE information_schema.KEY_COLUMN_USAGE(
              TABLE_SCHEMA TEXT,CONSTRAINT_NAME TEXT,TABLE_NAME TEXT,COLUMN_NAME TEXT,
              REFERENCED_TABLE_NAME TEXT,REFERENCED_COLUMN_NAME TEXT,ORDINAL_POSITION INT)
        """)
        connection.execute(text("""
            INSERT INTO information_schema.TABLES VALUES
              ('commerce','orders','BASE TABLE','orders fact'),
              ('commerce','shops','BASE TABLE','shop dimension'),
              ('commerce','runtime_events','BASE TABLE','control plane')
        """))
        connection.execute(text("""
            INSERT INTO information_schema.COLUMNS VALUES
              ('commerce','orders','order_id',1,'varchar','varchar(64)','NO','PRI',''),
              ('commerce','orders','shop_id',2,'varchar','varchar(64)','NO','MUL',''),
              ('commerce','orders','paid_at',3,'datetime','datetime','YES','','pay time'),
              ('commerce','orders','pay_amount',4,'decimal','decimal(18,2)','NO','','paid'),
              ('commerce','shops','shop_id',1,'varchar','varchar(64)','NO','PRI',''),
              ('commerce','shops','phone',2,'varchar','varchar(32)','NO','','sensitive')
        """))
        connection.execute(text("""
            INSERT INTO information_schema.KEY_COLUMN_USAGE VALUES
              ('commerce','fk_orders_shop','orders','shop_id','shops','shop_id',1)
        """))
    return engine


def test_mysql_information_schema_collection_is_allowlisted_and_versioned():
    collector = MySQLSchemaCollector(information_schema_engine(), {
        "database": "commerce", "source_id": "source_commerce",
        "name": "Commerce", "domain": "TRADE",
        "include_tables": ["orders", "shops"],
        "grain_overrides": {"orders": "order", "shops": "shop"},
    })
    first = collector.collect()
    second = collector.collect()
    assert first.catalog_version == second.catalog_version
    assert first.catalog_version.startswith("catalog_")
    assert [item.name for item in first.objects] == ["orders", "shops"]
    assert "runtime_events" not in {item.name for item in first.objects}
    assert len(first.fields) == 6
    assert first.relations[0].left_table == "orders"
    paid_at = next(item for item in first.fields if item.field_name == "paid_at")
    phone = next(item for item in first.fields if item.field_name == "phone")
    assert (paid_at.classification, paid_at.is_time_field) == ("BUSINESS_TIME", True)
    assert phone.classification == "PHONE"


def test_catalog_version_and_lexical_tokens_are_content_sensitive():
    assert content_version({"a": 1}) == content_version({"a": 1})
    assert content_version({"a": 1}) != content_version({"a": 2})
    tokens = lexical_tokens("昨天各品类 GMV 与 order_items")
    assert "gmv" in tokens and "order" in tokens and "items" in tokens
    assert "品类" in tokens and "品" in tokens and "类" in tokens
    assert classify_field("id_number", "varchar")[0] == "ID_CARD"


def test_bm25_prefers_exact_repeated_term_and_normalizes_scores():
    rows = [
        {"document_id": "orders", "target_id": "obj_orders", "layer": "object",
         "document_kind": "object", "source_id": "s", "object_id": "obj_orders",
         "document_length": 10, "term": "gmv", "term_frequency": 3},
        {"document_id": "refunds", "target_id": "obj_refunds", "layer": "object",
         "document_kind": "object", "source_id": "s", "object_id": "obj_refunds",
         "document_length": 10, "term": "gmv", "term_frequency": 1},
    ]
    ranked = MySQLCatalogRepository._bm25(
        rows, n=2, avgdl=10, dfs={"gmv": 2}, limit=2)
    assert [item["target_id"] for item in ranked] == ["obj_orders", "obj_refunds"]
    assert ranked[0]["score"] == 1
    assert 0 < ranked[1]["score"] < 1
