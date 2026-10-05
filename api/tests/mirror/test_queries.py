"""Offline tests for mirror-backed reads: the queries provider, the
mirror-first snapshot loader, and the MCP tools' mirror paths.

Freshness uses real store timestamps (now_iso) so "fresh" means fresh; stale
cases pin old attempted_at stamps.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

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


# --- MCP tools: evidence read path ------------------------------------------------
#
# The four student-data MCP reads now serve ONLY from the pseudonymized evidence
# index (see api/tests/mcp_server/test_tools.py::evidence_mirror). The retired
# typed-mirror read path (store.write_roster/assignments/submissions feeding the
# tools directly) is no longer a read authority, so its MCP-serving tests were
# removed with the functional read path. The `queries` provider tests above still
# cover the typed-mirror projection used by internal consumers.
