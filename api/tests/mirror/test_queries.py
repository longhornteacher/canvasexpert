"""Offline tests for mirror-backed reads: the queries provider, the
mirror-first snapshot loader, and the MCP tools' mirror paths.

Freshness uses real store timestamps (now_iso) so "fresh" means fresh; stale
cases pin old attempted_at stamps.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from api import gradebook_queries, gradebook_snapshot
from api import freshness_policy, grading_policy
from api.mirror import queries, store
from api.mcp_server import tools
from api.platform_services import workspace

COURSE = "111"

USERS = [
    {"id": 900001, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900001",
     "enrollments": [{"course_section_id": 800001}]},
    {"id": 900002, "name": "Learner Two", "sortable_name": "Two, Learner",
     "short_name": "Learner Two", "sis_user_id": "SIS-900002",
     "enrollments": [{"course_section_id": 800002}]},
]
SECTIONS = {"800001": "Period 1", "800002": "Period 2"}
ASSIGNMENTS = [
    {"id": 700010, "name": "Essay 1", "due_at": "2026-07-01T23:59:00Z",
     "points_possible": 10, "published": True, "html_url": "u"},
]
SUBMISSIONS = [
    {"assignment_id": 700010, "user_id": 900001, "workflow_state": "graded",
     "submitted_at": "2026-07-01T20:00:00Z", "graded_at": "2026-07-02T09:00:00Z",
     "score": 9, "grade": "9", "late": False, "missing": False, "excused": False,
     "attempt": 1, "grade_matches_current_submission": True,
     "submission_type": "online_text_entry",
     "body": "<p>Learner One and Lee wrote this.</p>"},
    {"assignment_id": 700010, "user_id": 900002, "workflow_state": "unsubmitted",
     "submitted_at": None, "graded_at": None, "score": None, "grade": None,
     "late": False, "missing": True, "excused": False, "attempt": None,
     "grade_matches_current_submission": None, "submission_type": "", "body": ""},
]


# An orphan submission file: an assignment id that has a submissions/*.v1.json
# file on disk but no entry in assignments.v1.json (1.0beta slice 01b).
ORPHAN_ASSIGNMENT_ID = "999999"
ORPHAN_ROW = [{
    "assignment_id": 999999, "user_id": 900001, "workflow_state": "submitted",
    "submitted_at": "2026-07-01T10:00:00Z", "graded_at": None, "score": None,
    "grade": None, "late": False, "missing": False, "excused": False,
    "attempt": 1, "grade_matches_current_submission": True,
    "submission_type": "online_text_entry", "body": "orphan",
}]


def _populate(root, *, fresh=True, stamp=None):
    stamp = stamp or (store.now_iso() if fresh else "2026-01-01T00:00:00Z")
    store.write_roster(COURSE, USERS, SECTIONS, root=root, attempted_at=stamp)
    store.write_assignments(COURSE, ASSIGNMENTS, root=root, attempted_at=stamp)
    store.merge_submissions(COURSE, "700010", SUBMISSIONS, root=root,
                            attempted_at=stamp, replace=True)
    for pass_name in ("full", "roster"):
        store.record_pass(COURSE, pass_name, ok=True, attempted_at=stamp, root=root)


def _raise_if_live(monkeypatch):
    def _explode(*_args, **_kwargs):
        raise AssertionError("live Canvas read attempted")
    for name in ("course_students", "course_assignments", "course_submissions",
                 "assignment", "assignment_submissions"):
        monkeypatch.setattr(gradebook_queries, name, _explode)


# --- queries provider ----------------------------------------------------------

def test_queries_interface_serves_canvas_shaped_rows(tmp_path):
    _populate(str(tmp_path))
    students, err = queries.course_students(COURSE, root=str(tmp_path))
    assert err is None and {s["id"] for s in students} == {"900001", "900002"}
    assignments, err = queries.course_assignments(COURSE, root=str(tmp_path))
    assert err is None and assignments[0]["name"] == "Essay 1"
    subs, err = queries.course_submissions(COURSE, root=str(tmp_path))
    assert err is None and len(subs) == 2
    rows, err = queries.assignment_submissions(COURSE, "700010", root=str(tmp_path))
    assert err is None and rows[0]["user_id"] == "900001"


def test_queries_report_unavailable_for_unknown_course(tmp_path):
    data, err = queries.course_students("999", root=str(tmp_path))
    assert data is None and err == queries.MIRROR_UNAVAILABLE


def test_freshness_gates_on_serve_max_age(tmp_path):
    _populate(str(tmp_path), fresh=False)
    assert queries.data_freshness(COURSE, root=str(tmp_path)) == ""
    _populate(str(tmp_path), fresh=True)
    assert queries.data_freshness(COURSE, root=str(tmp_path)) != ""


def test_course_students_does_not_serve_a_stale_roster(tmp_path):
    """course_students must gate on the serve-freshness threshold this
    module's own docstring promises (design law #4): a readable but old
    roster file must not be served as current just because it still parses.
    Callers fall back to live Canvas on the resulting MIRROR_UNAVAILABLE."""
    _populate(str(tmp_path), fresh=False)
    students, err = queries.course_students(COURSE, root=str(tmp_path))
    assert students is None and err == queries.MIRROR_UNAVAILABLE


def test_course_submissions_filters_orphan_files_not_in_index(tmp_path):
    """1.0beta slice 01b, locked design item 1: a submission file on disk for
    an assignment id the committed index doesn't have must never surface in
    the aggregate read, even though nothing has pruned the file yet."""
    _populate(str(tmp_path))
    store.merge_submissions(COURSE, ORPHAN_ASSIGNMENT_ID, ORPHAN_ROW, root=str(tmp_path))
    assert store.read_submissions(COURSE, ORPHAN_ASSIGNMENT_ID, root=str(tmp_path)) is not None
    rows, err = queries.course_submissions(COURSE, root=str(tmp_path))
    assert err is None
    assert {row["assignment_id"] for row in rows} == {"700010"}


def test_assignment_submissions_filters_orphan_not_in_index(tmp_path):
    _populate(str(tmp_path))
    store.merge_submissions(COURSE, ORPHAN_ASSIGNMENT_ID, ORPHAN_ROW, root=str(tmp_path))
    data, err = queries.assignment_submissions(COURSE, ORPHAN_ASSIGNMENT_ID, root=str(tmp_path))
    assert data is None and err == queries.MIRROR_UNAVAILABLE
    # A missing/corrupt index does not gate the read — last-good rules unchanged.
    os.remove(store.assignments_path(COURSE, str(tmp_path)))
    data, err = queries.assignment_submissions(COURSE, ORPHAN_ASSIGNMENT_ID, root=str(tmp_path))
    assert err is None and len(data) == 1


def test_snapshot_queries_requires_fresh_and_complete_mirror(tmp_path):
    namespace, synced = queries.snapshot_queries(COURSE, root=str(tmp_path))
    assert namespace is None and synced == ""
    _populate(str(tmp_path))
    namespace, synced = queries.snapshot_queries(COURSE, root=str(tmp_path))
    assert namespace is not None and synced != ""


def test_snapshot_queries_refuses_a_stale_roster_behind_fresh_submissions(tmp_path):
    """data_freshness reads the submissions envelope, which a delta pass keeps
    current, while only a full or roster pass rewrites the roster. The roster
    can therefore age past the serve window on its own, and this namespace has
    to catch that here: load_snapshot has no live fallback once it commits,
    so a roster refused mid-snapshot would surface as a hard error."""
    root = str(tmp_path)
    _populate(root)
    assert queries.snapshot_queries(COURSE, root=root)[0] is not None
    store.write_roster(COURSE, USERS, SECTIONS, root=root,
                       attempted_at="2026-01-01T00:00:00Z")
    namespace, synced = queries.snapshot_queries(COURSE, root=root)
    assert namespace is None and synced == ""


# --- mirror-first shared snapshot loader --------------------------------------------

def test_load_snapshot_prefers_fresh_mirror(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    _populate(str(tmp_path))
    _raise_if_live(monkeypatch)  # would explode if the live path were taken
    snapshot, error = gradebook_snapshot.load_snapshot(COURSE)
    assert error is None
    assert snapshot["source"] == "mirror"
    assert snapshot["synced_at"] != ""
    assert snapshot["student_count"] == 2
    assert snapshot["total_missing"] == 1
    assert snapshot["assignments"][0]["name"] == "Essay 1"


def test_load_snapshot_falls_back_to_live_when_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    _populate(str(tmp_path), fresh=False)
    monkeypatch.setattr(gradebook_queries, "course_students", lambda cid: ([], None))
    monkeypatch.setattr(gradebook_queries, "course_assignments", lambda cid: ([], None))
    monkeypatch.setattr(gradebook_queries, "course_submissions", lambda cid: ([], None))
    snapshot, error = gradebook_snapshot.load_snapshot(COURSE)
    assert error is None
    assert snapshot["source"] == "canvas"
    assert snapshot["synced_at"] == ""


def test_load_snapshot_falls_back_to_live_when_only_the_roster_is_stale(monkeypatch, tmp_path):
    """The teacher gets live Canvas, never a hard error, when submissions are
    current but the roster is not. Reachable whenever roster passes keep
    failing while deltas keep succeeding, which is exactly what happens when
    Canvas is answering with a roster the wipe guard refuses to commit."""
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    _populate(str(tmp_path))
    store.write_roster(COURSE, USERS, SECTIONS, root=str(tmp_path),
                       attempted_at="2026-01-01T00:00:00Z")
    monkeypatch.setattr(gradebook_queries, "course_students", lambda cid: ([], None))
    monkeypatch.setattr(gradebook_queries, "course_assignments", lambda cid: ([], None))
    monkeypatch.setattr(gradebook_queries, "course_submissions", lambda cid: ([], None))
    snapshot, error = gradebook_snapshot.load_snapshot(COURSE)
    assert error is None
    assert snapshot["source"] == "canvas"
    assert snapshot["synced_at"] == ""


def test_load_snapshot_explicit_queries_override_skips_mirror(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    _populate(str(tmp_path))
    from types import SimpleNamespace
    override = SimpleNamespace(course_students=lambda cid: ([], None),
                               course_assignments=lambda cid: ([], None),
                               course_submissions=lambda cid: ([], None))
    snapshot, _ = gradebook_snapshot.load_snapshot(COURSE, queries=override)
    assert snapshot["source"] == "canvas"
    assert snapshot["student_count"] == 0


# --- MCP tools: mirror paths ------------------------------------------------------

def _mcp_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": COURSE}])
    # No _vault_factory override: the mirror scrubs bodies at rest with the
    # workspace identity vault, so the tool must read through that same vault.


def test_mcp_get_roster_serves_from_mirror_without_canvas(monkeypatch, tmp_path):
    _mcp_setup(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = tools.get_roster(COURSE)
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert result["synced_at"] != ""
    assert len(result["roster"]["rows"]) == 2
    dumped = json.dumps(result)
    for leak in ("Learner One", "900001", "SIS-900001"):
        assert leak not in dumped


def test_mcp_get_submissions_serves_from_mirror_scrubbed(monkeypatch, tmp_path):
    _mcp_setup(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = tools.get_submissions(COURSE, "700010")
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert result["assignment"]["title"] == "Essay 1"
    rows = [dict(zip(result["submissions"]["columns"], row))
            for row in result["submissions"]["rows"]]
    graded = next(r for r in rows if r["workflow_state"] == "graded")
    # Real name and nickname scrubbed to the same pseudonym.
    assert graded["pseudonym"] in graded["text"]
    dumped = json.dumps(result)
    for leak in ("Learner One", "Lee", "900001"):
        assert leak not in dumped


def test_mcp_get_gradebook_snapshot_serves_from_mirror(monkeypatch, tmp_path):
    _mcp_setup(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    result = tools.get_gradebook_snapshot(COURSE)
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert result["synced_at"] != ""
    assert len(result["students"]["rows"]) == 2


def _pin_mcp_freshness(monkeypatch, now):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now.replace(tzinfo=None)
            return now.astimezone(tz)

    monkeypatch.setattr(freshness_policy, "datetime", FixedDateTime)
    monkeypatch.setattr(store, "datetime", FixedDateTime)
    monkeypatch.setattr(grading_policy, "load_no_school_dates", lambda root=None: [])
    monkeypatch.setattr(tools.mirror_queries, "_serve_max_age_hours", lambda: 6.0)


def _assert_mcp_freshness_results(monkeypatch, tmp_path, *, age_minutes, within_policy):
    _mcp_setup(monkeypatch, tmp_path)
    now = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)
    _pin_mcp_freshness(monkeypatch, now)
    stamp = (now - timedelta(minutes=age_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _populate(str(tmp_path), stamp=stamp)

    roster = tools.get_roster(COURSE)
    submissions = tools.get_submissions(COURSE, "700010")
    for result in (roster, submissions):
        assert result["freshness"]["school_hours"] is False
        assert result["freshness"]["policy_window_minutes"] == 600
        assert result["freshness"]["age_minutes"] == age_minutes
        assert result["freshness"]["within_policy"] is within_policy
    return roster, submissions


def test_mcp_r3_past_policy_window_asks_teacher_without_student_rows(monkeypatch, tmp_path):
    roster, submissions = _assert_mcp_freshness_results(
        monkeypatch, tmp_path, age_minutes=601, within_policy=False)
    for result in (roster, submissions):
        assert result["ok"] is False
        assert result["attention"]["action"] == "ask_teacher_confirmation"
        assert "roster" not in result
        assert "submissions" not in result


def test_mcp_r3_within_policy_serves_past_mirror_cutoff(monkeypatch, tmp_path):
    roster, submissions = _assert_mcp_freshness_results(
        monkeypatch, tmp_path, age_minutes=480, within_policy=True)
    assert 480 > tools.mirror_queries._serve_max_age_hours() * 60
    assert roster["ok"] is True
    assert submissions["ok"] is True
    assert roster["freshness"]["age_minutes"] == 480
    assert submissions["freshness"]["age_minutes"] == 480
    assert roster["roster"]["rows"]
    assert submissions["submissions"]["rows"]


def test_mcp_seamed_tests_bypass_the_mirror(monkeypatch, tmp_path):
    _mcp_setup(monkeypatch, tmp_path)
    _populate(str(tmp_path))
    from api.mcp_server import pseudonym
    monkeypatch.setattr(pseudonym, "_fetch_students",
                        lambda course_id: (USERS, None))
    assert tools._mirror_roster_doc(COURSE) is None  # seam guard wins
