import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_workspace_paths_are_resolved_at_call_time(tmp_path, monkeypatch):
    from api import runtime_paths
    from api.platform_services import config, workspace
    from api import ai_authoring
    from api import staged_content as deps

    roots = {"current": tmp_path / "one"}
    for label in ("one", "two"):
        root = tmp_path / label
        (root / "Library" / "AI Authoring").mkdir(parents=True)
        (root / "Library" / "AI Authoring" / "Author a Quiz (QuizForge).txt").write_text(
            f"{label} AI Authoring marker\n", encoding="utf-8"
        )

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(roots["current"]))
    assert runtime_paths.ai_ta_dir() == tmp_path / "one" / "Library" / "AI Authoring"
    roots["current"] = tmp_path / "two"
    assert runtime_paths.ai_ta_dir() == tmp_path / "two" / "Library" / "AI Authoring"

    built = ai_authoring.build_library(runtime_paths.ai_ta_dir())
    assert all(Path(path).is_relative_to(tmp_path / "two") for path in built)

    assert not hasattr(config, "RUBRIC_" + "FOLDERS")

    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", "")
    repo_root = Path(__file__).resolve().parents[2]
    assert runtime_paths.app_root() == repo_root
    assert runtime_paths.mcp_entrypoint() == repo_root / "api" / "mcp_server" / "__main__.py"
    assert runtime_paths.python_executable() == Path(sys.executable).resolve()


