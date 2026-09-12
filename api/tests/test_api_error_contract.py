"""API error contract: unhandled exceptions on /api/ routes stay JSON."""
import asyncio

from starlette.requests import Request
from fastapi.testclient import TestClient

from api.webui import server
from api.webui.server import app


def _fake_request(path: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "server": ("127.0.0.1", 8765),
        "path": path,
        "query_string": b"",
        "headers": [],
    })


def test_api_path_unhandled_exception_returns_json():
    resp = asyncio.run(
        server._api_errors_return_json(_fake_request("/api/work"), RuntimeError("boom"))
    )
    assert resp.status_code == 500
    assert resp.media_type == "application/json"


def test_non_api_path_keeps_plain_text():
    resp = asyncio.run(
        server._api_errors_return_json(_fake_request("/dashboard"), RuntimeError("boom"))
    )
    assert resp.status_code == 500
    assert resp.media_type == "text/plain"


def test_api_route_that_raises_is_readable_json(monkeypatch):
    """End-to-end: a route that raises comes back as parseable JSON, so the
    frontend's response.json() succeeds and the generic error message is shown."""
    from api.webui.routes import work as work_routes

    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(work_routes, "_all_jobs", _boom)
    client = TestClient(app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
    resp = client.get("/api/work?section=all")
    assert resp.status_code == 500
    body = resp.json()  # would raise if the body were plain text — the bug
    assert body["ok"] is False
    # The error message is now a fixed generic message, not the raw exception
    assert "kaboom" not in body["error"]
    assert "unexpected server error" in body["error"].lower()
