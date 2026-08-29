from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.results.duckdb_filter import compile_local_filter, filter_parquet
from backend.app.types import FilterCond, LocalFilterSpec

COLUMNS = ["sku_id", "gmv", "store"]


def _write_parquet(path: Path) -> None:
    table = pa.table(
        {
            "sku_id": ["s1", "s2", "s3", "s4"],
            "gmv": [10, 30, 20, 40],
            "store": ["a", "a", "b", "b"],
        }
    )
    pq.write_table(table, path)


def test_compile_parameterizes_filter_values(tmp_path: Path):
    path = tmp_path / "r.parquet"
    spec = LocalFilterSpec(filters=[FilterCond(field="sku_id", op="=", value="evil'; DROP TABLE x;--")])
    sql, params = compile_local_filter(path, spec, COLUMNS)
    assert "evil" not in sql
    assert "DROP TABLE" not in sql
    assert params[-1] == "evil'; DROP TABLE x;--"
    assert "?" in sql


def test_compile_rejects_unknown_column(tmp_path: Path):
    path = tmp_path / "r.parquet"
    spec = LocalFilterSpec(filters=[FilterCond(field="secret", op="=", value=1)])
    with pytest.raises(ValueError, match="secret"):
        compile_local_filter(path, spec, COLUMNS)


def test_compile_rejects_raw_sql_select(tmp_path: Path):
    path = tmp_path / "r.parquet"
    spec = LocalFilterSpec(select=["gmv; DELETE FROM t"])
    with pytest.raises(ValueError):
        compile_local_filter(path, spec, COLUMNS)


def test_filter_sort_select_topn(tmp_path: Path):
    path = tmp_path / "r.parquet"
    _write_parquet(path)
    rows = filter_parquet(
        path,
        LocalFilterSpec(
            filters=[FilterCond(field="store", op="=", value="a")],
            order_by=["gmv DESC"],
            select=["sku_id", "gmv"],
            topn=1,
        ),
        COLUMNS,
    )
    assert rows == [{"sku_id": "s2", "gmv": 30}]


def test_filter_in_and_like(tmp_path: Path):
    path = tmp_path / "r.parquet"
    _write_parquet(path)
    rows = filter_parquet(
        path,
        LocalFilterSpec(
            filters=[
                FilterCond(field="sku_id", op="in", value=["s1", "s3"]),
                FilterCond(field="store", op="like", value="b"),
            ]
        ),
        COLUMNS,
    )
    assert [r["sku_id"] for r in rows] == ["s3"]


def test_local_filter_spec_has_no_sql_field():
    assert "sql" not in LocalFilterSpec.model_fields
