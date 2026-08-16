# Schema RAG uses a MySQL authority and manifest-switched Milvus collections

Status: Accepted

Physical table, column and foreign-key facts are collected from an explicitly
allowlisted MySQL `information_schema`. Curated metric definitions, aliases,
classifications and verified joins remain authoritative business metadata.
`catalog_version` is a content hash over both physical and curated evidence;
Milvus is never treated as the source of truth.

The offline index contains four physical collection layers: source/domain,
object/metric, field/entity and relation. FastEmbed or an explicitly configured
OpenAI-compatible embedding provider creates real dense vectors. A MySQL term
index provides BM25 scores, and online retrieval fuses the authorized BM25 and
dense candidates before an LLM reranker. Only a single unambiguous dense metric
may be bound when no exact catalog ID, name or alias matches.

Every build writes version-suffixed staging collections, validates their schema,
embedding dimension and exact row counts, then atomically changes the active
MySQL manifest. Milvus Lite does not support collection aliases, so collection
names cannot be switched atomically inside Lite itself. Previous active
collections are retained until a separate cleanup operation; an interrupted
build cannot replace the active manifest.

Online requests require exact agreement among the authoritative catalog
version, active index version, embedding provider/model and vector dimension.
Source filters and denied field classifications are included in both Milvus
filter expressions and MySQL BM25 SQL before candidates enter the reranker.
High-sensitivity field names are omitted from object-level embedding documents.

The ContextBudgeter keeps metric dependencies, dimensions and time fields
before optional fields and aliases. If required evidence alone exceeds the
budget, retrieval fails with `RAG_CONTEXT_BUDGET_EXCEEDED` instead of silently
dropping evidence.
