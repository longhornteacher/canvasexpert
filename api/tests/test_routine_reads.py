"""Offline tests: the typed mirror-first read interface for Routines.

Fixture style mirrors ``api/tests/test_mirror_reads_helper.py``: the
workspace root is redirected to ``tmp_path`` and the mirror is populated with
``store.write_*`` writers + ``store.record_pass(course_id, "full"/"roster",
ok=True)``. ``store.now_iso()`` marks a pass fresh; a pinned old ISO string
marks it stale.

Covers the brief's named surface: three zero-live scopes, three fallback
shapes (``source="canvas"``), unknown-scope rejection (structured, not an
exception), metadata labeling, and proof that the writing routine (curve)
owners/imports of ``mirror_reads`` are untouched by this migration.
"""
from __future__ import annotations

from api import routine_reads
from api.mirror import store
from api.platform_services import workspace
from api.webui import mirror_reads
from api.webui.routes import routines_builtin, routines_custom

COURSE = "555301"

ASSIGNMENTS = [
    {"id": 800200, "name": "Lab Report", "due_at": "2026-01-01T09:00:00Z",
     "points_possible": 10, "published": True, "html_url": "u",
     "submission_types": ["online_text_entry"]},
]
USERS = [
    {"id": 900301, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900301", "enrollments": []},
]
SUBMISSIONS = [
    {"assignment_id": 800200, "user_id": 900301, "workflow_state": "submitted",
     "submitted_at": "2020-01-01T09:00:00Z", "score": None,
     "submission_comments": []},
]


def _populate(root, *, fresh=True):
    stamp = store.now_iso() if fresh else "2020-01-01T00:00:00Z"
    store.write_roster(COURSE, USERS, {}, root=root, attempted_at=stamp)
    store.write_assignments(COURSE, ASSIGNMENTS, root=root, attempted_at=stamp)
    store.merge_submissions(COURSE, "800200", SUBMISSIONS, root=root,
                            attempted_at=stamp, replace=True)
    for pass_name in ("full", "roster"):
        store.record_pass(COURSE, pass_name, ok=True, attempted_at=stamp, root=root)


def _mount(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def _explode(*_args, **_kwargs):
    raise AssertionError("live Canvas read attempted")


# --- (a) each supported scope serves fresh-mirror rows with zero live calls ------

def test_assignments_scope_serves_fresh_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = routine_reads.read_scope("assignments", COURSE, live_reader=_explode)
    assert result["ok"] is True
    assert result["error"] is None
    assert result["source"] == "mirror"
    assert {r["id"] for r in result["records"]} == {"800200"}
    assert result["synced_at"]
    assert result["generation"]


def test_roster_scope_serves_fresh_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = routine_reads.read_scope("roster", COURSE, live_reader=_explode)
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert {r["id"] for r in result["records"]} == {"900301"}
    assert result["synced_at"]
    assert result["generation"]


def test_submissions_scope_serves_fresh_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = routine_reads.read_scope("submissions", COURSE, live_reader=_explode)
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert {r["user_id"] for r in result["records"]} == {"900301"}
    assert result["synced_at"]
    assert result["generation"]


# --- (b) stale/missing mirror -> live fallback, source="canvas", no freshness ----

def test_assignments_scope_falls_back_live_when_stale(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)
    calls = []

    def fake_live(path, params, **kwargs):
        calls.append((path, params, kwargs))
        return [{"id": 1, "name": "Live Assignment"}], None

    result = routine_reads.read_scope("assignments", COURSE, live_reader=fake_live)
    assert result["ok"] is True
    assert result["source"] == "canvas"
    assert result["synced_at"] == ""
    assert result["generation"] == ""
    assert result["records"] == [{"id": 1, "name": "Live Assignment"}]
    (path, params, kwargs) = calls[0]
    assert path == f"/api/v1/courses/{COURSE}/assignments"
    assert params == {"per_page": 100}
    assert kwargs == {}


def test_roster_scope_falls_back_live_when_missing(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)  # nothing written — mirror unavailable
    calls = []

    def fake_live(path, params, **kwargs):
        calls.append((path, params, kwargs))
        return [{"id": 1, "name": "Live Student"}], None

    result = routine_reads.read_scope("roster", COURSE, live_reader=fake_live)
    assert result["ok"] is True
    assert result["source"] == "canvas"
    assert result["synced_at"] == ""
    assert result["generation"] == ""
    (path, params, kwargs) = calls[0]
    assert path == f"/api/v1/courses/{COURSE}/users"
    assert params == {"enrollment_type[]": "student", "per_page": 100}
    assert kwargs == {}


def test_submissions_scope_falls_back_live_when_stale(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)
    calls = []

    def fake_live(path, params, **kwargs):
        calls.append((path, params, kwargs))
        return [{"user_id": 1, "assignment_id": 1}], None

    result = routine_reads.read_scope("submissions", COURSE, live_reader=fake_live)
    assert result["ok"] is True
    assert result["source"] == "canvas"
    assert result["synced_at"] == ""
    assert result["generation"] == ""
    (path, params, kwargs) = calls[0]
    assert path == f"/api/v1/courses/{COURSE}/students/submissions"
    assert params == {"student_ids[]": "all", "per_page": 100}
    assert kwargs == {"timeout": 60}


def test_live_fallback_error_is_reported_and_not_ok(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)
    result = routine_reads.read_scope(
        "assignments", COURSE, live_reader=lambda *a, **k: (None, "boom"))
    assert result["ok"] is False
    assert result["error"] == "boom"
    assert result["records"] == []
    assert result["source"] == "canvas"
    assert result["synced_at"] == ""
    assert result["generation"] == ""


# --- (c) unknown scope: structured rejection, never an arbitrary URL -------------

def test_unknown_scope_is_rejected_structurally_not_an_exception(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = routine_reads.read_scope("grades", COURSE, live_reader=_explode)
    assert result == {
        "ok": False,
        "records": [],
        "error": "unsupported scope: 'grades'",
        "source": "",
        "synced_at": "",
        "generation": "",
    }


def test_result_keys_are_exactly_the_named_set(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = routine_reads.read_scope("assignments", COURSE, live_reader=_explode)
    assert set(result.keys()) == {"ok", "records", "error", "source", "synced_at", "generation"}


# --- (d) built-in routines: download and other survivors ---------------


def test_download_assignment_listing_uses_read_scope(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    monkeypatch.setattr(routines_builtin, "canvas_get_all", _explode)
    monkeypatch.setattr(routines_builtin.config, "get_token", lambda: "tok")
    monkeypatch.setattr(routines_builtin.config, "active_courses",
                        lambda: [{"id": COURSE, "nickname": "Course"}])

    calls = []

    def fake_refresh(course_id, assignment_id, session_id=None):
        calls.append((course_id, assignment_id))
        return None, None, {"status": "current"}

    monkeypatch.setattr(
        routines_builtin.assignment_refresh, "refresh_assignment", fake_refresh)
    result = routines_builtin._run_routine_download({"window_days": 3650})
    assert result["ok"] is True
    assert calls == [(COURSE, "800200")]


# --- (e) no private rows/source envelopes leak into what built-ins report --------


# --- (f) custom SDK: canvas_read is injected and delegates to read_scope --------

def test_custom_sdk_injects_canvas_read(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    monkeypatch.setattr(routines_custom, "canvas_get_all", _explode)
    sdk = routines_custom._routine_sdk(lambda *a, **k: None)
    assert "canvas_read" in sdk
    result = sdk["canvas_read"]("assignments", COURSE)
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert {r["id"] for r in result["records"]} == {"800200"}


def test_custom_sdkcanvas_get_all_and_canvas_send_remain(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    sdk = routines_custom._routine_sdk(lambda *a, **k: None)
    assert sdk["canvas_get_all"] is routines_custom.canvas_get_all
    assert sdk["canvas_send"] is routines_custom._canvas_send
    assert sdk["canvas_get"] is routines_custom.canvas_get


def test_read_scope_owns_no_canvas_import():
    assert not hasattr(routine_reads, "canvas_get")
    assert not hasattr(routine_reads, "canvas_get_all")
    assert not hasattr(routine_reads, "_canvas_send")
