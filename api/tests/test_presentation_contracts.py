"""Architecture and rendered contracts for the retained control-console UI.

These assertions protect the browser surfaces that still belong to the local
control console. They do not define the product center or require browser parity
for the agent-facing runtime.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from api.webui.server import app
from api.webui.routes import connections as connection_routes, pages


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "api" / "webui" / "templates"

# Retained console route -> (page template, layout, variant, real rail count).
#
# There used to be a fifth "migrated" flag here, plus a MIGRATED_ROUTES subset
# derived from it. Every route carried True once the template-family rollout
# finished, which made the subset identical to this dict and the assertion that
# compared them unfailable. The rollout is done, so the column is gone.
EXPECTED_PRESENTATION = {
    "/": ("canvasagent.html", "workspace", "full", 0),
    "/course-expert": ("course_expert.html", "workspace", "three", 2),
    "/roster": ("roster.html", "workspace", "left-main", 1),
    "/settings": ("settings.html", "workspace", "left-main", 1),
    # Routines sits on the workspace layout so its title shares a left edge
    # with the other primary-nav pages instead of jumping inward.
    "/routines": ("routines.html", "workspace", "full", 0),
    "/course": ("course.html", "document", "wide", 0),
    "/ai-expert": ("ai_expert.html", "document", "standard", 0),
    "/receipts/{receipt_id}": ("receipt.html", "document", "standard", 0),
    "/welcome": ("welcome.html", "wizard", "", 0),
}
FEATURE_CSS = (
    "api/webui/static/pages/canvasagent.css",
    "api/webui/static/pages/course_expert.css",
    "api/webui/static/roster_workbench.css",
    "api/webui/static/pages/settings.css",
    "api/webui/static/pages/routines.css",
    "api/webui/static/pages/student_reports.css",
    "api/webui/static/pages/course.css",
    "api/webui/static/pages/ai_expert.css",
    "api/webui/static/pages/welcome.css",
)
VISUAL_LITERAL_RE = re.compile(r"font-family:|#[0-9a-fA-F]{3,8}|rgb\(|hsl\(|border-radius:|box-shadow:")
FORBIDDEN_JS_SELECTORS = (
    ".ce-shell", ".ce-panel", ".ce-rail", ".ce-page-header", ".ce-btn",
    ".ce-field", ".ce-tabs", ".ce-notice", ".ce-actions", ".ce-table",
    ".ce-status-dot", ".ce-empty",
)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _client() -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _configure_fictional(monkeypatch):
    courses = [{"id": "course-1", "name": "Fictional Course", "nickname": "Fictional", "active": True}]
    monkeypatch.setattr(pages.config, "token_is_set", lambda: True)
    monkeypatch.setattr(pages.config, "get_canvas_base", lambda: "https://canvas.example.test")
    monkeypatch.setattr(pages.config, "get_workspace_path", lambda: "")
    monkeypatch.setattr(pages.config, "active_courses", lambda: courses)
    monkeypatch.setattr(pages.config, "saved_courses", lambda: courses)
    monkeypatch.setattr(pages.config, "get_download_root", lambda: "")
    monkeypatch.setattr(pages.config, "get_tier_tags", lambda: {
        "Support": "", "Core": "", "Accelerate": "",
    })
    monkeypatch.setattr(pages.workspace, "workspace_root", lambda: None)
    monkeypatch.setattr(pages.workspace, "onedrive_root", lambda: "Fictional")
    monkeypatch.setattr(pages.workspace, "folder", lambda name: "")
    monkeypatch.setattr(pages.workspace, "library_folder", lambda name: "")
    monkeypatch.setattr(pages.workspace, "to_review_root", lambda: "")
    monkeypatch.setattr(pages.workspace, "printables_root", lambda: "")
    monkeypatch.setattr(pages.workspace, "canvas_uploads_root", lambda: "")
    monkeypatch.setattr(pages.workspace, "student_work_root", lambda: "")
    monkeypatch.setattr(pages.workspace, "for_ai_root", lambda: "")
    monkeypatch.setattr(pages.workspace, "system_root", lambda root=None: "")
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
        "ok": True, "enabled": True, "workspace_configured": True, "serve_max_age_hours": 6,
        "courses": [], "vault_conflict": [],
    })
    monkeypatch.setattr(pages, "list_ai_ta_files", lambda: [])
    monkeypatch.setattr(pages, "list_quiz_files", lambda: [])
    monkeypatch.setattr(pages, "list_assignment_files", lambda: [])
    monkeypatch.setattr(pages, "list_page_files", lambda: [])
    monkeypatch.setattr(pages, "_routines_template_context", lambda: {
        "custom_dir": "Fictional/custom_routines",
        "authoring_path": "Fictional/custom_routines/AUTHORING.md",
        "custom_active": [],
        "custom_templates": [],
        "active_count": 1,
    })


def test_registry_is_the_full_program_route_map():
    assert len(EXPECTED_PRESENTATION) == 9


def test_all_live_templates_use_layouts_and_no_inline_styles():
    for _, (template, layout, _, _) in EXPECTED_PRESENTATION.items():
        text = (TEMPLATES / template).read_text(encoding="utf-8")
        assert f'{{% extends "layouts/{layout}.html" %}}' in text
        assert "stylesheet_bundle" not in text
        assert 'style="' not in text
        if template == "routines.html":
            assert 'style="' not in (TEMPLATES / "_routines_panel.html").read_text(encoding="utf-8")

    for layout in ("workspace.html", "document.html", "wizard.html", "display.html"):
        assert '{% extends "base.html" %}' in (TEMPLATES / "layouts" / layout).read_text(encoding="utf-8")

    for template in TEMPLATES.rglob("*.html"):
        assert 'style="' not in template.read_text(encoding="utf-8"), template.relative_to(ROOT)


def test_migrated_feature_css_consumes_shared_visual_tokens():
    for relative in FEATURE_CSS:
        assert not VISUAL_LITERAL_RE.search(_source(relative)), relative


def test_legacy_presentation_layer_is_gone():
    for relative in (
        "api/webui/static/style.css",
        "api/webui/static/workbench.css",
        "api/webui/templates/workbench_base.html",
        "api/webui/templates/_workbench_header.html",
        "api/webui/templates/name_manager.html",
        "api/webui/templates/_course_picker.html",
    ):
        assert not (ROOT / relative).exists(), relative
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert "/static/style.css" not in text, template.relative_to(ROOT)
        assert "/static/workbench.css" not in text, template.relative_to(ROOT)


def test_shared_component_classes_are_not_javascript_hooks():
    for path in (ROOT / "api" / "webui" / "static").rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for selector in FORBIDDEN_JS_SELECTORS:
            assert not re.search(
                rf"(?:querySelector|querySelectorAll|getElementsByClassName)\([^\n]*{re.escape(selector)}",
                text,
            ), f"{path.relative_to(ROOT)} uses {selector} as a JavaScript hook"


def test_migrated_routes_render_the_expected_isolated_shell(monkeypatch, tmp_path):
    _configure_fictional(monkeypatch)
    monkeypatch.setattr("api.webui.routes.receipts.receipts.list_receipts", lambda: [{
        "receipt_id": "presentation-receipt", "subject_type": "routine",
        "subject_id": "routine-id", "kind": "routine.run", "status": "applied",
        "attempted_at": "2026-09-20T12:00:00Z", "completed_at": "2026-09-20T12:00:00Z",
        "target_count": 0,
    }])
    root = tmp_path / "CanvasExpert"
    routes = {
        "/": "/",
        "/course-expert": "/course-expert",
        "/roster": "/roster",
        "/settings": "/settings",
        "/routines": "/routines",
        "/course": "/course",
        "/ai-expert": "/ai-expert",
        "/receipts/{receipt_id}": "/receipts/presentation-receipt",
        "/welcome": "/welcome",
    }
    client = _client()
    bundle = (
        "/static/ui/tokens.css", "/static/ui/foundation.css",
        "/static/ui/components.css", "/static/ui/layouts.css",
    )
    for key, url in routes.items():
        _, layout, variant, rails = EXPECTED_PRESENTATION[key]
        response = client.get(url)
        assert response.status_code == 200, url
        text = response.text
        if key == "/roster":
            for removed in (
                "Needs placement", "group-set-select", "group-label",
                "canvas_group", "canvas_groups", "group_state.js", "groups.js",
            ):
                assert removed not in text
        if key == "/settings":
            assert "the three tiers" in text
            assert 'data-tier="Support"' in text
            assert 'data-tier="Core"' in text
            assert 'data-tier="Accelerate"' in text
            assert 'placeholder="e.g. Silver"' in text
            assert 'placeholder="e.g. Red"' in text
            assert 'placeholder="e.g. Blue"' in text
            assert text.count('class="tier-tag-input"') == 3
        assert f'data-ce-layout="{layout}"' in text
        if variant:
            assert f'ce-{layout}--{variant}' in text
        else:
            assert f'ce-{layout}' in text
        # The wizard and display families are both header-less on purpose: first-run
        # setup stays focused, and a projected screen has no chrome to navigate.
        assert text.count("<header") == (0 if layout in ("wizard", "display") else 1)
        assert text.count("<main") == 1
        assert len(re.findall(r'class="[^"]*\bce-rail(?=\s|")', text)) == rails
        assert "/static/style.css" not in text
        assert "/static/workbench.css" not in text
        for href in bundle:
            assert href in text
        ids = re.findall(r'\bid="([^"]+)"', text)
        assert len(ids) == len(set(ids)), url

    for url in ("/powergrader", "/feedback-expert", "/api/powergrader/session/synthetic/packet",
                "/about", "/students/reports"):
        assert client.get(url).status_code == 404
    assert client.get("/course-expert?tab=students").status_code == 200
    assert client.get("/connections").status_code == 404


def test_create_first_session_notice_is_present_only_without_current_courses(monkeypatch):
    _configure_fictional(monkeypatch)
    monkeypatch.setattr(pages.config, "active_courses", lambda: [])
    empty_render = _client().get("/course-expert").text
    assert empty_render.count('data-ce-hook="first-session-course-notice"') == 1
    assert '/settings#add-courses-card' in empty_render
    assert empty_render.index('data-ce-hook="first-session-course-notice"') < empty_render.index('class="ce-panel ce-forge-start"')
    assert "Start in your assistant" in empty_render
    assert 'data-ce-hook="course-tab"' in empty_render

    _configure_fictional(monkeypatch)
    configured_render = _client().get("/course-expert").text
    assert 'data-ce-hook="first-session-course-notice"' not in configured_render


def test_create_title_matches_its_navigation_and_page_title(monkeypatch):
    _configure_fictional(monkeypatch)
    text = _client().get("/course-expert").text
    assert "<title>Create — Canvas Expert</title>" in text
    assert ">Create<" in text


def test_settings_and_welcome_have_no_retired_calendar_links(monkeypatch):
    _configure_fictional(monkeypatch)
    client = _client()
    settings = client.get("/settings").text
    welcome = client.get("/welcome").text
    assert "/calendar" not in settings + welcome
    assert "bell schedule" not in welcome.lower()
    assert 'href="/"' in welcome


def test_settings_and_welcome_internal_links_resolve(monkeypatch):
    _configure_fictional(monkeypatch)
    client = _client()
    for route in ("/settings", "/welcome"):
        text = client.get(route).text
        ids = set(re.findall(r'\bid="([^"]+)"', text))
        for fragment in re.findall(r'href="#([^"]+)"', text):
            assert fragment in ids, f"{route} links to missing #{fragment}"
        for target in re.findall(r'href="(/[^\"]*)"', text):
            path = target.split("#", 1)[0].split("?", 1)[0]
            if path.startswith(("/api/", "/static/")):
                continue
            assert client.get(path).status_code == 200, f"{route} links to missing {target}"
