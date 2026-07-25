from __future__ import annotations

import json

from openai import OpenAI

from app.config import get_settings


def chat_completion(messages: list[dict], *, temperature: float = 0) -> str:
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise ValueError(
            "LLM api_key is not configured; set llm.api_key in config.yaml"
        )
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=temperature,
    )
    content = response.choices[0].message.content
    if content is None:
        return ""
    return content.strip()


def chat_completion_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    temperature: float = 0,
) -> dict:
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise ValueError(
            "LLM api_key is not configured; set llm.api_key in config.yaml"
        )
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
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
    return {"content": message.content, "tool_calls": tool_calls}
