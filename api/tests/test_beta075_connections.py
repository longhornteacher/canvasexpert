import json
import runpy
import sys
import types
import zipfile
from pathlib import Path


def test_support_bundle_is_minimal_and_identifier_free(tmp_path, monkeypatch):
    from api import __version__, diagnostics, operational_log
    from api.mcp_server.contract import TOOL_SCHEMA_VERSION
    from api.platform_services import workspace

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    workspace_root = tmp_path / "workspace"
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(workspace_root))
    monkeypatch.setattr(diagnostics.config, "get_canvas_base", lambda: "")
    monkeypatch.setattr(diagnostics.config, "token_is_set", lambda: False)

    operational_log.emit("diagnostics.health", "ok", duration_ms=5, count=1)
    operational_log.emit("canvas.request", "failed", status_code=503, error_class=RuntimeError)
    rejected = [
        {"event": "diagnostics.rejected", "outcome": "ok", "path": str(tmp_path)},
        {"event": "diagnostics.rejected", "outcome": "ok", "token": "secret-token-value"},
        {"event": "diagnostics.rejected", "outcome": "ok", "user": "Real User Name"},
        {"event": "diagnostics.rejected", "outcome": "ok", "course_id": "course_id-42"},
        {"event": "diagnostics.rejected", "outcome": "ok", "student_id": "student_id-7"},
        {"event": "diagnostics.rejected", "outcome": "ok", "provider": {"body": "provider-body"}},
        {"event": "diagnostics.rejected", "outcome": "ok", "exception": "RuntimeError: stack details"},
    ]
    log_path = operational_log._log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for record in rejected:
            handle.write(json.dumps(record) + "\n")

    destination = tmp_path / "support-bundle.zip"
    result = diagnostics.build_support_bundle(destination)
    assert result == destination
    with zipfile.ZipFile(result) as archive:
        member_names = archive.namelist()
        # errors.log (B3/B4): present but empty here -- no traceback was written.
        assert member_names == ["errors.log", "health.json", "manifest.json", "operations.jsonl"]
        manifest = json.loads(archive.read("manifest.json"))
        health = json.loads(archive.read("health.json"))
        operations = archive.read("operations.jsonl").decode("utf-8")
        member_contents = {name: archive.read(name).decode("utf-8") for name in member_names}

    assert manifest["schema_version"] == 1
    assert manifest["app_version"] == __version__
    assert manifest["tool_schema_version"] == TOOL_SCHEMA_VERSION
    assert manifest["created_at"].endswith("Z")
    assert health == diagnostics.health_snapshot()
    assert '"event":"diagnostics.health"' in operations
    assert '"event":"canvas.request"' in operations

    forbidden = [
        str(tmp_path), "secret-token-value", "Real User Name", "course_id-42",
        "student_id-7", "provider-body", "RuntimeError: stack details",
    ]
    probe_markers = ("probe", "support-bundle.", ".support-")
    for name in member_names:
        assert not any(marker in name for marker in probe_markers)
        content = member_contents[name]
        assert not any(value in name or value in content for value in forbidden)

    assert not (workspace_root / "_System").exists()
    assert not any(path.name.startswith((".diagnostic-", ".support-", ".tmp"))
                   for path in tmp_path.iterdir())


def test_support_bundle_carries_recent_traceback_text(tmp_path, monkeypatch):
    """B3/B4: errors.log is the one member not built from an allowlisted
    source -- it's raw text, verified separately from the identifier-free
    check above (which only proves the OTHER three members stay clean)."""
    from api import diagnostics, operational_log

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(diagnostics.config, "get_canvas_base", lambda: "")
    monkeypatch.setattr(diagnostics.config, "token_is_set", lambda: False)

    operational_log.write_traceback("Traceback (most recent call last):\nValueError: something broke\n")

    destination = tmp_path / "support-bundle.zip"
    diagnostics.build_support_bundle(destination)
    with zipfile.ZipFile(destination) as archive:
        errors_text = archive.read("errors.log").decode("utf-8")
    assert "ValueError: something broke" in errors_text


