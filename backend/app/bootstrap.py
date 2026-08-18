"""Application composition root.

Only this module selects concrete MySQL, Milvus and model adapters. Delivery
code and graph nodes receive already-constructed dependencies and therefore
cannot silently switch to local test doubles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .auth import JWTAuthenticator
from .config import Settings
from .errors import RuntimeAgentError
from .gateways import ReadGateway, WriteGateway
from .graph import RuntimeGraph
from .repositories.catalog import MySQLCatalogRepository
from .repositories.catalog_index import MilvusCatalogIndex
from .repositories.data import MySQLDataRepository
from .repositories.mutation import MySQLMutationRepository
from .repositories.runtime import PersistentResultRepository, RuntimePersistence
from .services.catalog_retrieval import LLMReranker, ProductionCatalogRetrievalService
from .services.embedding import build_embedder
from .services.llm import StructuredLLM
from .services.permission import PermissionService


class UnconfiguredRetrieval:
    async def retrieve(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeAgentError(
            "RAG_NOT_CONFIGURED",
            "Milvus and an embedding model must be configured before data queries are enabled",
        )


@dataclass(frozen=True)
class RuntimeContainer:
    settings: Settings
    authenticator: JWTAuthenticator
    persistence: RuntimePersistence
    permissions: PermissionService
    gateway: ReadGateway
    llm: StructuredLLM
    catalog_repository: MySQLCatalogRepository
    catalog_index: MilvusCatalogIndex | None
    embedder: Any | None
    retrieval: Any
    graph: RuntimeGraph
    rag_error: str | None


def build_runtime_container(settings: Settings) -> RuntimeContainer:
    """Wire the production runtime; never seed or create an in-memory database."""
    authenticator = JWTAuthenticator(settings.raw.get("auth", {}))
    control_configured = bool(settings.mysql.get("accounts", {}).get("control"))
    if not control_configured and settings.app.environment != "local":
        raise RuntimeError("MySQL control account is not configured")
    # Local installations created before the control account migration retain
    # an explicit compatibility path. Production never falls back to the DDL
    # migration identity.
    persistence = RuntimePersistence(
        settings.mysql,
        account_name="control" if control_configured else "migration",
    )
    permissions = PermissionService(persistence, settings.permissions)
    data = MySQLDataRepository(
        settings.mysql,
        max_execution_ms=int(settings.read_query.get("max_execution_ms", 5000)),
    )
    gateway = ReadGateway(
        data=data,
        results=PersistentResultRepository(persistence),
        settings=settings.read_query,
    )
    writer = MySQLMutationRepository(settings.mysql)
    write_gateway = WriteGateway(
        data=writer,
        auditor=persistence,
        settings=settings.raw.get("write_query", {}),
    )
    llm = StructuredLLM(settings.raw.get("llm", {}))
    catalog_repository = MySQLCatalogRepository(persistence)
    catalog_index: MilvusCatalogIndex | None = None
    embedder: Any | None = None
    try:
        catalog_index = MilvusCatalogIndex(settings.raw.get("milvus", {}))
        embedder = build_embedder(settings.raw.get("llm", {}))
        configured_sources = list(settings.permissions.get(
            "allowed_source_ids", ["mysql_ecommerce_local"]))
        catalog_version = catalog_repository.version(configured_sources)
        manifest = catalog_repository.active_manifest(catalog_version)
        catalog_index.validate_manifest(manifest)
        if (manifest.embedding_provider != getattr(embedder, "provider", None)
                or manifest.embedding_model != getattr(embedder, "model_name", None)):
            raise RuntimeAgentError(
                "RAG_EMBEDDING_MODEL_MISMATCH",
                "configured embedding model does not match the active index")
        retrieval: Any = ProductionCatalogRetrievalService(
            catalog_repository,
            catalog_index,
            embedder,
            LLMReranker(llm),
            max_sources=int(settings.retrieval_budget.get("max_source_candidates", 3)),
            max_objects=int(settings.retrieval_budget.get("max_object_candidates", 5)),
            max_fields=int(settings.retrieval_budget.get("max_fields_per_object", 8)),
            max_join_hops=int(settings.retrieval_budget.get("max_join_hops", 2)),
            max_tokens=int(settings.retrieval_budget.get("max_context_tokens", 3000)),
            min_score=float(settings.retrieval_budget.get("min_rerank_score", 0.55)),
            ambiguity_gap=float(settings.retrieval_budget.get("ambiguity_score_gap", 0.08)),
        )
        rag_error = None
    except RuntimeAgentError as exc:
        retrieval, rag_error = UnconfiguredRetrieval(), exc.error_code
    except Exception:
        # A missing migration or corrupt manifest keeps the service degraded;
        # provider/driver details stay out of the public health response.
        retrieval, rag_error = UnconfiguredRetrieval(), "RAG_INDEX_MISSING"
    graph = RuntimeGraph(
        settings=settings.raw,
        retrieval=retrieval,
        gateway=gateway,
        write_gateway=write_gateway,
        llm=llm,
        persistence=persistence,
    )
    return RuntimeContainer(
        settings=settings,
        authenticator=authenticator,
        persistence=persistence,
        permissions=permissions,
        gateway=gateway,
        llm=llm,
        catalog_repository=catalog_repository,
        catalog_index=catalog_index,
        embedder=embedder,
        retrieval=retrieval,
        graph=graph,
        rag_error=rag_error,
    )
