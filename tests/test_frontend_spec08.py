"""Spec 08 CORS, SSE stream contract, and frontend module boundaries."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api import create_app

FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
ALLOWED_ORIGIN = "http://localhost:5173"
DENIED_ORIGIN = "https://not-allowed.example"
PREFLIGHT_HEADERS = "Authorization,Content-Type,X-Request-ID,Last-Event-ID"


def test_required_workbench_components_exist():
    required = {
        "workbench/AppShell.tsx",
        "workbench/ThreadList.tsx",
        "workbench/ChatComposer.tsx",
        "workbench/RunEvidenceRail.tsx",
        "workbench/ResultTable.tsx",
        "workbench/ChartRenderer.tsx",
        "workbench/InterruptPanel.tsx",
        "workbench/TraceDrawer.tsx",
        "client.ts",
        "api/schema.d.ts",
    }
    present = {
        str(path.relative_to(FRONTEND_SRC)) for path in FRONTEND_SRC.rglob("*") if path.is_file()
    }
    assert required.issubset(present)
    assert not (FRONTEND_SRC / "dashboard").exists()


def test_cors_preflight_allows_configured_origin_and_bearer_headers():
    client = TestClient(create_app())
    allowed = client.options(
        "/api/chat/stream",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": PREFLIGHT_HEADERS,
        },
    )
    assert allowed.status_code in {200, 204}
    assert allowed.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    allow_headers = allowed.headers.get("access-control-allow-headers", "").lower()
    for header in ("authorization", "content-type", "x-request-id", "last-event-id"):
        assert header in allow_headers
    credentials = {key.lower(): value for key, value in allowed.headers.items()}.get(
        "access-control-allow-credentials"
    )
    assert credentials != "true"

    denied = client.options(
        "/api/chat/stream",
        headers={
            "Origin": DENIED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": PREFLIGHT_HEADERS,
        },
    )
    assert "access-control-allow-origin" not in {
        key.lower(): value for key, value in denied.headers.items()
    }


def test_cors_get_and_post_echo_allowed_origin_without_credentials():
    client = TestClient(create_app())
    for method, path in (("GET", "/health"), ("POST", "/api/chat")):
        response = client.request(
            method,
            path,
            headers={"Origin": ALLOWED_ORIGIN},
            json={"message": "ping"} if method == "POST" else None,
        )
        assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
        assert response.headers.get("access-control-allow-credentials") != "true"
        blob = " ".join(f"{key}:{value}" for key, value in response.headers.items()).lower()
        assert "password" not in blob
        assert "jwt" not in blob


def test_sse_stream_declares_event_stream_and_requires_auth():
    client = TestClient(create_app())
    posted = client.post(
        "/api/chat/stream",
        json={"message": "昨天销售额是多少？"},
        headers={"Origin": ALLOWED_ORIGIN, "X-Request-ID": "req_cors"},
    )
    assert posted.status_code == 401
    assert posted.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN

    streamed = client.get(
        "/api/chat/stream",
        params={"request_id": "req_cors"},
        headers={"Origin": ALLOWED_ORIGIN, "Last-Event-ID": "0"},
    )
    assert streamed.status_code == 401
    assert streamed.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


def test_openapi_publishes_post_and_get_chat_stream():
    document = create_app().openapi()
    stream = document["paths"]["/api/chat/stream"]
    assert "post" in stream and "get" in stream
    for method in ("post", "get"):
        content = stream[method]["responses"]["200"]["content"]
        assert "text/event-stream" in content
