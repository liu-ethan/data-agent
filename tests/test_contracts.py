from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.models import PermissionContext, ScopeMode, TaskFrame, TimeRange


def test_core_contracts_reject_unknown_fields_and_invalid_scope():
    with pytest.raises(ValidationError):
        TaskFrame(task_id="t", user_id="u", question="q", intent="DATA_QUERY", unknown=True)
    # Spec 00 §4.1: an empty ALLOWLIST must be coerced to NONE, not rejected.
    coerced = PermissionContext(user_id="u", scope_mode="ALLOWLIST", policy_version="p")
    assert coerced.scope_mode == ScopeMode.NONE
    assert coerced.allowed_shop_ids == []
    frame = TaskFrame(task_id="t", user_id="u", question="q", intent="DATA_QUERY",
        time_range=TimeRange(start=datetime.now(timezone.utc),
                             end=datetime.now(timezone.utc) + timedelta(minutes=1)))
    assert frame.schema_version == "task_frame_v1"

