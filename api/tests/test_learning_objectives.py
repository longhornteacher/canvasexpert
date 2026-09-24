import copy
import json
import os

from api import course_catalog, learning_objectives
from api.mcp_server import tools
from api.platform_services import workspace


STAMP = "2026-08-30T12:00:00+00:00"


def _catalog():
    return {
        "version": 3,
        "course_id": "course-1",
        "course_name": "Fictional Course",
        "updated_at": STAMP,
        "assignments": {"state": "current", "last_success_at": STAMP, "last_attempt_at": STAMP, "error_code": "", "records": {
            "a1": {"id": "a1", "name": "Explain Evidence", "description_text": "", "points_possible": 10,
                   "due_at": "", "unlock_at": "", "lock_at": "", "created_at": "", "updated_at": "",
                   "published": True, "submission_types": [], "assignment_group_id": "", "quiz_id": "",
                   "is_quiz": False, "quiz_kind": "", "is_quiz_lti_assignment": False, "rubric": [],
                   "rubric_settings": {"id": "", "title": "", "points_possible": None,
                                        "free_form_criterion_comments": False,
                                        "hide_score_total_for_assessment": False, "hide_points": False,
                                        "hide_outcome_results": False}},
        }},
        "modules": {"state": "current", "last_success_at": STAMP, "last_attempt_at": STAMP, "error_code": "", "records": [
            {"id": "m1", "name": "Unit 1", "position": 1, "items": []},
        ]},
        "assignment_groups": {"state": "current", "last_success_at": STAMP, "last_attempt_at": STAMP, "error_code": "", "records": []},
        "pages": {"state": "current", "last_success_at": STAMP, "last_attempt_at": STAMP, "error_code": "", "records": [
            {"id": "p1", "title": "Welcome", "body_text": "Read this.", "published": True, "front_page": True, "updated_at": STAMP},
        ]},
    }


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    course_catalog.write_catalog(_catalog())
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "course-1", "active": True}])


def test_preview_apply_is_exact_revision_safe_and_atomic(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    preview = tools.preview_learning_objective(
        "course-1", "Explain how evidence supports a claim.", "2026-09-01", "2026-09-12",
        [{"kind": "module", "id": "m1", "title": "Unit 1"}],
    )
    assert preview["ok"] is True
    assert preview["preview"]["source_titles"] == ["Unit 1"]
    assert preview["next"] == tools._NEXT_STEPS["preview_learning_objective"]
    assert "current_revision as expected_revision" in preview["next"]
    applied = tools.apply_learning_objective(
        "course-1", preview["preview"], preview["preview_digest"], 0,
    )
    assert applied == {"ok": True, "revision": 1, "course_id": "course-1"}
    path = workspace.learning_objectives_path()
    stored = json.loads(open(path, encoding="utf-8").read())
    assert stored["revision"] == 1
    assert set(stored) == {"version", "revision", "objectives"}
    assert set(stored["objectives"]["course-1"][0]) == learning_objectives.ENTRY_KEYS
    stale = tools.apply_learning_objective(
        "course-1", preview["preview"], preview["preview_digest"], 0,
    )
    assert stale["ok"] is False
    assert "revision" in stale["error"]


def test_mcp_pages_are_current_gated_and_bounded(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    catalog = _catalog()
    catalog["pages"]["records"][0]["body_text"] = "x" * 400
    course_catalog.write_catalog(catalog)
    preview = tools.get_course_pages("course-1")
    assert preview["ok"] is True
    assert preview["state"] == "current"
    assert len(preview["pages"]["rows"][0][2]) < 400
    full = tools.get_course_pages("course-1", full_text=True)
    assert full["pages"]["rows"][0][2] == "x" * 400
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "other", "active": True}])
    blocked = tools.get_course_pages("course-1")
    assert blocked["ok"] is False


def test_mcp_pages_omits_unpublished_records(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    catalog = _catalog()
    catalog["pages"]["records"].append({
        "id": "p2", "title": "Draft", "body_text": "Not for the classroom.",
        "published": False, "front_page": False, "updated_at": STAMP,
    })
    course_catalog.write_catalog(catalog)
    result = tools.get_course_pages("course-1")
    assert result["pages"]["rows"] == [["p1", "Welcome", "Read this.", True, True, STAMP]]


def test_preview_rejects_unknown_source_and_overlapping_ranges(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    missing = tools.preview_learning_objective(
        "course-1", "Use evidence.", "2026-09-01", "2026-09-12",
        [{"kind": "page", "id": "missing", "title": "Missing"}],
    )
    assert missing["ok"] is False
    assert "source ref" in missing["error"]
    first = tools.preview_learning_objective(
        "course-1", "Use evidence.", "2026-09-01", "2026-09-12",
        [{"kind": "page", "id": "p1", "title": "Welcome"}],
    )
    assert tools.apply_learning_objective("course-1", first["preview"], first["preview_digest"], 0)["ok"]
    overlap = tools.preview_learning_objective(
        "course-1", "Explain evidence.", "2026-09-10", "2026-09-20",
        [{"kind": "page", "id": "p1", "title": "Welcome"}],
    )
    assert overlap["ok"] is False
    assert "overlap" in overlap["error"]


def test_v2_objective_limits_ids_and_replace_delete_loop(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    punctuation = "A" * 480 + " U.S. Dr. e.g."
    preview = tools.preview_learning_objective(
        "course-1", punctuation, "2026-09-01", "2026-09-12",
        [{"kind": "module", "id": "m1", "title": "Unit 1"}],
    )
    assert preview["ok"] is True
    assert preview["preview"]["replaces"] is None
    ident = preview["preview"]["proposed"]["id"]
    assert ident and len(ident) <= 128
    assert tools.apply_learning_objective(
        "course-1", preview["preview"], preview["preview_digest"], 0,
    )["ok"]
    listed = tools.list_learning_objectives("course-1")
    assert listed["objectives"]["columns"] == [
        "id", "objective", "effective_start", "effective_end",
        "source_titles", "authored_at",
    ]
    replacement = tools.preview_learning_objective(
        "course-1", "Explain U.S. evidence, Dr. King, e.g. a source.",
        "2026-09-01", "2026-09-12",
        [{"kind": "module", "id": "m1", "title": "Unit 1"}],
        replaces=ident,
    )
    assert replacement["preview"]["outgoing"] == punctuation
    assert replacement["preview"]["incoming"].startswith("Explain")
    assert tools.apply_learning_objective(
        "course-1", replacement["preview"], replacement["preview_digest"],
        1,
    )["revision"] == 2
    assert tools.delete_learning_objective("course-1", ident, 2) == {
        "ok": True, "course_id": "course-1", "revision": 3,
    }
    assert json.loads(open(workspace.learning_objectives_path(), encoding="utf-8").read())["objectives"]["course-1"] == []


def test_v1_document_is_rejected_without_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    path = workspace.learning_objectives_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "revision": 0, "objectives": {}}, handle)
    try:
        learning_objectives.read_document()
    except ValueError as error:
        assert str(error) == "objective_document_invalid"
    else:
        raise AssertionError("v1 document was accepted")
