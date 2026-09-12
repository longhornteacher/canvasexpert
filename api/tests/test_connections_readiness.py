"""The /connections Canvas health line reads readiness.snapshot(), not the
naive bool(base_url) and token_is_set() check -- a revoked token used to
read as "Configured" forever, because nothing ever probed Canvas.
"""
import re

import pytest
from fastapi.testclient import TestClient

from api.webui import readiness, server

_CANVAS_LINE = re.compile(r"Canvas:\s*<strong>\s*(.*?)\s*</strong>", re.S)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(server.config, "token_is_set", lambda: True)
    monkeypatch.setattr(server.config, "get_canvas_base", lambda: "https://canvas.invalid")


def _canvas_line(text):
    match = _CANVAS_LINE.search(text)
    assert match, "Canvas health line not found in rendered /connections page"
    return match.group(1).strip()


def _render(monkeypatch, components):
    monkeypatch.setattr(readiness, "snapshot", lambda: {
        "ok": True, "status": "degraded", "checked_at": "2026-08-06T00:00:00+00:00",
        "configured_model": "", "components": components,
    })
    return TestClient(server.app).get("/connections").text


@pytest.mark.parametrize(("status", "code", "expected"), [
    ("ready", "", "Connected"),
    ("degraded", "unauthorized", "Token invalid or revoked"),
    ("degraded", "timeout", "Unreachable (timed out)"),
    ("degraded", "network", "Unreachable"),
    ("unconfigured", "unconfigured", "Not configured"),
])
def test_canvas_health_line_reflects_the_real_probe(monkeypatch, status, code, expected):
    components = {"canvas": {"status": status}, "privacy": {"status": "ready"}}
    if code:
        components["canvas"]["code"] = code
    text = _render(monkeypatch, components)
    assert _canvas_line(text) == expected


def test_canvas_health_line_falls_back_before_the_first_probe(monkeypatch):
    """Right after launch, readiness has never run -- fall back to the old
    credential-presence check rather than showing a raw "unknown" state."""
    monkeypatch.setattr(readiness, "snapshot", lambda: {
        "ok": True, "status": "unknown", "checked_at": None, "configured_model": "",
        "components": {name: {"status": "unknown"} for name in ("canvas", "privacy")},
    })
    text = TestClient(server.app).get("/connections").text
    assert _canvas_line(text) == "Configured"
