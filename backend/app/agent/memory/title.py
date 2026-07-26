from __future__ import annotations

from app.agent.llm import chat_completion
from app.agent.memory.summarize import strip_sensitive

_MAX = 10


def generate_session_title(question: str, result_summary: str | None) -> str:
    q = strip_sensitive(question or "").strip()
    summary = strip_sensitive(result_summary or "").strip()
    fallback = (q[:_MAX] if q else "新会话")
    messages = [
        {
            "role": "system",
            "content": (
                "你是会话标题助手。根据用户问题与本轮结果摘要，"
                "生成不超过10个字符的中文标题。只输出标题本身，不要引号或解释。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{q}\n摘要：{summary or '（无）'}",
        },
    ]
    try:
        raw = chat_completion(messages, temperature=0)
    except Exception:
        return fallback
    title = strip_sensitive(raw or "").strip().strip("\"'「」").replace("\n", "")
    title = title[:_MAX]
    return title or fallback
