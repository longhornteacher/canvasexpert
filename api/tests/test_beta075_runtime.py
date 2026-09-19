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
    assert any(route.path == "/api/feedback/personas" for route in server.app.routes)

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
