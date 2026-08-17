"""Durable memory behaviour is covered by spec 05 and persistence tests.

SQLite ``CheckpointStore`` / in-memory ``ArtifactStore`` adapters were
removed so Graph and API share ``RuntimePersistence``.
"""

from backend.app.memory import (
    PromptContextBuilder,
    ReferenceResolver,
    RollingSummaryBuilder,
    apply_preferences,
    is_long_term_preference_request,
)
from backend.app.repositories.runtime import RuntimePersistence


def test_memory_package_exports_spec05_services():
    assert PromptContextBuilder is not None
    assert ReferenceResolver is not None
    assert RollingSummaryBuilder is not None
    assert callable(apply_preferences)
    assert callable(is_long_term_preference_request)
    assert RuntimePersistence is not None
