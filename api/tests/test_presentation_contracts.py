"""Rendered contracts for the console surfaces that remain in the pilot."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from api.webui.server import app
from api.webui.routes import connections as connection_routes, pages


def _configure_fictional(monkeypatch):
    courses = [{"id": "course-1", "name": "Fictional Course", "nickname": "Fictional", "active": True}]
    monkeypatch.setattr(pages.config, "token_is_set", lambda: True)
    monkeypatch.setattr(pages.config, "get_canvas_base", lambda: "https://canvas.example.test")
    monkeypatch.setattr(pages.config, "get_workspace_path", lambda: "")
    monkeypatch.setattr(pages.config, "active_courses", lambda: courses)
    monkeypatch.setattr(pages.config, "saved_courses", lambda: courses)
    monkeypatch.setattr(pages.config, "get_tier_tags", lambda: {
        "Support": "", "Core": "", "Accelerate": "",
    })
    monkeypatch.setattr(pages.workspace, "workspace_root", lambda: None)
    monkeypatch.setattr(pages.workspace, "onedrive_root", lambda: "Fictional")
    for name in (
        "folder", "library_folder", "to_review_root",
        "canvas_uploads_root", "student_work_root", "for_ai_root", "system_root",
    ):
        monkeypatch.setattr(pages.workspace, name, lambda *args, **kwargs: "")
    monkeypatch.setattr(connection_routes.connections, "connection_context", lambda: {
        "generic_stdio_config": {"mcpServers": {}},
        "health": {
            "python": {"available": True},
            "mcp": {"importable": True, "entrypoint_present": True},
            "workspace": {"configured": True, "writable": True},
            "pseudonym_registry": {"configured": True, "low_runway": False},
        },
        "readiness": {"status": "unknown", "components": {"canvas": {"status": "unknown"}, "privacy": {"status": "unknown"}}},
        "clients": {
            "claude": {"client": "claude", "detected": True, "connected": True, "current": True},
            "chatgpt": {"client": "chatgpt", "detected": False, "connected": False, "current": False},
        },
    })
    monkeypatch.setattr(connection_routes.mirror_service, "status", lambda: {
        "ok": True, "enabled": True, "workspace_configured": True,
        "serve_max_age_hours": 6, "courses": [], "vault_conflict": [],
    })


def test_retained_console_routes_render_and_receipts_remain_available(monkeypatch):
    _configure_fictional(monkeypatch)
    monkeypatch.setattr("api.webui.routes.receipts.receipts.list_receipts", lambda: [{
        "receipt_id": "presentation-receipt", "subject_type": "operation",
        "subject_id": "operation-1", "kind": "operation.apply", "status": "applied",
        "attempted_at": "2026-09-20T12:00:00Z", "completed_at": "2026-09-20T12:00:00Z",
        "target_count": 1,
    }])

    # This registry intentionally covers only the setup, private names, settings,
    # home, and durable receipt surfaces retained by the local control console.
    routes = {
        "/": ("canvasagent.html", "workspace", "full", 0),
        "/names": ("names.html", "workspace", "left-main", 1),
        "/settings": ("settings.html", "workspace", "left-main", 1),
        "/welcome": ("welcome.html", "wizard", "", 0),
        "/receipts/{receipt_id}": ("receipt.html", "document", "standard", 0),
    }
    urls = {
        "/": "/",
        "/names": "/names",
        "/settings": "/settings",
        "/welcome": "/welcome",
        "/receipts/{receipt_id}": "/receipts/presentation-receipt",
    }
    bundle = (
        "/static/ui/tokens.css", "/static/ui/foundation.css",
        "/static/ui/components.css", "/static/ui/layouts.css",
    )

    client = TestClient(app)
    for route, (template, layout, variant, rails) in routes.items():
        response = client.get(urls[route])
        assert response.status_code == 200, route
        html = response.text
        template_path = Path(__file__).resolve().parents[1] / "webui" / "templates" / template
        assert f'{{% extends "layouts/{layout}.html" %}}' in template_path.read_text(encoding="utf-8")
        assert f'data-ce-layout="{layout}"' in html
        assert f'ce-{layout}--{variant}' in html if variant else f'ce-{layout}' in html
        assert len(re.findall(r'class="[^"]*\bce-rail(?=\s|")', html)) == rails
        assert html.count("<main") == 1
        assert all(href in html for href in bundle)
        ids = re.findall(r'\bid="([^"]+)"', html)
        assert len(ids) == len(set(ids)), route
        assert template != "receipt.html" or "operation-1" in html
