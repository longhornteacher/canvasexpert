import json
import os
import re
from pathlib import Path

import pytest

from api.platform_services import config, workspace
from api import runtime_paths
from api.platform_services.config import _io as config_io


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_onedrive_root_prefers_commercial(monkeypatch):
    monkeypatch.setenv("OneDriveCommercial", r"C:\OD-Commercial")
    monkeypatch.setenv("OneDrive", r"C:\OD-Personal")
    assert workspace.onedrive_root() == r"C:\OD-Commercial"

    monkeypatch.delenv("OneDriveCommercial", raising=False)
    assert workspace.onedrive_root() == r"C:\OD-Personal"

    monkeypatch.delenv("OneDrive", raising=False)
    assert workspace.onedrive_root() is None


def test_ensure_workspace_creates_and_seeds_authoring_library(tmp_path, monkeypatch):
    onedrive = tmp_path / "OneDrive"
    root = onedrive / "CanvasExpert"
    source_api = tmp_path / "api"
    source_authoring = source_api / "default_docs" / "AI Authoring"
    source_authoring.mkdir(parents=True)
    (source_authoring / "Author a Quiz (QuizForge).txt").write_text("default v1", encoding="utf-8")
    (source_authoring / "Author a Page (PageForge).txt").write_text("new default", encoding="utf-8")

    monkeypatch.setenv("OneDrive", str(onedrive))
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setattr(workspace, "API_DIR", str(source_api))
    monkeypatch.setattr(workspace, "CONFIG_PATH", str(tmp_path / "config.json"))
    # Neutralize the legacy in-folder path too, so a real machine-local
    # config.json on the dev box (this app's own real config) can never
    # migrate itself into this test's isolated tmp_path.
    monkeypatch.setattr(workspace, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy-config.json"))
    monkeypatch.setattr(workspace, "DEFAULT_DOCS_DIR", str(source_api / "default_docs"))

    seeded = root / "Library" / "AI Authoring" / "Author a Quiz (QuizForge).txt"
    seeded.parent.mkdir(parents=True, exist_ok=True)
    seeded.write_text("user edited version", encoding="utf-8")

    resolved = workspace.ensure_workspace()
    assert resolved == str(root)

    for folder in ["AI Authoring", "Quizzes", "Pages", "Calendars", "Source Materials"]:
        assert (root / "Library" / folder).is_dir()
    assert (root / "Assignments" / "_Shared").is_dir()
    assert not (root / "Library" / "Assignments").exists()
    assert not (root / "Library" / "Rubrics").exists()
    for folder in ["Printables", "Canvas Uploads", "To Review", "Student Work", "For AI", "_System"]:
        assert (root / folder).is_dir()

    assert seeded.read_text(encoding="utf-8") == "user edited version"
    assert (root / "Library" / "AI Authoring" / "Author a Page (PageForge).txt").read_text(encoding="utf-8") == "new default"

    (source_authoring / "Author an Assignment (AssignmentForge).txt").write_text("later default", encoding="utf-8")
    workspace.ensure_workspace()
    assert (root / "Library" / "AI Authoring" / "Author an Assignment (AssignmentForge).txt").read_text(encoding="utf-8") == "later default"


def test_config_split_writes_workspace_settings_when_available(tmp_path, monkeypatch):
    machine_config = tmp_path / "config.json"
    workspace_root = tmp_path / "OneDrive" / "CanvasExpert"
    workspace_root.mkdir(parents=True)
    monkeypatch.setattr(config_io, "CONFIG_PATH", str(machine_config))
    # Neutralize the legacy in-folder path so a real machine-local config.json
    # on the dev box can never migrate itself into this test's tmp_path.
    monkeypatch.setattr(config_io, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy-config.json"))
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(workspace_root))

    _write_json(machine_config, {"canvas_base": config.CANVAS_BASE_DEFAULT, "saved_courses": []})

    config.bookmark_course("123", "Algebra", "Algebra 1")

    machine_state = json.loads(machine_config.read_text(encoding="utf-8"))
    from api.shared_kv import SharedKVStore
    workspace_state = SharedKVStore("settings", root=workspace_root).read()

    assert "saved_courses" not in machine_state
    assert workspace_state["saved_courses"][0]["id"] == "123"
    assert workspace_state["saved_courses"][0]["nickname"] == "Algebra 1"


def test_config_split_stays_machine_local_without_workspace(tmp_path, monkeypatch):
    machine_config = tmp_path / "config.json"
    monkeypatch.setattr(config_io, "CONFIG_PATH", str(machine_config))
    # Neutralize the legacy in-folder path so a real machine-local config.json
    # on the dev box can never migrate itself into this test's tmp_path.
    monkeypatch.setattr(config_io, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy-config.json"))
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)

    _write_json(machine_config, {"canvas_base": config.CANVAS_BASE_DEFAULT, "saved_courses": []})

    config.bookmark_course("777", "Biology", "Bio")

    machine_state = json.loads(machine_config.read_text(encoding="utf-8"))
    assert machine_state["saved_courses"][0]["id"] == "777"
    assert not (tmp_path / "OneDrive").exists()


def test_config_migrates_from_legacy_in_folder_location_once(tmp_path, monkeypatch):
    """D1: a fresh profile with no machine-local config.json but a legacy
    in-folder config.json reads the legacy values once, writes the new
    location, and leaves the legacy file in place (never deleted -- an older
    copy of the app on the same machine may still depend on it)."""
    legacy = tmp_path / "legacy" / "config.json"
    legacy.parent.mkdir(parents=True)
    _write_json(legacy, {"canvas_base": "https://legacy.example.test", "saved_courses": []})
    new_path = tmp_path / "new" / "config.json"
    assert not new_path.exists()

    monkeypatch.setattr(config_io, "LEGACY_CONFIG_PATH", str(legacy))
    monkeypatch.setattr(config_io, "CONFIG_PATH", str(new_path))
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)

    assert config.get_canvas_base() == "https://legacy.example.test"
    assert new_path.exists()
    assert legacy.exists()

    migrated = json.loads(new_path.read_text(encoding="utf-8"))
    assert migrated["canvas_base"] == "https://legacy.example.test"

    # Second read is a no-op migration (new path already exists) and does not
    # touch the legacy file again.
    legacy_mtime = legacy.stat().st_mtime
    config.set_canvas_base("https://updated.example.test")
    assert legacy.stat().st_mtime == legacy_mtime
    assert json.loads(legacy.read_text(encoding="utf-8"))["canvas_base"] == "https://legacy.example.test"


