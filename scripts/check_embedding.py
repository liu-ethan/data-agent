#!/usr/bin/env python3
"""Ping embedding API. Does not write vectors to SQLite (T8 owns that)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_config import httpx_post, join_url, load_config


def main() -> int:
    cfg = load_config()
    emb = cfg["embedding"]
    if not emb.get("available", True):
        print("SKIP embedding.available=false")
        return 0
    key = str(emb.get("api_key") or "")
    model = emb.get("model")
    dim = int(emb.get("dim") or 0)
    if not key or key.startswith("sk-xxx"):
        raise SystemExit("FAIL embedding.api_key missing")
    if not model or dim < 1:
        raise SystemExit("FAIL embedding.model/dim missing")

    url = join_url(emb["base_url"], "embeddings")
    payload = {"model": model, "input": "sku status connectivity check"}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx_post(url, json=payload, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise SystemExit(f"FAIL embedding network: {type(exc).__name__}") from exc

    if resp.status_code >= 400:
        raise SystemExit(f"FAIL embedding HTTP {resp.status_code} body={resp.text[:300]}")

    data = resp.json()
    vec = data.get("data", [{}])[0].get("embedding")
    if not isinstance(vec, list) or not vec:
        raise SystemExit("FAIL embedding empty vector")
    if len(vec) != dim:
        raise SystemExit(f"FAIL embedding dim={len(vec)} expected={dim}")
    print(f"OK  embedding model={model} dim={len(vec)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
