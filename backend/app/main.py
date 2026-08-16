"""Compatibility entrypoint; the HTTP implementation lives in :mod:`backend.app.api`."""

from .api import app, create_app

__all__ = ["app", "create_app"]
