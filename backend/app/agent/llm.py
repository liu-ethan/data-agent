from __future__ import annotations

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