def test_no_workspace_never_seeds_ai_authoring_under_the_repo_root(tmp_path, monkeypatch):
    """With no workspace configured, ai_ta_dir() is None and building the
    library at startup writes nothing under the repo root.

    Regression for the 2026-09-23 incident where an unconfigured workspace
    made ai_ta_dir() fall back to app_root() / "AI Authoring", and
    build_library then copied the canonical api/default_docs/AI Authoring/
    contracts into a second copy in the source tree.
    """
    from api import runtime_paths
    from api.platform_services import workspace
    from api import ai_authoring, runtime
    from api.mirror import service

    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    monkeypatch.setattr(runtime_paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(workspace, "ensure_workspace", lambda: None)
    monkeypatch.setattr("api.platform_services.config.ensure_workspace_pinned", lambda: None)
    monkeypatch.setattr("api.operation_ledger.recovery.recover_pending_operations", lambda: None)
    library_calls = []
    monkeypatch.setattr(ai_authoring, "build_library", library_calls.append)
    monkeypatch.setattr(service, "mirror_heartbeat_worker", lambda stop_event: stop_event.wait())
    monkeypatch.setattr(runtime, "_started", False)
    monkeypatch.setattr(runtime, "_stopped", False)
    monkeypatch.setattr(runtime, "_heartbeat_stop", None)
    monkeypatch.setattr(runtime, "_heartbeat_thread", None)

    assert runtime_paths.ai_ta_dir() is None

    runtime.start()
    runtime.stop()

    assert library_calls == []
    assert not (tmp_path / "AI Authoring").exists()



def test_all_content_pickers_use_only_the_synced_library(tmp_path, monkeypatch):
    from api import runtime_paths
    from api.platform_services import workspace
    from api import staged_content as deps

    repo_root = tmp_path / "repo-api"
    bundled = repo_root / "qf_materials" / "qf quiz examples"
    bundled.mkdir(parents=True)
    (bundled / "bundled-example.txt").write_text("bundled\n", encoding="utf-8")
    monkeypatch.setattr(runtime_paths, "api_root", lambda: repo_root)

    pickers = {
        "quiz": deps.list_quiz_files,
        "assignment": deps.list_assignment_files,
        "page": deps.list_page_files,
    }
    for kind, picker in pickers.items():
        monkeypatch.setattr(workspace, "workspace_root", lambda: None)
        assert picker() == [], kind

        root = tmp_path / kind
        folder = (root / "Assignments" if kind == "assignment"
                  else root / "Library" / runtime_paths._KIND_WORKSPACE_NAMES[kind])
        folder.mkdir(parents=True)
        (folder / f"{kind}-source.txt").write_text("library\n", encoding="utf-8")
        monkeypatch.setattr(workspace, "workspace_root", lambda root=root: str(root))
        files = picker()
        assert [item["label"] for item in files] == [f"{kind}-source.txt"]
        assert all("bundled-example.txt" not in item["path"] for item in files)


def test_txt_file_labels_disambiguate_only_on_a_name_collision(tmp_path):
    """Two files sharing a basename across different folders must each show
    their own folder in the label; a lone file just shows its name."""
    from api import staged_content as deps

    folder_a = tmp_path / "folder-a"
    folder_b = tmp_path / "folder-b"
    folder_a.mkdir()
    folder_b.mkdir()
    (folder_a / "shared.txt").write_text("a\n", encoding="utf-8")
    (folder_b / "shared.txt").write_text("b\n", encoding="utf-8")
    (folder_a / "unique.txt").write_text("u\n", encoding="utf-8")

    files = deps._list_txt_files([folder_a, folder_b])
    labels = {f["label"] for f in files}
    assert labels == {"shared.txt (folder-a)", "shared.txt (folder-b)", "unique.txt"}


def test_quick_fix_contract_and_version(monkeypatch, tmp_path):
    from api import __version__
    from api.platform_services import config, workspace
    from api.webui import server

    from api.mcp_server import tools
    from api.mirror import store as mirror_store

    for module_name in (
        "api.webui.routes." + "feedback_" + "run",
        "api.webui.routes." + "feedback_" + "manual",
        "api.webui.routes." + "feedback_" + "push",
    ):
        assert importlib.util.find_spec(module_name) is None
    assert not any(route.path.startswith("/api/feedback/") for route in server.app.routes)

    # Publish synthetic safe evidence and index it: the MCP reads serve from the
    # pseudonymized evidence index, never live Canvas.
    from api.mirror import service
    from api.mirror.evidence_acquisition import publish_course_receipt
    from api.mirror.evidence_paths import source_key_for_origin
    from api.mirror.evidence_publish import EvidencePublisher
    from api.tests.mirror.acquisition_samples import SyntheticVault, course_receipt_sample

    from contextlib import contextmanager
    from api.mirror import store as mirror_store_module

    root = tmp_path / "workspace"
    source = source_key_for_origin("https://canvas.example.test")
    vault = SyntheticVault()
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    monkeypatch.setattr(workspace.runtime_paths, "local_cache_dir", lambda: tmp_path / "local")

    @contextmanager
    def _transaction(_root):
        yield vault

    monkeypatch.setattr(mirror_store_module, "_vault_transaction", _transaction)
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "1"}])
    monkeypatch.setattr(tools.config, "saved_courses", lambda: [{"id": "1"}])
    monkeypatch.setattr(tools.config, "get_canvas_base",
                        lambda: "https://canvas.example.test")
    monkeypatch.setattr(tools, "_open_vault", lambda: (vault, None))

    receipt = course_receipt_sample("read_path")
    publisher = EvidencePublisher(workspace_root=root, source_key=source,
                                  course_id="1", vault=vault)
    publish_course_receipt(publisher=publisher, receipt=receipt,
                           writer_key="writer-a", run_id="run-a")
    service.run_index_maintenance(root=root, source_key=source)

    result = tools.get_roster("1")
    assert result["ok"] is True

    monkeypatch.setattr(config, "token_is_set", lambda: True)
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    client = TestClient(server.app)
    retired_about = client.get("/about")
    assert retired_about.status_code == 404

    # Consistent scoring feedback: personas are removed entirely. /settings
    # renders the current version with no Personas row, and the retired
    # /api/feedback/* routes are gone (404).
    settings_response = client.get("/settings")
    assert settings_response.status_code == 200
    assert f"You are on {__version__}." in settings_response.text
    assert "Personas" not in settings_response.text
    personas_response = TestClient(server.app).get("/api/feedback/personas")
    assert personas_response.status_code == 404
