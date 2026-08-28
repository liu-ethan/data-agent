from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.catalog.metrics import get_metric
from backend.app.catalog.store import CatalogStore
from scripts.init_sqlite import ALL_METRICS, ALL_TABLES, METRICS, SQL_DIR, apply_sql, seed_catalog


@pytest.fixture
def catalog_db(tmp_path: Path) -> Path:
    db = tmp_path / "catalog.sqlite"
    apply_sql(db, SQL_DIR / "catalog.sql")
    seed_catalog(db)
    return db


def test_loads_ten_reviewed_metrics(catalog_db: Path):
    store = CatalogStore(catalog_db)
    snapshot = store.load()
    ids = {m.metric_id for m in snapshot.metrics}
    assert ids == set(ALL_METRICS)
    assert len(snapshot.metrics) == 10
    gmv = get_metric("gmv", catalog_db=catalog_db)
    expected = next(m for m in METRICS if m["metric_id"] == "gmv")
    assert gmv.grain_table == "fact_order_item"
    assert gmv.formula == expected["formula"]
    assert gmv.time_field == expected["time_field"]
    aov = get_metric("aov", catalog_db=catalog_db)
    assert aov.grain_table == "fact_order"


def test_unknown_metric_id_raises(catalog_db: Path):
    with pytest.raises(LookupError, match="metric"):
        get_metric("not_a_metric", catalog_db=catalog_db)


def test_cvr_and_ad_roi_needs_tables_resolve_in_catalog(catalog_db: Path):
    tables = set(ALL_TABLES)
    cvr = get_metric("cvr", catalog_db=catalog_db)
    ad_roi = get_metric("ad_roi", catalog_db=catalog_db)
    assert cvr.needs_tables == ["fact_traffic"]
    assert ad_roi.needs_tables == ["fact_ad_spend", "dim_campaign"]
    assert set(cvr.needs_tables) <= tables
    assert set(ad_roi.needs_tables) <= tables
    snapshot_tables = {t.table_name for t in CatalogStore(catalog_db).load().tables}
    assert set(cvr.needs_tables) <= snapshot_tables
    assert set(ad_roi.needs_tables) <= snapshot_tables
