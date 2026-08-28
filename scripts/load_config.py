"""Load config.yaml. Never print secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict[str, Any]:
    path = ROOT / "config.yaml"
    if not path.exists():
        raise SystemExit(f"FAIL missing {path} (copy config.example.yaml)")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("FAIL config.yaml is not a mapping")
    return data


def join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _socks_to_http(url: str) -> str:
    lower = url.lower()
    for prefix in ("socks5h://", "socks5://", "socks://"):
        if lower.startswith(prefix):
            return "http://" + url[len(prefix) :]
    return url


def httpx_post(url: str, **kwargs: Any) -> httpx.Response:
    """httpx 不认 socks://。Clash 7890 一般是 mixed，改走同端口 HTTP；再不行直连。"""
    proxy_keys = (
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    )
    socks = next(
        (os.environ[k] for k in proxy_keys if os.environ.get(k, "").lower().startswith("socks")),
        None,
    )
    attempts: list[dict[str, Any]] = []
    if socks:
        attempts.append({"proxy": _socks_to_http(socks), "trust_env": False})
    attempts.append({"trust_env": False})
    attempts.append({})

    last: Exception | None = None
    for extra in attempts:
        try:
            return httpx.post(url, **extra, **kwargs)
        except (ValueError, httpx.HTTPError) as exc:
            last = exc
            continue
    raise last or RuntimeError("httpx_post failed")
