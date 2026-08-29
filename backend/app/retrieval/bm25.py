from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from backend.app.catalog.models import SchemaColumn, SchemaTable

_WORD = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_HAN = re.compile(r"[\u4e00-\u9fff]+")
DB = "data-agent-ecommerce"


@dataclass(frozen=True)
class Hit:
    key: str
    score: float


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    for word in _WORD.findall(text):
        tokens.append(word)
        if "_" in word:
            tokens.extend(part for part in word.split("_") if part)
    for run in _HAN.findall(text):
        tokens.append(run)
        tokens.extend(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def table_doc(table: SchemaTable) -> str:
    parts = [
        table.table_name,
        table.table_name.replace("_", " "),
        table.business_name,
        table.domain,
        table.grain_description,
        table.comment or "",
        *table.aliases,
    ]
    return " ".join(part for part in parts if part)


def column_doc(column: SchemaColumn) -> str:
    full = f"{DB}.{column.table_name}.{column.column_name}"
    short = f"{column.table_name}.{column.column_name}"
    parts = [
        full,
        short,
        column.table_name,
        column.column_name,
        column.comment or "",
        column.data_type,
        *column.aliases,
    ]
    return " ".join(part for part in parts if part)


def _search(query: str, keys: list[str], corpus: list[str], top_k: int) -> list[Hit]:
    if not keys or top_k <= 0:
        return []
    tokens = tokenize(query)
    if not tokens:
        return []
    tokenized = [tokenize(doc) or [""] for doc in corpus]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(tokens)
    ranked = sorted(zip(keys, scores), key=lambda item: item[1], reverse=True)
    hits = [Hit(key=key, score=float(score)) for key, score in ranked[:top_k] if score > 0]
    if hits:
        return hits
    return [Hit(key=key, score=float(score)) for key, score in ranked[:top_k]]


def search_tables(query: str, tables: list[SchemaTable], top_k: int) -> list[Hit]:
    return _search(query, [t.table_name for t in tables], [table_doc(t) for t in tables], top_k)


def search_columns(
    query: str,
    columns: list[SchemaColumn],
    top_k: int,
    *,
    table_names: set[str] | None = None,
) -> list[Hit]:
    if table_names is not None:
        columns = [c for c in columns if c.table_name in table_names]
    keys = [f"{c.table_name}.{c.column_name}" for c in columns]
    return _search(query, keys, [column_doc(c) for c in columns], top_k)