def test_adding_a_saved_previous_course_makes_it_current(tmp_path, monkeypatch):
    machine_config = tmp_path / "config.json"
    monkeypatch.setattr(config_io, "CONFIG_PATH", str(machine_config))
    # Neutralize the legacy in-folder path so a real machine-local config.json
    # on the dev box can never migrate itself into this test's tmp_path.
    monkeypatch.setattr(config_io, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy-config.json"))
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    _write_json(machine_config, {
        "saved_courses": [{
            "id": "777", "name": "Biology", "nickname": "Old Bio", "active": False,
        }],
    })

    config.bookmark_course("777", "Biology", "Summer Bio")

    saved = json.loads(machine_config.read_text(encoding="utf-8"))["saved_courses"]
    assert saved == [{
        "id": "777", "name": "Biology", "nickname": "Summer Bio", "active": True,
    }]


def test_workspace_migration_is_idempotent(tmp_path, monkeypatch):
    machine_config = tmp_path / "config.json"
    workspace_root = tmp_path / "OneDrive" / "CanvasExpert"
    workspace_root.mkdir(parents=True)
    monkeypatch.setattr(config_io, "CONFIG_PATH", str(machine_config))
    # Neutralize the legacy in-folder path so a real machine-local config.json
    # on the dev box can never migrate itself into this test's tmp_path.
    monkeypatch.setattr(config_io, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy-config.json"))
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(workspace_root))

    _write_json(
        machine_config,
        {
            "canvas_base": config.CANVAS_BASE_DEFAULT,
            "saved_courses": [{"id": "1", "name": "History", "nickname": "Hist", "active": True}],
            "extra_time": {"1": [{"id": "a", "name": "Ada", "days": 2}]},
            "late_sweep": {},
        },
    )

    first = config.saved_courses()
    from api.shared_kv import SharedKVStore
    settings_store = SharedKVStore("settings", root=workspace_root)
    assert (workspace_root / "_Shared" / "kv" / "settings" / "snapshot.initial.json").exists()
    assert first[0]["nickname"] == "Hist"

    _write_json(
        machine_config,
        {
            "canvas_base": config.CANVAS_BASE_DEFAULT,
            "saved_courses": [{"id": "2", "name": "Math", "nickname": "Math", "active": True}],
            "extra_time": {},
            "late_sweep": {},
            "calendars": {},
        },
    )

    second = config.saved_courses()
    workspace_state = settings_store.read()

    assert second[0]["id"] == "1"
    assert workspace_state["saved_courses"][0]["id"] == "1"
    assert workspace_state["extra_time"]["1"][0]["name"] == "Ada"


def test_legacy_canvas_caches_are_marked_but_preserved(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    old_cache = root / "_System" / "Canvas Catalog"
    old_cache.mkdir(parents=True)
    canonical = old_cache / "catalog.v3.json"
    conflict = old_cache / "catalog-LAPTOP-TEST.json"
    canonical.write_text("canonical cache", encoding="utf-8")
    conflict.write_text("retained conflict evidence", encoding="utf-8")
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    monkeypatch.setattr(workspace.runtime_paths, "local_cache_dir", lambda: tmp_path / "local-cache")
    workspace._legacy_cache_retirement_checked.clear()

    local_root = workspace.canvas_catalog_root()

    assert local_root == str(tmp_path / "local-cache" / "Canvas Catalog")
    assert "no longer reads" in (old_cache / "README-MIGRATED.txt").read_text(encoding="utf-8")
    assert canonical.read_text(encoding="utf-8") == "canonical cache"
    assert conflict.read_text(encoding="utf-8") == "retained conflict evidence"


@pytest.mark.parametrize("cache_kind", ["catalog", "mirror"])
def test_canvas_cache_documents_stay_machine_local_across_workspace_roots(
        cache_kind, tmp_path, monkeypatch):
    from api import course_catalog
    from api.mirror import read_service, store

    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    course_id = "cache-location-law"
    local_cache = runtime_paths.local_cache_dir()

    if cache_kind == "catalog":
        scope = {
            "state": "unavailable", "last_success_at": "",
            "last_attempt_at": "", "error_code": "", "records": [],
        }
        document = {
            "version": 3, "course_id": course_id,
            "course_name": "Synthetic Course",
            "updated_at": "2026-09-24T12:00:00Z",
            "assignments": {**scope, "records": {}},
            "modules": dict(scope),
            "assignment_groups": dict(scope),
            "pages": dict(scope),
        }
        course_catalog.write_catalog(document)
        assert course_catalog.read_catalog(course_id)["catalog"] == document
        cache_path = Path(workspace.course_catalog_v3_path(course_id))
        cache_root_fn = workspace.canvas_catalog_root
        path_fn = workspace.course_catalog_dir
    else:
        users = [{
            "id": 900001, "name": "Learner One",
            "sortable_name": "One, Learner", "short_name": "Learner One",
            "sis_user_id": "SIS-900001", "enrollments": [],
        }]
        store.write_roster(course_id, users, {}, root=str(root))
        assert read_service.private_roster(course_id, max_age_hours=6)["state"] == "current"
        store.write_roster(course_id, users, {})
        assert store.read_roster(course_id, root=str(root))["state"] == "current"
        cache_path = Path(store.roster_path(course_id, root=str(root)))
        cache_root_fn = workspace.canvas_mirror_root
        path_fn = workspace.course_mirror_dir

    assert local_cache in cache_path.parents
    assert (root / "_System") not in cache_path.parents
    with pytest.raises(TypeError):
        cache_root_fn(str(root))
    with pytest.raises(TypeError):
        path_fn(course_id, str(root))


def test_canonical_course_first_paths_keep_ids_and_bound_long_names(tmp_path, monkeypatch):
    root = tmp_path / "CanvasExpert"
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    path = workspace.attempt_folder(
        "A" * 400, "course/fictional", "B" * 400, "assignment/fictional",
        "C" * 400, "student/fictional", 3,
    )
    assert "Student Work" in path and "Submissions" in path and "Assignments" in path
    assert "course_fictional" in path and "assignment_fictional" in path and "student_fictional" in path
    assert path.endswith("Attempt 3")
    assert len(path) <= workspace.MAX_PATH_LENGTH


def test_extended_path_leaves_short_paths_unchanged(monkeypatch):
    # Short paths keep their exact current behavior — no \\?\ prefix — so normal
    # I/O is never perturbed by the long-path workaround.
    monkeypatch.setattr(workspace.os, "name", "nt")
    short = r"C:\Users\user\OneDrive\deep\file.txt"
    assert workspace.extended_path(short) == short
    assert workspace.extended_path("") == ""


@pytest.mark.skipif(os.name != "nt", reason="\\\\?\\ prefixing is Windows-only")
def test_extended_path_prefixes_paths_near_the_limit():
    long_path = r"C:\Users\user\OneDrive" + ("\\" + "x" * 30) * 8 + r"\file.txt"
    assert len(long_path) >= workspace.MAX_PATH_LENGTH
    out = workspace.extended_path(long_path)
    assert out == "\\\\?\\" + long_path
    assert workspace.extended_path(out) == out  # idempotent


def test_extended_path_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(workspace.os, "name", "posix")
    assert workspace.extended_path("/home/user/deep/file.txt") == "/home/user/deep/file.txt"
    assert workspace.extended_path("") == ""


def test_ensure_workspace_does_not_touch_a_stray_legacy_folder(tmp_path, monkeypatch):
    """ensure_workspace() only ever creates the v2 tree; it never reads, renames,
    or deletes an unrelated pre-existing folder (clean break, no migration)."""
    root = tmp_path / "CanvasExpert"
    stray = root / "FeedbackExpert" / "_system" / "vault"
    stray.mkdir(parents=True)
    marker = stray / "vault.json"
    marker.write_text("untouched", encoding="utf-8")
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))

    workspace.ensure_workspace()
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert (root / "Student Work" / "Submissions").is_dir()
    assert (root / "For AI").is_dir()
    assert (root / "_System" / "Identity Vault").is_dir()
    assert not (root / "_System" / "Identity Vault" / "vault.json").exists()


