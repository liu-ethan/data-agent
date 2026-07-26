from __future__ import annotations

import json
import time

from openai import OpenAI

from app.config import get_settings
from app.log.logging import log_event


def chat_completion(
    messages: list[dict], *, temperature: float | None = None
) -> str:
    settings = get_settings()
    if temperature is None:
        temperature = settings.llm_temperature
    if not settings.openai_api_key.strip():
        raise ValueError(
            "LLM api_key is not configured; set llm.api_key in config.yaml"
        )
    log_event(
        "INFO",
        "prompt_input",
        mode="chat",
        model=settings.openai_model,
        temperature=temperature,
        detail={"messages": messages},
    )
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=temperature,
    )
    content = response.choices[0].message.content
    if content is None:
        content = ""
    else:
        content = content.strip()
    log_event(
        "INFO",
        "prompt_output",
        mode="chat",
        model=settings.openai_model,
        latency_ms=int((time.perf_counter() - started) * 1000),
        detail={"content": content},
    )
    return content


def chat_completion_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    temperature: float | None = None,
) -> dict:
    settings = get_settings()
    if temperature is None:
        temperature = settings.llm_temperature
    if not settings.openai_api_key.strip():
        raise ValueError(
            "LLM api_key is not configured; set llm.api_key in config.yaml"
        )
    log_event(
        "INFO",
        "prompt_input",
        mode="tools",
        model=settings.openai_model,
        temperature=temperature,
        detail={"messages": messages, "tools": tools},
    )
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        tools=tools,
        temperature=temperature,
    )
    message = response.choices[0].message
    tool_calls = []
    for tool_call in message.tool_calls or []:
        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append(
            {
                "id": tool_call.id,
                "name": tool_call.function.name,
                "arguments": arguments,
            }
        )
    result = {"content": message.content, "tool_calls": tool_calls}
    log_event(
        "INFO",
        "prompt_output",
        mode="tools",
        model=settings.openai_model,
        latency_ms=int((time.perf_counter() - started) * 1000),
        detail={
            "content": message.content,
            "tool_calls": tool_calls,
        },
    )
    if tool_calls:
        log_event(
            "INFO",
            "llm_tool_calls",
            mode="tools",
            model=settings.openai_model,
            detail={"tool_calls": tool_calls},
        )
    return result
