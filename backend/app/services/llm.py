"""Provider-neutral, schema-validated LLM boundary."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from ..errors import RuntimeAgentError
from .trace import record

T = TypeVar("T", bound=BaseModel)
Protocol = Literal["anthropic", "openai"]


def _json_object(content: str) -> dict[str, Any]:
    """Extract one JSON object while keeping schema validation authoritative."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("structured response must be a JSON object")
    return value


@dataclass(frozen=True)
class LLMTrace:
    provider: str
    protocol: Protocol
    model: str
    purpose: str
    prompt_version: str
    schema_name: str
    schema_version: str
    duration_ms: float
    attempt_count: int
    schema_repair_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    total_tokens: int | None = None
    request_id: str | None = None
    provider_response_id: str | None = None
    stop_reason: str | None = None


@dataclass(frozen=True)
class _DecodedResponse:
    content: str
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    total_tokens: int | None
    provider_response_id: str | None
    stop_reason: str | None


def _endpoint(base_url: str, protocol: Protocol) -> str:
    base = base_url.rstrip("/")
    if protocol == "anthropic":
        if base.endswith("/v1/messages"):
            return base
        return base + ("/messages" if base.endswith("/v1") else "/v1/messages")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _anthropic_request(*, model: str, instruction: str, user: str,
                       max_tokens: int, temperature: float) -> dict[str, Any]:
    # MiniMax's Anthropic-compatible endpoint documents (0, 1].
    return {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": max(0.01, min(1.0, temperature)),
        "system": instruction,
        "messages": [{"role": "user", "content": user}],
    }


def _openai_request(*, model: str, instruction: str, user: str,
                    max_tokens: int, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": user},
        ],
    }


def _decode_anthropic(body: dict[str, Any]) -> _DecodedResponse:
    # Never persist or expose provider `thinking` blocks.
    content = "".join(
        str(part.get("text", "")) for part in body.get("content", [])
        if isinstance(part, dict) and part.get("type") == "text"
    )
    usage = body.get("usage") or {}
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    return _DecodedResponse(
        content, input_tokens, output_tokens,
        usage.get("cache_creation_input_tokens"), usage.get("cache_read_input_tokens"),
        input_tokens + output_tokens
        if isinstance(input_tokens, int) and isinstance(output_tokens, int) else None,
        body.get("id"), body.get("stop_reason"),
    )