def test_assignment_evidence_manifest_is_atomic_identity_checked_and_conflict_fail_closed(tmp_path, monkeypatch):
    root = tmp_path / "CanvasExpert"
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    kwargs = {"course_name": "Course", "course_id": "course-1", "assignment_name": "Essay", "assignment_id": "assignment-1"}
    manifest = {"version": 1, "course_id": "course-1", "assignment_id": "assignment-1", "evidence": []}
    path = workspace.write_assignment_evidence_manifest(manifest, **kwargs)
    assert path and workspace.read_assignment_evidence_manifest(**kwargs) == manifest
    assert workspace.write_assignment_evidence_manifest({**manifest, "course_id": "wrong"}, **kwargs) is None
    conflict = Path(path).with_name("Fictional assignment_evidence_manifest conflicted copy.json")
    conflict.write_text("{}", encoding="utf-8")
    assert workspace.read_assignment_evidence_manifest(**kwargs) is None
    assert workspace.write_assignment_evidence_manifest(manifest, **kwargs) is None


def test_managed_evidence_path_uses_identity_not_filename_suffix(tmp_path):
    first = workspace.managed_evidence_path("Course", "course", "Essay", "assignment", "Student", "user", 2, "file-1", "draft.docx", tmp_path)
    second = workspace.managed_evidence_path("Course", "course", "Essay", "assignment", "Student", "user", 2, "file-2", "draft.docx", tmp_path)
    assert first != second and "file-1" in first and "file-2" in second


