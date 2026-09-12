"""Contract tests for process-local readiness probes and redacted results."""

import json

from api.webui import readiness
from api.webui.routes.readiness import get_readiness, post_readiness_probe


def test_unknown_snapshot_is_redacted_and_process_local(monkeypatch):
    readiness._reset_for_tests()
    first = get_readiness()
    assert first["status"] == "unknown"
    assert first["components"]["canvas"]["status"] == "unknown"
    assert "path" not in json.dumps(first).lower()
    assert "token" not in json.dumps(first).lower()


def test_probe_runs_once_until_forced(monkeypatch):
    readiness._reset_for_tests()
    calls = {"canvas": 0, "privacy": 0}

    def component(name):
        def run():
            calls[name] += 1
            return {"status": "ready"}
        return run

    monkeypatch.setattr(readiness, "_probe_canvas", component("canvas"))
    monkeypatch.setattr(readiness, "_probe_privacy", component("privacy"))

    first = post_readiness_probe()
    second = post_readiness_probe()
    forced = post_readiness_probe(force=True)
    assert first["status"] == second["status"] == forced["status"] == "ready"
    assert calls == {"canvas": 2, "privacy": 2}


def test_component_mapping_never_returns_provider_body():
    assert readiness._code("HTTP 401: secret provider body") == "unauthorized"
    assert readiness._code("Read timed out") == "timeout"
    assert readiness._code("Connection refused") == "network"


def test_privacy_probe_removes_temporary_file(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setattr(readiness.workspace, "workspace_root", lambda: str(root))
    result = readiness._probe_privacy()
    assert result == {"status": "ready"}
    system = root / "_System"
    assert list(system.glob(".readiness-*.probe")) == []
