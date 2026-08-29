from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


class LimitExceeded(Exception):
    pass


def parse_byte_size(value: int | str) -> int:
    if isinstance(value, int):
        return value
    text = value.strip().upper()
    for suffix, mul in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024), ("B", 1)):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)])) * mul
    return int(text)


class ParquetStreamWriter:
    def __init__(self, path: Path, *, max_rows: int, max_bytes: int) -> None:
        self.path = path
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self.row_count = 0
        self.columns: list[str] = []
        self._writer: pq.ParquetWriter | None = None
        self._schema: pa.Schema | None = None
        self._closed = False

    def write_batch(self, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        if self._closed:
            raise RuntimeError("writer is closed")
        table = pa.Table.from_pylist(list(rows))
        if self.row_count + table.num_rows > self.max_rows:
            raise LimitExceeded("max_rows exceeded")
        if self._writer is None:
            self.columns = list(table.schema.names)
            self._schema = table.schema
            self._writer = pq.ParquetWriter(str(self.path), table.schema)
        else:
            table = table.cast(self._schema)
        self._writer.write_table(table)
        self.row_count += table.num_rows
        if self.path.stat().st_size > self.max_bytes:
            raise LimitExceeded("max_bytes exceeded")

    def write_empty(self, columns: Sequence[str]) -> None:
        if self._closed:
            raise RuntimeError("writer is closed")
        if self._writer is not None:
            return
        fields = [pa.field(name, pa.null()) for name in columns]
        schema = pa.schema(fields)
        self.columns = list(columns)
        self._schema = schema
        self._writer = pq.ParquetWriter(str(self.path), schema)

    def close(self) -> None:
        if self._closed:
            return
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._closed = True
