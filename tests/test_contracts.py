from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.models import PermissionContext, TaskFrame, TimeRange


def test_core_contracts_reject_unknown_fields_and_invalid_scope():
    with pytest.raises(ValidationError):
        TaskFrame(task_id="t", user_id="u", question="q", intent="DATA_QUERY", unknown=True)
    with pytest.raises(ValidationError):
        PermissionContext(user_id="u", scope_mode="ALLOWLIST", policy_version="p")
    frame = TaskFrame(task_id="t", user_id="u", question="q", intent="DATA_QUERY",
        time_range=TimeRange(start=datetime.now(timezone.utc),
                             end=datetime.now(timezone.utc) + timedelta(minutes=1)))
    assert frame.schema_version == "task_frame_v1"

