from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from backend.app.resources.domain import think_steps

ThinkFn = Callable[[dict[str, str]], None]
_listener: ContextVar[ThinkFn | None] = ContextVar("think_listener", default=None)


def emit_think(node: str) -> None:
    listener = _listener.get()
    if listener is None:
        return
    spec = think_steps().get(node)
    if spec is None:
        return
    listener({"node": node, "label": spec["label"], "text": spec["text"]})


@contextmanager
def think_listener(fn: ThinkFn) -> Iterator[None]:
    token = _listener.set(fn)
    try:
        yield
    finally:
        _listener.reset(token)
