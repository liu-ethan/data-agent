import json

from app.log.logging import get_request_id, log_event, set_request_id


def test_log_event_emits_json(capsys):
    set_request_id("req_test_1")
    log_event("INFO", "schema_served", detail={"tables": 8})
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "schema_served"
    assert payload["request_id"] == "req_test_1"
    assert payload["level"] == "INFO"
    assert "ts" in payload
    assert get_request_id() == "req_test_1"