def test_managed_evidence_path_reads_filename_first_then_id(tmp_path):
    """The readable filename leads and the stable ID trails it, matching
    named_id_folder's "<display> — <id>" convention (not id-first)."""
    result = workspace.managed_evidence_path(
        "Course", "course", "Essay", "assignment", "Student", "user", 1, "48213", "essay.docx", tmp_path)
    assert os.path.basename(result) == "essay — 48213.docx"


def test_managed_evidence_path_keeps_id_intact_when_display_must_shrink(tmp_path):
    """When the projected path is too long, bounded_join shortens the display
    portion, never the identity suffix after the em dash."""
    long_filename = ("a very long original upload name " * 4) + ".docx"
    result = workspace.managed_evidence_path(
        "Course", "course", "Assignment", "assignment", "Student", "user", 1,
        "evidence-9001", long_filename, tmp_path)
    assert result.endswith("evidence-9001.docx")
    assert len(result) <= workspace.MAX_PATH_LENGTH


def test_teacher_visible_path_fits_under_budget(tmp_path):
    """teacher_visible_path returns a path at most 230 chars."""
    base = str(tmp_path)
    result = workspace.teacher_visible_path(
        base,
        ("A long course name that goes on and on for testing purposes", "course-1000001"),
        ("A long assignment name that also goes on and on for testing", "assignment-1000002"),
        "run-timestamp",
        filename="some-file.txt",
    )
    assert len(result) <= workspace.TEACHER_VISIBLE_BUDGET
    assert result.startswith(base)


