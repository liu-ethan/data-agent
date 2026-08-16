"""Real dense embedding adapters used by offline indexing and online queries."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..errors import RuntimeAgentError


def _validate_vectors(vectors: object, expected: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected:
        raise RuntimeAgentError("RAG_EMBEDDING_FAILED",
                                "embedding service returned an invalid batch")
    output: list[list[float]] = []
    dimension: int | None = None
    for vector in vectors:
        if (not isinstance(vector, list) or not vector
                or not all(isinstance(item, (float, int)) for item in vector)):
            raise RuntimeAgentError("RAG_EMBEDDING_FAILED",
                                    "embedding service returned an invalid vector")
        values = [float(item) for item in vector]
        dimension = dimension or len(values)
        if len(values) != dimension:
            raise RuntimeAgentError("RAG_EMBEDDING_FAILED",
                                    "embedding dimensions are inconsistent")
        output.append(values)
    return output


class OpenAICompatibleEmbedder:
    provider = "openai_compatible"

    def __init__(self, config: dict[str, Any], *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        embedding = config.get("embedding") or {}
        self.base_url = str(embedding.get("base_url", config.get("base_url", ""))).rstrip("/")
        self.api_key = embedding.get("api_key", config.get("api_key"))
        self.model_name = str(embedding.get("model") or "")
        self.batch_size = max(1, int(embedding.get("batch_size", 64)))
        if not self.base_url or not self.api_key or not self.model_name:
            raise RuntimeAgentError(
                "RAG_NOT_CONFIGURED", "embedding provider credentials are not configured")
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(float(embedding.get("timeout_seconds", 30))),
            "trust_env": bool(embedding.get("trust_env", config.get("trust_env", False))),
        }
        proxy = embedding.get("proxy_url", config.get("proxy_url"))
        if proxy:
            kwargs["proxy"] = proxy
        if transport:
            kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**kwargs)

    async def embed_query(self, value: str) -> list[float]:
        return (await self.embed_documents([value]))[0]

    async def embed_documents(self, values: list[str]) -> list[list[float]]:
        output: list[list[float]] = []
        for offset in range(0, len(values), self.batch_size):
            batch = values[offset:offset + self.batch_size]
            try:
                response = await self._client.post(
                    self.base_url + "/embeddings",
                    headers={"authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model_name, "input": batch},
                )
                response.raise_for_status()
                data = response.json().get("data")
                if not isinstance(data, list):
                    raise ValueError("embedding data must be a list")
                ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
                output.extend(_validate_vectors(
                    [item.get("embedding") for item in ordered], len(batch)))
            except RuntimeAgentError:
                raise
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                raise RuntimeAgentError(
                    "RAG_EMBEDDING_FAILED", "embedding provider request failed",
                    retryable=isinstance(exc, (httpx.TimeoutException, httpx.RequestError)),
                    details={"error_type": type(exc).__name__},
                ) from exc
        return output

    async def aclose(self) -> None:
        await self._client.aclose()


class FastEmbedder:
    provider = "fastembed"

    def __init__(self, config: dict[str, Any]) -> None:
        embedding = config.get("embedding") or {}
        self.model_name = str(embedding.get("model") or "BAAI/bge-small-zh-v1.5")
        self.cache_dir = embedding.get("cache_dir")
        self.batch_size = max(1, int(embedding.get("batch_size", 256)))
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeAgentError("RAG_NOT_CONFIGURED",
                                        "fastembed is unavailable") from exc
            kwargs = {"model_name": self.model_name}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            try:
                self._model = TextEmbedding(**kwargs)
            except Exception as exc:
                raise RuntimeAgentError(
                    "RAG_EMBEDDING_FAILED", "embedding model could not be loaded",
                    details={"model": self.model_name,
                             "error_type": type(exc).__name__}) from exc
        return self._model

    async def embed_query(self, value: str) -> list[float]:
        def encode() -> list[float]:
            vector = next(iter(self._load().query_embed(value)))
            return [float(item) for item in vector]
        return await asyncio.to_thread(encode)

    async def embed_documents(self, values: list[str]) -> list[list[float]]:
        if not values:
            return []

        def encode() -> list[list[float]]:
            return [[float(item) for item in vector]
                    for vector in self._load().passage_embed(
                        values, batch_size=self.batch_size)]
        vectors = await asyncio.to_thread(encode)
        return _validate_vectors(vectors, len(values))

    async def aclose(self) -> None:
        return None


def build_embedder(config: dict[str, Any]) -> OpenAICompatibleEmbedder | FastEmbedder:
    provider = str((config.get("embedding") or {}).get("provider", "fastembed"))
    if provider == "fastembed":
        return FastEmbedder(config)
    if provider in {"openai", "openai_compatible"}:
        return OpenAICompatibleEmbedder(config)
    raise RuntimeAgentError("RAG_NOT_CONFIGURED",
                            f"unsupported embedding provider: {provider}")
