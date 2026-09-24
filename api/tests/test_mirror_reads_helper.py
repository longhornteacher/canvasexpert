"""Offline tests: mirror-first reads for routine display consumers.

Fixture style mirrors ``api/tests/test_mirror_queries.py`` /
``api/tests/test_work_providers_mirror.py``: the workspace root is redirected
to ``tmp_path`` and the mirror is populated with ``store.write_*`` writers +
``store.record_pass(course_id, "full"/"roster", ok=True)``. ``store.now_iso()``
marks a pass fresh; a pinned old ISO string marks it stale.

Write-path observation (documented here, not just in the summary): reads that
feed a Canvas write stay fully live, because mirror-served computes could build
a write on stale data, which locked decision 1 forbids. The built-in curve
routine now delegates its reviewed write to the grade-adjustment operation;
these helpers cover only independent mirror-first student-data reads. Tests
below lock in that split.
"""
from __future__ import annotations

from api.mirror import store
from api.platform_services import workspace
from api.webui import mirror_reads

COURSE = "555201"

ASSIGNMENTS = [
    {"id": 700200, "name": "Lab Report", "due_at": "2026-01-01T09:00:00Z",
     "points_possible": 10, "published": True, "html_url": "u",
     "submission_types": ["online_text_entry"]},
]
USERS = [
    {"id": 900201, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900201", "enrollments": []},
    {"id": 900202, "name": "Learner Two", "sortable_name": "Two, Learner",
     "short_name": "Learner Two", "sis_user_id": "SIS-900202", "enrollments": []},
]
# One old, ungraded "submitted" row (grading-debt candidate) and one graded
# row (not a debt). Old enough that "days ungraded" clears any min_days
# threshold regardless of when the suite runs.
SUBMISSIONS = [
    {"assignment_id": 700200, "user_id": 900201, "workflow_state": "submitted",
     "submitted_at": "2020-01-01T09:00:00Z", "score": None,
     "submission_comments": []},
    {"assignment_id": 700200, "user_id": "900202", "workflow_state": "graded",
     "submitted_at": "2020-01-02T09:00:00Z", "score": 8,
     "submission_comments": []},
]


def _populate(root, *, fresh=True):
    stamp = store.now_iso() if fresh else "2020-01-01T00:00:00Z"
    store.write_roster(COURSE, USERS, {}, root=root, attempted_at=stamp)
    store.write_assignments(COURSE, ASSIGNMENTS, root=root, attempted_at=stamp)
    store.merge_submissions(COURSE, "700200", SUBMISSIONS, root=root,
                            attempted_at=stamp, replace=True)
    for pass_name in ("full", "roster"):
        store.record_pass(COURSE, pass_name, ok=True, attempted_at=stamp, root=root)


def _mount(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def _explode(*_args, **_kwargs):
    raise AssertionError("live Canvas read attempted")


# --- (a) each helper serves fresh-mirror rows with zero live calls -----------------

def test_students_or_live_serves_fresh_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    monkeypatch.setattr(mirror_reads, "canvas_get_all", _explode)
    rows, err, source = mirror_reads.students_or_live(COURSE)
    assert err is None and source == "mirror"
    assert {r["id"] for r in rows} == {"900201", "900202"}


def test_assignments_or_live_serves_fresh_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    monkeypatch.setattr(mirror_reads, "canvas_get_all", _explode)
    rows, err, source = mirror_reads.assignments_or_live(COURSE)
    assert err is None and source == "mirror"
    assert {r["id"] for r in rows} == {"700200"}


def test_submissions_or_live_serves_fresh_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    monkeypatch.setattr(mirror_reads, "canvas_get_all", _explode)
    rows, err, source = mirror_reads.submissions_or_live(COURSE)
    assert err is None and source == "mirror"
    assert {r["user_id"] for r in rows} == {"900201", "900202"}


# --- (b) stale mirror -> live fallback with source "canvas" -----------------------

def test_students_or_live_falls_back_live_when_stale(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)
    live_rows = [{"id": 1, "name": "Live Student"}]
    monkeypatch.setattr(mirror_reads, "canvas_get_all", lambda *a, **k: (live_rows, None))
    rows, err, source = mirror_reads.students_or_live(COURSE)
    assert err is None and source == "canvas" and rows == live_rows


def test_assignments_or_live_falls_back_live_when_stale(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)
    live_rows = [{"id": 1, "name": "Live Assignment"}]
    monkeypatch.setattr(mirror_reads, "canvas_get_all", lambda *a, **k: (live_rows, None))
    rows, err, source = mirror_reads.assignments_or_live(COURSE)
    assert err is None and source == "canvas" and rows == live_rows


def test_submissions_or_live_falls_back_live_when_stale(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path), fresh=False)
    live_rows = [{"user_id": 1, "assignment_id": 1}]
    monkeypatch.setattr(mirror_reads, "canvas_get_all", lambda *a, **k: (live_rows, None))
    rows, err, source = mirror_reads.submissions_or_live(COURSE)
    assert err is None and source == "canvas" and rows == live_rows


def test_helper_falls_back_live_when_mirror_read_errors(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    from api.mirror import queries as mirror_queries
    monkeypatch.setattr(mirror_queries, "course_assignments", lambda course_id, **k: (None, "boom"))
    live_rows = [{"id": 1}]
    monkeypatch.setattr(mirror_reads, "canvas_get_all", lambda *a, **k: (live_rows, None))
    rows, err, source = mirror_reads.assignments_or_live(COURSE)
    assert err is None and source == "canvas" and rows == live_rows