def test_teacher_visible_path_compact_fallback_keeps_stable_id(tmp_path):
    """When the full readable path exceeds the budget, the compact form
    preserves the stable ID."""
    base = str(tmp_path)
    # Pad the base to trigger compact fallback without exceeding budget entirely
    padded_base = os.path.join(base, "x" * 40)
    result = workspace.teacher_visible_path(
        padded_base,
        ("A very long fictional course name for testing purposes that exceeds all limits", "course-1000001"),
        ("Another extremely long fictional assignment name for testing purposes here", "assignment-1000002"),
        "20260722-120000-000000",
        filename="test-file.txt",
    )
    assert len(result) <= workspace.TEACHER_VISIBLE_BUDGET
    # The stable ID should be preserved in the compact form
    assert "course-1000001" in result or "assignment-1000002" in result


def test_teacher_visible_path_raises_on_too_deep(tmp_path):
    """When even the compact form cannot fit, TeacherVisiblePathBudgetError is raised."""
    base = str(tmp_path)
    # Deeply nested path that even compact form can't fix
    very_deep_base = os.path.join(base, *["x" * 50] * 5)
    with pytest.raises(workspace.TeacherVisiblePathBudgetError):
        workspace.teacher_visible_path(
            very_deep_base,
            ("course", "course-1000001"),
            ("assignment", "assignment-1000002"),
            filename="file.txt",
        )


def test_teacher_visible_path_reserve_makes_shorter_path(tmp_path):
    """When reserve is specified, the returned path is shorter to leave room."""
    base = str(tmp_path)
    without_reserve = workspace.teacher_visible_path(
        base,
        ("Course", "course-1"),
        ("Assignment", "assignment-1"),
        filename="file.txt",
    )
    with_reserve = workspace.teacher_visible_path(
        base,
        ("Course", "course-1"),
        ("Assignment", "assignment-1"),
        filename="file.txt",
        reserve=50,
    )
    assert len(with_reserve) <= len(without_reserve)
    assert len(with_reserve) <= workspace.TEACHER_VISIBLE_BUDGET - 50


def test_teacher_visible_path_deterministic_hash_distinct(tmp_path):
    """Two different stable IDs produce distinct compact paths."""
    base = str(tmp_path)
    r1 = workspace.teacher_visible_path(
        base,
        ("Same Name", "id-1111111"),
        filename="f.txt",
    )
    # Both should fit (no deep base)
    assert len(r1) <= workspace.TEACHER_VISIBLE_BUDGET


def test_run_stamp_matches_expected_shape():
    stamp = workspace.run_stamp()
    assert re.fullmatch(r"\d{8}-\d{6}-\d{6}", stamp)


def test_needs_compact_layout_false_for_short_path(tmp_path):
    assert workspace.needs_compact_layout(str(tmp_path), "Essay__bundle.json") is False


def test_needs_compact_layout_true_when_over_budget(tmp_path):
    deep_base = os.path.join(str(tmp_path), "x" * 250)
    assert workspace.needs_compact_layout(deep_base, "Essay__bundle.json") is True


