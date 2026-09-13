"""CanvasAgent drives its account status from the existing fresh-readiness API."""

from pathlib import Path

from fastapi.testclient import TestClient

from api.webui import readiness, server

ROOT = Path(__file__).resolve().parents[2]


def test_canvasagent_requests_a_fresh_readiness_probe_on_open():
    script = (ROOT / "api/webui/static/canvasagent.js").read_text(encoding="utf-8")
    assert 'fetch("/api/readiness/probe?force=true", { method: "POST"' in script
    assert 'fetch("/api/readiness")' not in script


def test_canvas_account_mapping_contract_covers_configuration_auth_network_and_ready():
    script = (ROOT / "api/webui/static/canvasagent.js").read_text(encoding="utf-8")
    for label in (
        'label: "Ready"',
        'label: "Not configured"',
        'label: "Credentials rejected"',
        'label: "Network timeout"',
        'label: "Network unavailable"',
    ):
        assert label in script
    assert 'code === "unauthorized"' in script
    assert 'code === "timeout"' in script


def test_readiness_probe_route_contract_is_preserved(monkeypatch):
    expected = {"ok": True, "status": "ready", "components": {"canvas": {"status": "ready"}}}
    monkeypatch.setattr(readiness, "probe", lambda force=False: expected)
    monkeypatch.setattr(server.config, "token_is_set", lambda: False)
    monkeypatch.setattr(server.config, "get_canvas_base", lambda: "")

    response = TestClient(server.app).post("/api/readiness/probe?force=true")
    assert response.status_code == 200
    assert response.json() == expected