def test_connections_page_and_mcpb_use_runtime_paths_without_client_config_writes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api import __version__, connections, diagnostics, runtime_paths
    from api.mcp_server.contract import TOOL_SCHEMA_VERSION
    from api.platform_services import config, workspace
    from api.webui import server

    first_app = tmp_path / "first-app"
    second_app = tmp_path / "second-app"
    first_workspace = tmp_path / "first-workspace"
    second_workspace = tmp_path / "second-workspace"
    for app_root in (first_app, second_app):
        (app_root / "api" / "mcp_server").mkdir(parents=True)
        (app_root / "api" / "mcp_server" / "server.py").write_text("", encoding="utf-8")
        (app_root / "tools").mkdir()
    current = {"app": first_app, "workspace": first_workspace}
    monkeypatch.setattr(runtime_paths, "app_root", lambda: current["app"])
    monkeypatch.setattr(runtime_paths, "python_executable", lambda: Path(sys.executable).resolve())
    monkeypatch.setattr(runtime_paths, "mcp_entrypoint", lambda: current["app"] / "api" / "mcp_server" / "__main__.py")
    monkeypatch.setattr(runtime_paths, "temp_dir", lambda: tmp_path / "server-temp")
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(current["workspace"]))
    monkeypatch.setattr(config, "get_canvas_base", lambda: "")
    monkeypatch.setattr(config, "token_is_set", lambda: False)
    monkeypatch.setenv("PATH", "")

    client_dirs = [tmp_path / name for name in ("claude", "chatgpt", "generic")]
    for path in client_dirs:
        path.mkdir()
    before = {path: sorted(item.name for item in path.iterdir()) for path in client_dirs}

    current["app"] = second_app
    current["workspace"] = second_workspace
    context = connections.connection_context()
    generic = connections.generic_stdio_config()
    package = connections.build_claude_mcpb(tmp_path / "CanvasExpert-test.mcpb")
    assert context["app_root"] == str(second_app)
    assert context["generic_stdio_config"] == generic
    assert generic == {
        "mcpServers": {
            "canvas-expert": {
                "command": str(Path(sys.executable).resolve()),
                "args": [str(second_app / "api" / "mcp_server" / "__main__.py")],
            }
        }
    }

    with zipfile.ZipFile(package) as archive:
        assert archive.namelist() == ["manifest.json", "server/launcher.py"]
        manifest = json.loads(archive.read("manifest.json"))
        launcher = archive.read("server/launcher.py").decode("utf-8")
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
    assert manifest == {
        "manifest_version": "0.3",
        "name": "canvas-expert",
        "display_name": "Canvas Expert",
        "version": __version__,
        "description": "Launches the Canvas Expert MCP server from this computer's unzipped CanvasExpert folder.",
        "author": {"name": "Canvas Expert"},
        "server": {
            "type": "python",
            "entry_point": "server/launcher.py",
            "mcp_config": {
                "command": str(Path(sys.executable).resolve()),
                "args": ["${__dirname}/server/launcher.py"],
                "env": {"CANVAS_EXPERT_ROOT": str(second_app)},
            },
        },
        "compatibility": {"platforms": ["win32"], "runtimes": {"python": ">=3.14,<4.0"}},
    }
    assert "subprocess" not in launcher
    assert str(second_app) not in launcher

    launcher_path = tmp_path / "launcher.py"
    launcher_path.write_text(launcher, encoding="utf-8")
    fake_api = types.ModuleType("api")
    fake_api.__path__ = []
    fake_mcp = types.ModuleType("api.mcp_server")
    fake_mcp.__path__ = []
    fake_server = types.ModuleType("api.mcp_server.server")
    calls = []
    fake_server.run_stdio = lambda: calls.append(True)
    monkeypatch.setitem(sys.modules, "api", fake_api)
    monkeypatch.setitem(sys.modules, "api.mcp_server", fake_mcp)
    monkeypatch.setitem(sys.modules, "api.mcp_server.server", fake_server)
    monkeypatch.setenv("CANVAS_EXPERT_ROOT", str(second_app))
    runpy.run_path(str(launcher_path), run_name="__main__")
    assert calls == [True]

    monkeypatch.setattr(server.config, "token_is_set", lambda: True)
    monkeypatch.setattr(server.config, "get_canvas_base", lambda: "https://canvas.invalid")
    response = TestClient(server.app).get("/")
    assert response.status_code == 200
    assert response.text.count("CanvasAgent") >= 1
    assert "MCP connections" in response.text
    assert 'href="/settings#workspace-card"' in response.text
    assert TestClient(server.app).get("/connections").status_code == 404
    assert TestClient(server.app).get("/api/connections/health").status_code == 200
    assert TestClient(server.app).post("/api/connections/claude-package").status_code == 200
    assert TestClient(server.app).post("/api/support-bundle").status_code == 200

    after = {path: sorted(item.name for item in path.iterdir()) for path in client_dirs}
    assert after == before
    assert "tunnel_client_present" not in diagnostics.health_snapshot()["environment"]


def test_health_snapshot_pseudonym_registry_when_no_workspace_is_configured(monkeypatch):
    """No workspace yet means no vault to read, but the registry's own total
    is still true and non-sensitive, so it is reported anyway."""
    from api import diagnostics, feedback_vault
    from api.platform_services import workspace

    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    registry = diagnostics.health_snapshot()["pseudonym_registry"]
    assert registry == {
        "configured": False,
        "words_total": len(feedback_vault._REGISTRY_WORDS),
        "words_assigned": 0,
        "words_remaining": len(feedback_vault._REGISTRY_WORDS),
        "low_runway": False,
    }


def test_health_snapshot_pseudonym_registry_reflects_this_machines_real_vault(tmp_path, monkeypatch):
    from api import diagnostics, feedback_vault
    from api.platform_services import workspace

    from api.identity_vault_service import open_vault

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    vault = open_vault()
    with vault.transaction():
        vault.get_or_assign("9001", "Student One")
        vault.get_or_assign("9002", "Student Two")

    registry = diagnostics.health_snapshot()["pseudonym_registry"]
    total = len(feedback_vault._REGISTRY_WORDS)
    assert registry == {
        "configured": True,
        "words_total": total,
        "words_assigned": 2,
        "words_remaining": total - 2,
        "low_runway": False,
    }