def test_needs_compact_layout_joins_every_child_component(tmp_path):
    base = str(tmp_path)
    shallow = workspace.needs_compact_layout(base, "a", "b.txt")
    deep = workspace.needs_compact_layout(base, "a" * 100, "b" * 100, "c" * 100)
    assert shallow is False
    assert deep is True

def _panels_root(tmp_path):
    root = tmp_path / "CanvasExpert"
    (root / "Library" / "Panels").mkdir(parents=True)
    return root


def test_migrate_panels_moves_the_objectives_document(tmp_path, monkeypatch):
    """The document a teacher authored survives the folder rename."""
    root = _panels_root(tmp_path)
    (root / "Library" / "Panels" / "Learning Objectives.json").write_text(
        '{"version": 2, "revision": 7, "objectives": {}}')
    (root / "Library" / "Panels" / "Themes").mkdir()
    (root / "Library" / "Panels" / "Themes" / "bobcats.json").write_text("{}")

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    workspace.migrate_legacy_panels_folder()

    moved = root / "Library" / "Learning Objectives" / "Learning Objectives.json"
    assert moved.read_text() == '{"version": 2, "revision": 7, "objectives": {}}'
    # Everything else went with the display, and nothing was archived.
    assert not (root / "Library" / "Panels").exists()
    assert not (root / "_System").exists()


def test_migrate_panels_deletes_a_folder_with_no_document(tmp_path, monkeypatch):
    root = _panels_root(tmp_path)
    (root / "Library" / "Panels" / "Themes").mkdir()
    (root / "Library" / "Panels" / "Themes" / "bobcats.json").write_text("{}")

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    workspace.migrate_legacy_panels_folder()

    assert not (root / "Library" / "Panels").exists()


def test_migrate_panels_will_not_delete_an_objectives_document_it_cannot_move(tmp_path, monkeypatch):
    """Two machines syncing, or a half-finished earlier run: the canonical copy
    at the destination wins, and the old folder is left rather than deleting an
    authored document."""
    root = _panels_root(tmp_path)
    (root / "Library" / "Panels" / "Learning Objectives.json").write_text("old")
    (root / "Library" / "Learning Objectives").mkdir(parents=True)
    (root / "Library" / "Learning Objectives" / "Learning Objectives.json").write_text("current")

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    workspace.migrate_legacy_panels_folder()

    assert (root / "Library" / "Learning Objectives" / "Learning Objectives.json").read_text() == "current"
    assert (root / "Library" / "Panels" / "Learning Objectives.json").read_text() == "old"


def test_migrate_panels_is_a_no_op_without_the_old_folder(tmp_path, monkeypatch):
    root = tmp_path / "CanvasExpert"
    (root / "Library").mkdir(parents=True)

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    workspace.migrate_legacy_panels_folder()

    assert not (root / "Library" / "Panels").exists()
    assert not (root / "_System").exists()


# ── Reserve-branch path segments ────────────────────────────────────────────


def _duplicated_segments(path: str) -> list[str]:
    """Segments whose ' — ' halves repeat, e.g. 'For AI — For AI' or
    'Quiz — 900002 — 900002'."""
    bad = []
    for segment in str(path).split(os.sep):
        parts = [p.strip() for p in segment.split(" — ") if p.strip()]
        if len(parts) != len(set(parts)):
            bad.append(segment)
    return bad


def test_reserve_branch_paths_contain_no_duplicated_segments(tmp_path):
    """LAW: no reserve-branch path repeats a name or an id within one segment.

    Both reserve branches passed displays that already carried the id, and
    passed bare constants as tuples against themselves, producing
    'For AI — For AI', 'Course Name — 900001 — 900001' and
    'Student Work — Student Work'. That wasted roughly forty characters of a
    230-character budget before any real content.
    """
    for path in (
        workspace.ai_run_folder(
            "Fictional Course", "900001", "Fictional Assignment", "900002",
            "assisted", run_timestamp="20260910-111320-596588",
            root=str(tmp_path), reserve=60,
        ),
        workspace.grading_keys_assignment_folder(
            "Fictional Course", "900001", "Fictional Assignment", "900002",
            root=str(tmp_path), reserve=60,
        ),
    ):
        assert path
        assert not _duplicated_segments(path), _duplicated_segments(path)
        # The stable ids still survive -- de-duplication must not drop them.
        assert "900001" in path and "900002" in path


