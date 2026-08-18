"""Stable application errors and safe FastAPI serialization."""

from __future__ import annotations

import os
from typing import Any

from .models import AppError


class RuntimeAgentError(Exception):
    def __init__(self, error_code: str, message: str, *, retryable: bool = False,
                 details: dict[str, Any] | None = None) -> None:
        # Spec 00 §6: error codes must come from the registry. The env-var
        # escape hatch exists for negative tests that intentionally use
        # unregistered codes; production code paths never set it.
        if error_code not in ERROR_MESSAGES and not os.environ.get(
                "DRA_ALLOW_UNREGISTERED_ERROR_CODES"):
            raise ValueError(
                f"error_code {error_code!r} is not registered in ERROR_MESSAGES; "
                f"add it to backend/app/errors.py before using it"
            )
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def as_model(self, trace_id: str) -> AppError:
        return AppError(error_code=self.error_code, message=self.message,
                        trace_id=trace_id, retryable=self.retryable, details=self.details)


ERROR_MESSAGES = {
    "CONFIG_MISSING": "Required configuration is missing",
    "LLM_NOT_CONFIGURED": "The LLM provider is not configured",
    "LLM_NETWORK_CONFIG_ERROR": "The LLM network configuration is invalid",
    "LLM_TIMEOUT": "The LLM request timed out",
    "LLM_AUTH_FAILED": "The LLM provider rejected the configured credentials",
    "LLM_RATE_LIMITED": "The LLM provider rate limit was exceeded",
    "LLM_PROVIDER_ERROR": "The LLM provider request failed",
    "LLM_RESPONSE_INVALID": "The LLM returned an invalid structured response",
    "RAG_NOT_CONFIGURED": "Schema RAG is not configured",
    "RAG_CONNECTION_FAILED": "The Milvus connection failed",
    "RAG_INDEX_MISSING": "The active Schema RAG index is missing",
    "RAG_INDEX_INVALID": "The active Schema RAG index is invalid",
    "RAG_SEARCH_FAILED": "Schema vector search failed",
    "RAG_EMBEDDING_FAILED": "The embedding request failed",
    "RAG_EMBEDDING_DIMENSION_MISMATCH": "The embedding dimension does not match the index",
    "RAG_EMBEDDING_MODEL_MISMATCH": "The embedding model does not match the index",
    "RAG_RERANK_FAILED": "The schema reranker returned an invalid result",
    "RAG_CONTEXT_BUDGET_EXCEEDED": "Required schema evidence exceeds the context budget",
    "CATALOG_VERSION_MISMATCH": "The catalog and index versions are inconsistent",
    "CATALOG_COLLECTION_FAILED": "MySQL schema metadata collection failed",
    "CATALOG_MIGRATION_REQUIRED": "The Schema RAG migration has not been applied",
    "SQL_PARSE_ERROR": "The SQL could not be parsed safely",
    "SQL_FORBIDDEN_OPERATION": "Only a single read query is allowed",
    "SQL_OBJECT_NOT_ALLOWED": "The query references an unavailable object",
    "PERMISSION_DENIED": "The requested data is not within the current permission scope",
    "QUERY_SPEC_MISMATCH": "The SQL does not match its approved query specification",
    "MISSING_TIME_FILTER": "A fact-table query requires a time filter",
    "QUERY_TOO_EXPENSIVE": "The query exceeds the configured cost limit",
    "EXPLAIN_FAILED": "The query could not pass the cost check",
    "QUERY_TIMEOUT": "The query exceeded the execution timeout",
    "QUERY_EXECUTION_FAILED": "The database could not execute the read query",
    "READER_ACCOUNT_INVALID": "The configured analytical account is invalid",
    "READER_ACCOUNT_NOT_READ_ONLY": "The analytical account is not read-only",
    "READER_ACCOUNT_OVERPRIVILEGED": "The analytical account can access unapproved objects",
    "DATA_SCHEMA_MISMATCH": "The business database schema does not match the contract",
    "RESULT_PERSIST_FAILED": "The query result could not be persisted",
    "ARTIFACT_STALE": "The requested artifact is expired or no longer authorized",
    "CHECKPOINT_CONFLICT": "The conversation state changed; please retry from the latest state",
    "INTERRUPT_INVALID": "The interrupt cannot be resumed in its current state",
    "WRITE_FORBIDDEN": "This write operation is not allowed",
    "MUTATION_STALE": "The approved mutation is no longer valid",
    "MUTATION_EXECUTION_FAILED": "The database could not execute the write",
    "WRITER_ACCOUNT_INVALID": "The configured writer account is invalid",
    "WRITER_ACCOUNT_OVERPRIVILEGED": "The writer account can access unapproved objects",
    "INVALID_TIMEZONE": "The requested IANA timezone is not recognized",
    "CHECKPOINT_VERSION_REQUIRED": "An expected_state_version must be supplied to resume an existing thread",
    "MEMORY_CONFIRMATION_REQUIRED": "This memory update requires explicit user confirmation",
    "ACCOUNT_TAKEN": "The requested account is already registered",
    "BUDGET_EXCEEDED": "The runtime exceeded its configured budget",
    "THREAD_DELETE_FAILED": "The conversation could not be deleted",
    "MEMORY_WRITE_FAILED": "The confirmed preference could not be saved",
}
