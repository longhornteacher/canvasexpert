import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_workspace_paths_are_resolved_at_call_time(tmp_path, monkeypatch):
    from api import runtime_paths
    from api.platform_services import config, workspace
    from api.webui import ai_ta, deps
    from api.webui.routes import library

    roots = {"current": tmp_path / "one"}
    for label in ("one", "two"):
        root = tmp_path / label
        (root / "Library" / "AI Authoring").mkdir(parents=True)
        (root / "Library" / "AI Authoring" / "Author a Quiz (QuizForge).txt").write_text(
            f"{label} AI Authoring marker\n", encoding="utf-8"
        )

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(roots["current"]))
    first_ai_ta = deps.list_ai_ta_files()
    assert any(str(tmp_path / "one") in item["path"] for item in first_ai_ta)

    roots["current"] = tmp_path / "two"
    second_ai_ta = deps.list_ai_ta_files()
    assert any(str(tmp_path / "two") in item["path"] for item in second_ai_ta)
    assert all(str(tmp_path / "one") not in item["path"] for item in second_ai_ta)

    built = ai_ta.build_library(runtime_paths.ai_ta_dir())
    assert all(Path(path).is_relative_to(tmp_path / "two") for path in built)

    rebuilt = json.loads(library.api_ai_ta_rebuild().body)
    assert rebuilt["ok"] is True
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
    from api.webui import ai_ta, server

    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    monkeypatch.setattr(runtime_paths, "app_root", lambda: tmp_path)

    assert runtime_paths.ai_ta_dir() is None

    ai_ta_target = runtime_paths.ai_ta_dir()
    if ai_ta_target is not None:
        ai_ta.build_library(ai_ta_target)

    assert not (tmp_path / "AI Authoring").exists()
    assert list(tmp_path.iterdir()) == []

    # The same guard server._lifespan applies must be present in the module
    # source, so a future edit that removes the None check is caught even if
    # this test's own inline mirror of it is not.
    import inspect
    lifespan_src = inspect.getsource(server._lifespan)
    assert "ai_ta_dir()" in lifespan_src
    assert "is not None" in lifespan_src


def test_all_content_pickers_use_only_the_synced_library(tmp_path, monkeypatch):
    from api import runtime_paths
    from api.platform_services import workspace
    from api.webui import deps

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
    from api.webui import deps

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
    from api.platform_services import workspace
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

    class CountingVault:
        def __init__(self):
            self.save_calls = 0
            self.pseudonyms = {}

        def get_or_assign(self, canvas_id, *_args, **_kwargs):
            return self.pseudonyms.setdefault(str(canvas_id), "Avery Example")

        def add_nicknames(self, *_args, **_kwargs):
            return None

        def all_real_identifiers(self):
            return [], []

        def save(self):
            self.save_calls += 1

    vault = CountingVault()
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "course-1"}])
    mirror_store.write_roster(
        "course-1", [{"id": "student-1", "name": "Synthetic Student"}], {},
        root=str(tmp_path),
    )
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)

    result = tools.get_roster("course-1")
    assert result["ok"] is True
    assert vault.save_calls == 1

    monkeypatch.setattr(server.config, "token_is_set", lambda: True)
    monkeypatch.setattr(server.config, "get_canvas_base", lambda: "https://canvas.invalid")
    response = TestClient(server.app).get("/about")
    assert response.status_code == 200
    assert __version__ in response.text
    assert "every assignment currently needing scoring" in response.text
    assert "one pseudonymized assignment packet at a time" in response.text

    # Consistent scoring feedback: personas are removed entirely. /settings
    # renders with no Personas row, and the retired /api/feedback/* routes
    # are gone (404), never rendered against the real workspace.
    settings_response = TestClient(server.app).get("/settings")
    assert settings_response.status_code == 200
    assert "Personas" not in settings_response.text
    personas_response = TestClient(server.app).get("/api/feedback/personas")
    assert personas_response.status_code == 404