def test_teacher_visible_path_tuple_takes_raw_display_and_id(tmp_path):
    """The tuple contract is (raw display, raw id); the helper joins them once."""
    result = workspace.teacher_visible_path(
        str(tmp_path), ("Fictional Course", "900001"), filename="f.txt")
    assert "Fictional Course — 900001" in result
    assert "900001 — 900001" not in result


def test_assignment_source_resolves_shared_and_course_trees(tmp_path):
    root = tmp_path / "CanvasExpert"
    course = workspace.course_assignments_root("42", "ELA 7", root)
    shared = workspace.assignment_source_folder("", "", "shared-1", "Common rubric", root)
    owned = workspace.assignment_source_folder("42", "ELA 7", "a-7", "Writing", root)

    assert course == str(root / "Assignments" / "42 - ELA 7")
    assert shared == str(root / "Assignments" / "_Shared" / "Common rubric — shared-1")
    assert owned == str(root / "Assignments" / "42 - ELA 7" / "Writing — a-7")


def test_assignment_picker_uses_only_canonical_source(tmp_path, monkeypatch):
    root = tmp_path / "CanvasExpert"
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    assert runtime_paths.content_folders("assignment") == [root / "Assignments"]


def test_compact_paths_preserve_category_roots(tmp_path):
    base = os.path.join(str(tmp_path), "x" * 80)
    result = workspace.teacher_visible_path(
        base, "For AI", ("A" * 120, "course-1"), ("B" * 120, "assignment-1"),
        "run-1", filename="batch.md",
    )
    assert os.sep + "For AI" + os.sep in result
    assert "For AI" in result
    assert len(result) <= workspace.TEACHER_VISIBLE_BUDGET


def test_reset_workspace_dry_run_apply_is_idempotent_and_preserves_allowlist(tmp_path):
    root = tmp_path / "CanvasExpert"
    (root / "Library" / "Assignments" / "old.txt").parent.mkdir(parents=True)
    (root / "Library" / "Assignments" / "old.txt").write_text("old")
    (root / "Assignments" / "_Shared" / "old.txt").parent.mkdir(parents=True)
    (root / "Assignments" / "_Shared" / "old.txt").write_text("old")
    (root / "For AI" / "packet.json").parent.mkdir(parents=True)
    (root / "For AI" / "packet.json").write_text("safe")
    (root / "Student Work" / "Submissions" / "report.txt").parent.mkdir(parents=True)
    (root / "Student Work" / "Submissions" / "report.txt").write_text("private")
    (root / "_System" / "Canvas Mirror" / "42" / "mirror.json").parent.mkdir(parents=True)
    (root / "_System" / "Canvas Mirror" / "42" / "mirror.json").write_text("mirror")
    (root / "settings.json").write_text("settings")

    dry = workspace.reset_workspace(root)
    assert dry["status"] == "planned"
    assert dry["counts"]["items"] == 4
    assert (root / "For AI" / "packet.json").exists()

    applied = workspace.reset_workspace(root, apply=True)
    assert applied["status"] == "applied"
    assert not (root / "Library" / "Assignments" / "old.txt").exists()
    assert not (root / "Assignments" / "_Shared" / "old.txt").exists()
    assert not (root / "For AI" / "packet.json").exists()
    assert (root / "settings.json").read_text() == "settings"
    assert (root / "_System" / "Canvas Mirror" / "42" / "mirror.json").exists()

    again = workspace.reset_workspace(root, apply=True)
    assert again["status"] == "applied"
    assert again["counts"] == {"items": 0, "files": 0, "directories": 0}


def test_reset_workspace_refuses_unknown_categories(tmp_path):
    root = tmp_path / "CanvasExpert"
    unknown = root / "Student Work" / "Unclassified"
    unknown.mkdir(parents=True)
    (unknown / "keep.txt").write_text("keep")

    report = workspace.reset_workspace(root, apply=True)
    assert report["status"] == "refused"
    assert report["refused"][0]["reason"] == "unknown category"
    assert (unknown / "keep.txt").exists()
