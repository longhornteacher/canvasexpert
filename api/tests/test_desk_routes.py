"""Focused rendering contracts for the CanvasAgent root health console."""

from pathlib import Path
import builtins

from fastapi.testclient import TestClient
import pytest

from api.webui import server
from api.webui.routes import connections as connection_routes, pages
from api.platform_services import workspace
from api.platform_services import config
from api.shared_storage import LegacyStorageReappearedError
from api.platform_services.config import courses


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
    monkeypatch.setattr(config, "token_is_set", lambda: token)
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.example.test" if token else "")
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


def test_retired_settings_file_returns_safe_status_without_opening_it(tmp_path, monkeypatch):
    retired = tmp_path / "settings.json"
    retired.write_text("synthetic retired sentinel", encoding="utf-8")
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))

    real_builtin_open = builtins.open
    real_path_open = Path.open
    opened = []

    def _is_retired(path):
        try:
            return Path(path).resolve() == retired.resolve()
        except TypeError:
            return False

    def guarded_builtin_open(path, *args, **kwargs):
        if _is_retired(path):
            opened.append(str(path))
            raise AssertionError("retired settings file was opened")
        return real_builtin_open(path, *args, **kwargs)

    def guarded_path_open(path, *args, **kwargs):
        if path.resolve() == retired.resolve():
            opened.append(str(path))
            raise AssertionError("retired settings file was opened")
        return real_path_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    try:
        client = TestClient(server.app)
        page = client.get("/")
        health = client.get("/api/connections/health")
        mirror = client.get("/api/mirror/status")
        privacy = client.get("/api/names/vault-conflict")

        assert page.status_code == 200
        assert "MCP connections" in page.text
        assert 'id="generic-stdio-config"' in page.text
        for response in (health, mirror):
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is False
            assert body["error"] == "legacy_storage_reappeared"
            assert "never opened" in body["detail"]
        assert mirror.json()["status"] == "unavailable"
        assert privacy.status_code == 200
        assert privacy.json()["safety_blocked"] is True
        with pytest.raises(LegacyStorageReappearedError):
            courses.saved_courses()
        assert opened == []
    finally:
        monkeypatch.setattr(builtins, "open", real_builtin_open)
        monkeypatch.setattr(Path, "open", real_path_open)

    assert retired.read_text(encoding="utf-8") == "synthetic retired sentinel"
    assert not retired.with_name("settings.json.migrated-synthetic").exists()


def test_health_mapping_covers_client_mirror_privacy_and_overall_failure_paths():
    script = (Path(__file__).resolve().parents[2] / "api/webui/static/canvasagent.js").read_text(encoding="utf-8")
    for contract in (
        "status.connected && status.current",
        "health.python.available",
        "health.mcp.importable",
        'health.error === "legacy_storage_reappeared"',
        'label: "Retired storage file found"',
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

    assert create.status_code == 404
    assert "Location" not in create.headers
    assert old_page.status_code == 404
