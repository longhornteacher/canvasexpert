"""Focused rendering contracts for the CanvasAgent root health console."""

from pathlib import Path

from fastapi.testclient import TestClient

from api.webui import server
from api.webui.routes import connections as connection_routes, pages


def _ready_context():
    return {
        "app_root": "local-app-folder",
        "generic_stdio_config": {"mcpServers": {"canvas-expert": {"command": "python", "args": ["server.py"]}}},
        "health": {
            "python": {"available": True},
            "mcp": {"importable": True, "entrypoint_present": True},
            "workspace": {"configured": True, "writable": True},
            "pseudonym_registry": {"configured": True, "low_runway": False},
        },
        "readiness": {
            "ok": True, "status": "ready", "checked_at": "2026-09-10T12:00:00+00:00",
            "components": {"canvas": {"status": "ready"}, "privacy": {"status": "ready"}},
        },
        "clients": {
            "claude": {"client": "claude", "detected": True, "connected": True, "current": True},
            "chatgpt": {"client": "chatgpt", "detected": False, "connected": False, "current": False},
        },
    }


def _configure(monkeypatch, *, token=True):
    monkeypatch.setattr(server.config, "token_is_set", lambda: token)
    monkeypatch.setattr(server.config, "get_canvas_base", lambda: "https://canvas.example.test" if token else "")
    monkeypatch.setattr(connection_routes.connections, "connection_context", _ready_context)
    monkeypatch.setattr(connection_routes.mirror_service, "status", lambda: {
        "ok": True,
        "enabled": True,
        "workspace_configured": True,
        "serve_max_age_hours": 6,
        "courses": [{
            "course_id": "course-1",
            "course_name": "Fictional Course",
            "passes": {"full": {"last_success_at": "2026-09-10T11:00:00+00:00"}, "delta": {"last_success_at": "2026-09-10T11:30:00+00:00"}},
        }],
        "vault_conflict": [],
    })


def test_canvasagent_root_render_has_one_primary_console_and_no_retired_home(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(server.app)
    response = client.get("/")

    assert response.status_code == 200
    assert "CanvasAgent" in response.text
    assert "MCP connections" in response.text
    assert "Canvas account" in response.text
    assert "Canvas data" in response.text
    assert "Local workspace &amp; privacy" in response.text
    assert 'href="/settings#workspace-card"' in response.text
    assert 'href="/settings#mirror-card"' in response.text
    assert 'href="/settings#current-courses-card"' in response.text
    assert "Start</h2>" not in response.text
    assert "Continue</h2>" not in response.text
    assert "Attention</h2>" not in response.text
    assert "Prepared</h2>" not in response.text
    assert "Receipts</h2>" not in response.text
    assert "Sync now" not in response.text
    assert "/api/work" not in response.text
    assert 'href="/connections"' not in response.text
    assert 'href="/"' in response.text and ">CanvasAgent</a>" in response.text


def test_canvasagent_root_is_available_before_canvas_setup_and_connections_is_retired(monkeypatch):
    _configure(monkeypatch, token=False)
    client = TestClient(server.app)

    assert client.get("/").status_code == 200
    assert client.get("/connections").status_code == 404


def test_health_mapping_covers_client_mirror_privacy_and_overall_failure_paths():
    script = (Path(__file__).resolve().parents[2] / "api/webui/static/canvasagent.js").read_text(encoding="utf-8")
    for contract in (
        "status.connected && status.current",
        "health.python.available",
        "health.mcp.importable",
        "data.enabled",
        "data.workspace_configured",
        "data.serve_max_age_hours",
        "mirror.vault_conflict.length",
        "workspace.writable",
        "registry.low_runway",
        'states.indexOf("unavailable")',
        'states.indexOf("attention")',
    ):
        assert contract in script


def test_student_reports_compatibility_routes_are_retired(monkeypatch):
    monkeypatch.setattr(pages.config, "token_is_set", lambda: True)
    monkeypatch.setattr(pages.config, "get_canvas_base", lambda: "https://canvas.example.test")

    client = TestClient(server.app)
    create = client.get("/course-expert?tab=students", follow_redirects=False)
    old_page = client.get("/students/reports", follow_redirects=False)

    assert create.status_code == 200
    assert "Location" not in create.headers
    assert old_page.status_code == 404