def _decode_openai(body: dict[str, Any]) -> _DecodedResponse:
    choice = body["choices"][0]
    content = choice["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("provider response content must be text")
    usage = body.get("usage") or {}
    return _DecodedResponse(
        content, usage.get("prompt_tokens"), usage.get("completion_tokens"), None,
        (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        usage.get("total_tokens"), body.get("id"), choice.get("finish_reason"),
    )


def _retry_after(response: httpx.Response, fallback: float, cap: float) -> float:
    value = response.headers.get("retry-after")
    if value:
        try:
            return min(cap, max(0.0, float(value)))
        except ValueError:
            try:
                return min(cap, max(0.0, parsedate_to_datetime(value).timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(cap, fallback)


class StructuredLLM:
    """Pooled multi-protocol client with bounded transient retries."""

    def __init__(self, config: dict[str, Any], *,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.base_url = str(config.get("base_url", "")).rstrip("/")
        self.api_key = config.get("api_key")
        self.model = str(config.get("model") or "")
        inferred = "anthropic" if "/anthropic" in self.base_url else "openai"
        self.protocol: Protocol = config.get("protocol") or inferred
        self.provider = str(config.get("provider") or urlparse(self.base_url).hostname
                            or self.protocol)
        self.timeout = float(config.get("timeout_seconds", 30))
        self.retries = max(0, int(config.get("max_retries", 2)))
        self.schema_repairs = max(0, int(config.get("max_schema_repairs", 1)))
        self.retry_base = max(0.0, float(config.get("retry_base_seconds", 0.25)))
        self.retry_cap = max(self.retry_base, float(config.get("retry_cap_seconds", 5)))
        if self.protocol not in {"anthropic", "openai"}:
            raise RuntimeAgentError("LLM_NETWORK_CONFIG_ERROR", "unsupported LLM protocol")
        if (not self.base_url or not self.api_key or not self.model
                or str(self.api_key).startswith("CHANGE_ME")):
            raise RuntimeAgentError(
                "LLM_NOT_CONFIGURED", "LLM provider credentials are not configured")
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout),
            "trust_env": bool(config.get("trust_env", False)),
        }
        if config.get("proxy_url"):
            kwargs["proxy"] = config["proxy_url"]
        if transport:
            kwargs["transport"] = transport
        try:
            self._client = httpx.AsyncClient(**kwargs)
        except (ImportError, ValueError) as exc:
            raise RuntimeAgentError(
                "LLM_NETWORK_CONFIG_ERROR",
                "LLM client network/proxy configuration is invalid",
                details={"error_type": type(exc).__name__},
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> StructuredLLM:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def structured(
        self, *, system: str, user: str, schema: type[T], purpose: str,
        temperature: float = 0.0, prompt_version: str = "unversioned",
    ) -> tuple[T, LLMTrace]:
        instruction = (
            system + "\n\nReturn only a JSON object conforming exactly to this JSON Schema:\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        )
        max_tokens = int((self.config.get("max_output_tokens") or {}).get(purpose, 1600))
        if self.protocol == "anthropic":
            headers = {"x-api-key": str(self.api_key), "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
            payload = _anthropic_request(model=self.model, instruction=instruction, user=user,
                                         max_tokens=max_tokens, temperature=temperature)
        else:
            headers = {"authorization": f"Bearer {self.api_key}",
                       "content-type": "application/json"}
            payload = _openai_request(model=self.model, instruction=instruction, user=user,
                                      max_tokens=max_tokens, temperature=temperature)

        started = time.perf_counter()
        transport_retries = 0
        schema_repairs = 0
        input_tokens = output_tokens = cache_creation_tokens = cache_read_tokens = 0
        usage_observed = False
        for attempt in range(1, self.retries + self.schema_repairs + 2):
            try:
                response = await self._client.post(
                    _endpoint(self.base_url, self.protocol), headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                if transport_retries < self.retries:
                    transport_retries += 1
                    await self._wait_retry(purpose, attempt, "timeout")
                    continue
                raise self._error("LLM_TIMEOUT", "LLM request exceeded the configured timeout",
                                  purpose, attempt, exc, retryable=True) from exc
            except httpx.RequestError as exc:
                if transport_retries < self.retries:
                    transport_retries += 1
                    await self._wait_retry(purpose, attempt, type(exc).__name__)
                    continue
                raise self._error("LLM_PROVIDER_ERROR", "LLM provider request failed",
                                  purpose, attempt, exc, retryable=True) from exc

            if response.status_code >= 400:
                transient = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
                if transient and transport_retries < self.retries:
                    transport_retries += 1
                    record("llm.retry", purpose=purpose, model=self.model,
                           attempt=attempt, status_code=response.status_code)
                    await asyncio.sleep(_retry_after(
                        response, self.retry_base * 2 ** (attempt - 1), self.retry_cap))
                    continue
                code = ("LLM_AUTH_FAILED" if response.status_code in {401, 403}
                        else "LLM_RATE_LIMITED" if response.status_code == 429
                        else "LLM_PROVIDER_ERROR")
                exc = httpx.HTTPStatusError(
                    "provider error", request=response.request, response=response)
                raise self._error(code, "LLM provider rejected the request", purpose,
                                  attempt, exc, retryable=transient,
                                  status_code=response.status_code)
            try:
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("provider response must be an object")
                decoded = (_decode_anthropic(body) if self.protocol == "anthropic"
                           else _decode_openai(body))
                usage_observed = usage_observed or any(value is not None for value in (
                    decoded.input_tokens, decoded.output_tokens,
                    decoded.cache_creation_input_tokens, decoded.cache_read_input_tokens))
                input_tokens += int(decoded.input_tokens or 0)
                output_tokens += int(decoded.output_tokens or 0)
                cache_creation_tokens += int(decoded.cache_creation_input_tokens or 0)
                cache_read_tokens += int(decoded.cache_read_input_tokens or 0)
                parsed = schema.model_validate(_json_object(decoded.content))
            except (json.JSONDecodeError, ValueError, KeyError, IndexError,
                    TypeError, ValidationError) as exc:
                schema_errors = ([
                    {"location": ".".join(str(part) for part in item["loc"]),
                     "type": item["type"], "message": item["msg"]}
                    for item in exc.errors(
                        include_input=False, include_context=False)[:10]
                ] if isinstance(exc, ValidationError) else [{
                    "location": "response", "type": type(exc).__name__,
                    "message": "response is not valid schema JSON"}])
                if schema_repairs < self.schema_repairs:
                    schema_repairs += 1
                    repair = ("\n\nYour previous response failed schema validation. "
                              "Correct these errors and return the complete JSON object: "
                              + json.dumps(schema_errors, ensure_ascii=False))
                    if self.protocol == "anthropic":
                        payload["system"] = instruction + repair
                    else:
                        payload["messages"][0]["content"] = instruction + repair
                    record("llm.schema_repair", purpose=purpose, model=self.model,
                           attempt=attempt, repair_count=schema_repairs,
                           error_type=type(exc).__name__)
                    continue
                error = self._error(
                    "LLM_RESPONSE_INVALID", "LLM failed to return a valid structured response",
                    purpose, attempt, exc)
                error.details["schema_errors"] = schema_errors
                error.details["schema_repair_count"] = schema_repairs
                raise error from exc

            duration = round((time.perf_counter() - started) * 1000, 2)
            schema_default = schema.model_fields.get("schema_version")
            trace = LLMTrace(
                provider=self.provider, protocol=self.protocol, model=self.model,
                purpose=purpose, prompt_version=prompt_version, schema_name=schema.__name__,
                schema_version=str(schema_default.default if schema_default else "v1"),
                duration_ms=duration, attempt_count=attempt,
                schema_repair_count=schema_repairs,
                input_tokens=input_tokens if usage_observed else None,
                output_tokens=output_tokens if usage_observed else None,
                cache_creation_input_tokens=(cache_creation_tokens
                                             if usage_observed else None),
                cache_read_input_tokens=cache_read_tokens if usage_observed else None,
                total_tokens=(input_tokens + output_tokens if usage_observed else None),
                request_id=response.headers.get("x-request-id") or response.headers.get("request-id"),
                provider_response_id=decoded.provider_response_id,
                stop_reason=decoded.stop_reason,
            )
            record("llm.completed", purpose=purpose, provider=self.provider,
                   protocol=self.protocol, model=self.model, duration_ms=duration,
                   attempt_count=attempt, schema_repair_count=schema_repairs,
                   input_tokens=trace.input_tokens,
                   output_tokens=trace.output_tokens, total_tokens=trace.total_tokens,
                   request_id=trace.request_id)
            return parsed, trace
        raise AssertionError("retry loop exited unexpectedly")

    async def _wait_retry(self, purpose: str, attempt: int, error_type: str) -> None:
        record("llm.retry", purpose=purpose, model=self.model,
               attempt=attempt, error_type=error_type)
        await asyncio.sleep(min(self.retry_cap, self.retry_base * 2 ** (attempt - 1)))

    @staticmethod
    def _error(code: str, message: str, purpose: str, attempt: int,
               exc: Exception, *, retryable: bool = False,
               status_code: int | None = None) -> RuntimeAgentError:
        record("llm.failed", purpose=purpose, attempt_count=attempt,
               error_code=code, error_type=type(exc).__name__, status_code=status_code)
        details: dict[str, Any] = {"purpose": purpose, "attempt_count": attempt,
                                   "error_type": type(exc).__name__}
        if status_code is not None:
            details["status_code"] = status_code
        return RuntimeAgentError(code, message, retryable=retryable, details=details)
