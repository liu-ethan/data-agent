"""Checkpoint, artifact, reference and long-term memory abstractions.

Durable MySQL persistence lives in ``repositories.runtime.RuntimePersistence``.
This package only holds the deterministic memory services that Graph nodes
call: reference resolution, prompt projection, rolling summaries and
preference overlay.
"""

from .preferences import (
    apply_preferences,
    extract_explicit_conditions,
    is_long_term_preference_request,
)
from .prompt_context import PromptContextBuilder
from .references import ReferenceResolver, ResolvedReference
from .summary import RollingSummaryBuilder

__all__ = [
    "PromptContextBuilder",
    "ReferenceResolver",
    "ResolvedReference",
    "RollingSummaryBuilder",
    "apply_preferences",
    "extract_explicit_conditions",
    "is_long_term_preference_request",
]
