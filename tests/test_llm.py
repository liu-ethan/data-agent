import asyncio
from functools import wraps

import httpx
import pytest
from pydantic import BaseModel

from backend.app.errors import RuntimeAgentError
from backend.app.services.llm import StructuredLLM, _json_object
from backend.app.services.trace import trace_records


class Reply(BaseModel):
    value: str
    schema_version: str = "reply_v1"


def async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def config(protocol="anthropic", **overrides):
    return {
        "base_url": ("https://api.minimax.io/anthropic" if protocol == "anthropic"
                     else "https://example.test/v1"),
        "api_key": "test-key",
        "model": "test-model",
        "protocol": protocol,
        "max_retries": 2,
        "retry_base_seconds": 0,
        **overrides,
    }


@pytest.mark.parametrize("content", [
    '{"status":"QUERY_PLAN"}',
    '```json\n{"status":"QUERY_PLAN"}\n```',
    'Result follows:\n{"status":"QUERY_PLAN"}\nEnd.',
])
def test_structured_json_accepts_common_provider_wrappers(content):
    assert _json_object(content) == {"status": "QUERY_PLAN"}


@pytest.mark.parametrize("content", ["[]", "no object", "```json\n[]\n```"])
def test_structured_json_still_rejects_non_objects(content):
    with pytest.raises((ValueError, TypeError)):
        _json_object(content)


@async_test
async def test_anthropic_protocol_ignores_thinking_and_records_full_usage():
    captured = {}

    def handler(request: httpx.Request):
        captured["request"] = request
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, headers={"x-request-id": "req-provider"}, json={
            "id": "msg_1",
            "content": [
                {"type": "thinking", "thinking": "must never be parsed"},
                {"type": "text", "text": '{"value":"ok"}'},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 4,
                      "cache_creation_input_tokens": 3,
                      "cache_read_input_tokens": 7},
            "stop_reason": "end_turn",
        })

    llm = StructuredLLM(config(), transport=httpx.MockTransport(handler))
    try:
        reply, trace = await llm.structured(
            system="safe", user="question", schema=Reply, purpose="agent",
            temperature=0, prompt_version="agent_v2")
    finally:
        await llm.aclose()

    assert reply.value == "ok"
    assert captured["request"].url == "https://api.minimax.io/anthropic/v1/messages"
    assert captured["request"].headers["x-api-key"] == "test-key"
    assert captured["body"]["temperature"] == 0.01
    assert captured["body"]["max_tokens"] == 1600
    assert trace.input_tokens == 12
    assert trace.output_tokens == 4
    assert trace.total_tokens == 16
    assert trace.cache_creation_input_tokens == 3
    assert trace.cache_read_input_tokens == 7
    assert trace.provider_response_id == "msg_1"
    assert trace.request_id == "req-provider"
    assert trace.stop_reason == "end_turn"
    assert trace.prompt_version == "agent_v2"
    assert trace.schema_version == "reply_v1"


@async_test
async def test_openai_protocol_builds_chat_request_and_decodes_usage():
    captured = {}

    def handler(request: httpx.Request):
        captured["request"] = request
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={
            "id": "chat_1",
            "choices": [{"message": {"content": '{"value":"openai"}'},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 2,
                      "total_tokens": 11,
                      "prompt_tokens_details": {"cached_tokens": 5}},
        })

    llm = StructuredLLM(config("openai"), transport=httpx.MockTransport(handler))
    try:
        reply, trace = await llm.structured(
            system="safe", user="question", schema=Reply, purpose="response")
    finally:
        await llm.aclose()

    assert reply.value == "openai"
    assert captured["request"].url == "https://example.test/v1/chat/completions"
    assert captured["request"].headers["authorization"] == "Bearer test-key"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert trace.total_tokens == 11
    assert trace.cache_read_input_tokens == 5
    assert trace.stop_reason == "stop"


@async_test
async def test_transient_429_is_retried_and_attempt_count_is_traced():
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": '{"value":"ok"}'}],
            "usage": {},
        })

    llm = StructuredLLM(config(), transport=httpx.MockTransport(handler))
    try:
        _, trace = await llm.structured(
            system="safe", user="question", schema=Reply, purpose="agent")
    finally:
        await llm.aclose()
    assert attempts == 2
    assert trace.attempt_count == 2


@async_test
async def test_authentication_failure_is_not_retried():
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"error": "bad key"})

    llm = StructuredLLM(config(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeAgentError) as error:
            await llm.structured(
                system="safe", user="question", schema=Reply, purpose="agent")
    finally:
        await llm.aclose()
    assert attempts == 1
    assert error.value.error_code == "LLM_AUTH_FAILED"
    assert error.value.retryable is False


@async_test
async def test_timeout_retries_are_bounded_and_mapped():
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("slow", request=request)

    llm = StructuredLLM(
        config(max_retries=1), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeAgentError) as error:
            await llm.structured(
                system="safe", user="question", schema=Reply, purpose="agent")
    finally:
        await llm.aclose()
    assert attempts == 2
    assert error.value.error_code == "LLM_TIMEOUT"
    assert error.value.retryable is True
    assert error.value.details["attempt_count"] == 2


@async_test
async def test_invalid_structured_response_is_not_retried_or_logged_as_content():
    attempts = 0
    trace_records.clear()

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={
            "content": [{"type": "text",
                         "text": '{"wrong":"provider-secret-thinking"}'}],
            "usage": {},
        })

    llm = StructuredLLM(config(max_schema_repairs=0),
                        transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeAgentError) as error:
            await llm.structured(
                system="safe", user="question", schema=Reply, purpose="agent")
    finally:
        await llm.aclose()
    assert attempts == 1
    assert error.value.error_code == "LLM_RESPONSE_INVALID"
    assert {item["location"] for item in error.value.details["schema_errors"]} == {
        "value"
    }
    assert "provider-secret-thinking" not in repr(trace_records)


@async_test
async def test_invalid_schema_gets_one_bounded_repair_and_aggregates_usage():
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        content = ('{"wrong":"value"}' if attempts == 1
                   else '{"value":"repaired"}')
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": content}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
        })

    llm = StructuredLLM(config(max_retries=0, max_schema_repairs=1),
                        transport=httpx.MockTransport(handler))
    try:
        reply, trace = await llm.structured(
            system="safe", user="question", schema=Reply, purpose="agent")
    finally:
        await llm.aclose()
    assert reply.value == "repaired"
    assert attempts == 2
    assert trace.attempt_count == 2
    assert trace.schema_repair_count == 1
    assert trace.input_tokens == 10
    assert trace.output_tokens == 4
    assert trace.total_tokens == 14


def test_usage_token_counts_are_not_treated_as_credentials_by_trace_redaction():
    from backend.app.services.trace import record

    trace_records.clear()
    record("usage", input_tokens=10, api_key="secret")
    assert trace_records[-1]["input_tokens"] == 10
    assert trace_records[-1]["api_key"] == "<redacted>"


def test_unsupported_protocol_is_rejected_during_composition():
    with pytest.raises(RuntimeAgentError) as error:
        StructuredLLM(config(protocol="custom"))
    assert error.value.error_code == "LLM_NETWORK_CONFIG_ERROR"
