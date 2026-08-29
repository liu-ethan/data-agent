from __future__ import annotations

import sqlite3
import struct
from math import sqrt
from pathlib import Path
from typing import Protocol

from backend.app.catalog.models import CatalogSnapshot
from backend.app.retrieval.bm25 import Hit, column_doc, table_doc
from scripts.init_sqlite import SQL_DIR, apply_sql


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    na = sqrt(sum(a * a for a in left))
    nb = sqrt(sum(b * b for b in right))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _connect(embeddings_db: str | Path) -> sqlite3.Connection:
    path = Path(embeddings_db)
    if not path.exists() or path.stat().st_size == 0:
        apply_sql(path, SQL_DIR / "embeddings.sql")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_index(
    catalog: CatalogSnapshot,
    *,
    embeddings_db: str | Path,
    embedder: Embedder | None,
) -> None:
    if embedder is None:
        return
    with _connect(embeddings_db) as conn:
        row = conn.execute(
            "SELECT catalog_version FROM embedding_manifest ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is not None and int(row[0]) == catalog.catalog_version:
            return
        conn.execute("DELETE FROM table_embedding")
        conn.execute("DELETE FROM column_embedding")
        conn.execute("DELETE FROM embedding_manifest")
        table_names = [t.table_name for t in catalog.tables]
        table_texts = [table_doc(t) for t in catalog.tables]
        table_vecs = embedder.embed(table_texts) if table_texts else []
        for name, text, vec in zip(table_names, table_texts, table_vecs):
            conn.execute(
                """INSERT INTO table_embedding
                   (table_name, catalog_version, text, vector)
                   VALUES (?, ?, ?, ?)""",
                (name, catalog.catalog_version, text, _pack(vec)),
            )
        col_rows = [
            (c.table_name, c.column_name, column_doc(c)) for c in catalog.columns
        ]
        col_vecs = embedder.embed([row[2] for row in col_rows]) if col_rows else []
        for (table_name, column_name, text), vec in zip(col_rows, col_vecs):
            conn.execute(
                """INSERT INTO column_embedding
                   (table_name, column_name, catalog_version, text, vector)
                   VALUES (?, ?, ?, ?, ?)""",
                (table_name, column_name, catalog.catalog_version, text, _pack(vec)),
            )
        dim = len(table_vecs[0]) if table_vecs else (len(col_vecs[0]) if col_vecs else 0)
        conn.execute(
            """INSERT INTO embedding_manifest (model, dim, catalog_version, built_at)
               VALUES (?, ?, ?, datetime('now'))""",
            ("injected", dim, catalog.catalog_version),
        )
        conn.commit()


def search_tables(
    query: str,
    *,
    catalog_version: int,
    embeddings_db: str | Path,
    embedder: Embedder | None,
    top_k: int = 5,
) -> list[Hit]:
    if embedder is None or top_k <= 0:
        return []
    qvec = embedder.embed([query])[0]
    with _connect(embeddings_db) as conn:
        rows = conn.execute(
            """SELECT table_name, vector FROM table_embedding
               WHERE catalog_version = ?""",
            (catalog_version,),
        ).fetchall()
    scored = [(name, _cosine(qvec, _unpack(blob))) for name, blob in rows]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [Hit(key=name, score=score) for name, score in scored[:top_k] if score > 0]


def search_columns(
    query: str,
    *,
    catalog_version: int,
    embeddings_db: str | Path,
    embedder: Embedder | None,
    top_k: int = 10,
    table_names: set[str] | None = None,
) -> list[Hit]:
    if embedder is None or top_k <= 0:
        return []
    qvec = embedder.embed([query])[0]
    sql = """SELECT table_name, column_name, vector FROM column_embedding
             WHERE catalog_version = ?"""
    params: list[object] = [catalog_version]
    with _connect(embeddings_db) as conn:
        rows = conn.execute(sql, params).fetchall()
    scored: list[Hit] = []
    for table_name, column_name, blob in rows:
        if table_names is not None and table_name not in table_names:
            continue
        scored.append(
            Hit(
                key=f"{table_name}.{column_name}",
                score=_cosine(qvec, _unpack(blob)),
            )
        )
    scored.sort(key=lambda hit: hit.score, reverse=True)
    return [hit for hit in scored[:top_k] if hit.score > 0]
