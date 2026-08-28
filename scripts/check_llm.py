#!/usr/bin/env python3
"""Ping LLM chat completions. Does not write to MySQL or SQLite."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_config import httpx_post, join_url, load_config


def main() -> int:
    cfg = load_config()
    llm = cfg["llm"]
    key = str(llm.get("api_key") or "")
    model = llm.get("model")
    if not key or key.startswith("sk-xxx"):
        raise SystemExit("FAIL llm.api_key missing")
    if not model:
        raise SystemExit("FAIL llm.model missing")

    url = join_url(llm["base_url"], "chat/completions")
    timeout = float(llm.get("timeout_seconds") or 60)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word pong."}],
        "max_tokens": 16,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx_post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise SystemExit(f"FAIL llm network: {type(exc).__name__}") from exc

    if resp.status_code >= 400:
        raise SystemExit(f"FAIL llm HTTP {resp.status_code} body={resp.text[:300]}")

    data = resp.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if not str(content).strip():
        raise SystemExit("FAIL llm empty content")
    print(f"OK  llm model={model} reply_len={len(str(content))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
