"""Infrastructure doubles that are forbidden in production composition."""

from .data import ResultRepository, SQLiteDataRepository

__all__ = ["ResultRepository", "SQLiteDataRepository"]
