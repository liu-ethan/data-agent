from __future__ import annotations

from app.agent.knowledge.loader import load_metrics


def load_metric_specs() -> dict[str, dict]:
    return {key: dict(value) for key, value in load_metrics().items()}


def get_metric_keys() -> frozenset[str]:
    return frozenset(load_metrics().keys())


def format_metric_vocab_for_prompt() -> str:
    items: list[str] = []
    for key, spec in sorted(load_metrics().items()):
        aliases = [spec["display_name"], *spec.get("aliases", [])]
        compact_aliases: list[str] = []
        for alias in aliases:
            if alias == key or alias in compact_aliases:
                continue
            compact_aliases.append(alias)
        if compact_aliases:
            items.append(f"{key}({ '/'.join(compact_aliases[:4]) })")
        else:
            items.append(key)
    return ", ".join(items)


def get_metric_spec(key: str) -> dict | None:
    spec = load_metrics().get(key)
    if spec is None:
        return None
    return dict(spec)


def is_known_metric(key: str) -> bool:
    return key in load_metrics()


def _norm(text: str) -> str:
    return "".join(text.lower().split())


def query_knowledge(query: str, *, kind: str | None = None) -> dict | None:
    q = _norm(query)
    if not q:
        return None
    if kind not in (None, "", "metric"):
        return None

    metrics = load_metrics()
    for key, spec in metrics.items():
        candidates = [key, spec["display_name"], *spec.get("aliases", [])]
        if any(_norm(item) == q for item in candidates):
            return {"kind": "metric", "metric": dict(spec)}

    fuzzy: list[dict] = []
    for key, spec in metrics.items():
        candidates = [key, spec["display_name"], *spec.get("aliases", [])]
        if any(q in _norm(item) or _norm(item) in q for item in candidates):
            fuzzy.append(dict(spec))
    if not fuzzy:
        return None
    if len(fuzzy) == 1:
        return {"kind": "metric", "metric": fuzzy[0]}
    return {"kind": "metric", "metrics": fuzzy}
