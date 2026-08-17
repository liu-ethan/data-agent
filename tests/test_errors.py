"""Tests for the error registry and RuntimeAgentError construction.

Spec 00 §6: error codes must come from ERROR_MESSAGES so clients can rely
on a stable set of codes.
"""

from __future__ import annotations

import pytest

from backend.app.errors import ERROR_MESSAGES, RuntimeAgentError


def test_every_runtime_error_code_is_registered():
    """Spec-required codes plus the four added during the P0 audit must be in the registry."""
    assert "PERMISSION_DENIED" in ERROR_MESSAGES
    assert "CHECKPOINT_CONFLICT" in ERROR_MESSAGES
    assert "INVALID_TIMEZONE" in ERROR_MESSAGES
    assert "CHECKPOINT_VERSION_REQUIRED" in ERROR_MESSAGES
    assert "MEMORY_CONFIRMATION_REQUIRED" in ERROR_MESSAGES
    assert "ACCOUNT_TAKEN" in ERROR_MESSAGES
    assert "BUDGET_EXCEEDED" in ERROR_MESSAGES


def test_unregistered_error_code_is_rejected_at_construction(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DRA_ALLOW_UNREGISTERED_ERROR_CODES", raising=False)
    with pytest.raises(ValueError, match="not registered"):
        RuntimeAgentError("DEFINITELY_NOT_REAL", "boom")


def test_registered_error_code_is_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DRA_ALLOW_UNREGISTERED_ERROR_CODES", raising=False)
    exc = RuntimeAgentError("PERMISSION_DENIED", "no")
    assert exc.error_code == "PERMISSION_DENIED"
    assert exc.message == "no"
    assert exc.retryable is False
    assert exc.details == {}


def test_registered_error_code_carries_retryable_and_details(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DRA_ALLOW_UNREGISTERED_ERROR_CODES", raising=False)
    exc = RuntimeAgentError(
        "RESULT_PERSIST_FAILED", "could not save", retryable=True,
        details={"hint": "retry once"})
    assert exc.retryable is True
    assert exc.details == {"hint": "retry once"}


def test_as_model_wires_trace_id():
    exc = RuntimeAgentError("PERMISSION_DENIED", "no")
    model = exc.as_model("trace_abc")
    assert model.error_code == "PERMISSION_DENIED"
    assert model.trace_id == "trace_abc"
    assert model.retryable is False
