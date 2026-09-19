"""Offline tests for the CanvasMirror sync passes.

Canvas is a fake ``canvas_get_all`` closure dispatching on path + params —
the sync module never touches the real client (it is injected, catalog
pattern). Clock is injected via ``now=``.
"""
from __future__ import annotations

import json

from api import course_catalog
from api.mirror import read_service, store, sync

COURSE = "111"
NOW = "2026-07-16T12:00:00Z"
NOW_MINUS_OVERLAP = "2026-07-16T11:50:00Z"

USERS = [
    {"id": 900001, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900001",
     "enrollments": [{"course_section_id": 800001}]},
]
SECTIONS = [{"id": 800001, "name": "Period 1"}]
ASSIGNMENTS = [
    {"id": 700010, "name": "Essay 1", "due_at": "2026-07-01T23:59:00Z",
     "points_possible": 10, "published": True, "html_url": "u1",
     "submission_types": ["online_text_entry"], "updated_at": ""},
    {"id": 700020, "name": "Essay 2", "due_at": "2026-07-08T23:59:00Z",
     "points_possible": 10, "published": True, "html_url": "u2",
     "submission_types": ["online_text_entry"], "updated_at": ""},
]


def _sub(assignment_id, attempt=1, body="Draft.", **overrides):
    row = {
        "assignment_id": assignment_id, "user_id": 900001,
        "workflow_state": "submitted",
        "submitted_at": f"2026-07-0{attempt}T10:00:00Z", "graded_at": None,
        "score": None, "grade": None, "late": False, "missing": False,
        "excused": False, "attempt": attempt,
        "grade_matches_current_submission": True,
        "submission_type": "online_text_entry", "body": body,
    }
    row.update(overrides)
    return row


class FakeCanvas:
    """Dispatches on URL suffix; records every call for param assertions."""

    def __init__(self, *, assignments=None, users=None, sections=None,
                 submissions=None, delta_submitted=None, delta_graded=None,
                 errors=None):
        self.assignments = assignments if assignments is not None else list(ASSIGNMENTS)
        self.users = users if users is not None else list(USERS)
        self.sections = sections if sections is not None else list(SECTIONS)
        self.submissions = submissions or []
        self.delta_submitted = delta_submitted or []
        self.delta_graded = delta_graded or []
        self.errors = errors or {}
        self.calls = []

    def __call__(self, path, params=None, timeout=30):
        self.calls.append((path, dict(params or {})))
        if path.endswith("/assignments"):
            return (None, self.errors["assignments"]) if "assignments" in self.errors \
                else (self.assignments, None)
        if path.endswith("/users"):
            return (None, self.errors["users"]) if "users" in self.errors \
                else (self.users, None)
        if path.endswith("/sections"):
            return (None, self.errors["sections"]) if "sections" in self.errors \
                else (self.sections, None)
        if path.endswith("/students/submissions"):
            if "submissions" in self.errors:
                return None, self.errors["submissions"]
            if "submitted_since" in (params or {}):
                return self.delta_submitted, None
            if "graded_since" in (params or {}):
                return self.delta_graded, None
            return self.submissions, None
        raise AssertionError(f"unexpected path {path}")

    def complete(self, path, params=None, timeout=30):
        """Explicit all-pages receipt used for assignment and roster membership."""
        rows, error = self(path, params, timeout)
        return rows, error, error is None


# --- full pass ---------------------------------------------------------------

def test_full_pass_writes_everything_and_sets_watermarks(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010), _sub(700020)])
    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    assert result["ok"] is True
    assert result["assignments"] == 2
    assert store.read_roster(COURSE, root=str(tmp_path))["students"]["900001"]["name"] == "Learner One"
    assert set(store.read_assignments(COURSE, root=str(tmp_path))["assignments"]) == {"700010", "700020"}
    assert store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    state = store.read_sync(COURSE, root=str(tmp_path))
    assert state["passes"]["full"]["state"] == "current"
    assert state["passes"]["roster"]["state"] == "current"
    assert state["watermarks"] == {"submitted_since": NOW_MINUS_OVERLAP,
                                   "graded_since": NOW_MINUS_OVERLAP}


def test_refresh_persists_operation_revision_and_usable_snapshot(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010)])
    result = sync.refresh(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete,
                          root=str(tmp_path), now=NOW)
    assert result["status"] == "synced"
    assert result["usable"] is True
    assert result["mirror_revision"] == 1
    assert result["snapshot_id"] == f"{COURSE}:1"
    status = sync.refresh_status(COURSE, root=str(tmp_path))
    assert status["operation_id"] == result["operation_id"]
    assert status["snapshot_id"] == result["snapshot_id"]
    assignments = read_service.private_assignments(COURSE, root=str(tmp_path))
    submissions = read_service.private_submissions(COURSE, root=str(tmp_path))
    assert assignments["mirror_revision"] == submissions["mirror_revision"] == 1
    assert assignments["snapshot_id"] == submissions["snapshot_id"] == result["snapshot_id"]
    assert assignments["refresh_state"] == submissions["refresh_state"] == "synced"


def test_refresh_lifecycle_persists_syncing_before_terminal_state(tmp_path):
    store.begin_refresh(COURSE, operation_id="op-1", requested_at=NOW,
                        root=str(tmp_path))
    syncing = sync.refresh_status(COURSE, root=str(tmp_path))
    assert syncing["status"] == "syncing"
    assert syncing["usable"] is False
    assert syncing["operation_id"] == "op-1"


def test_refresh_failure_is_terminal_and_retry_converges(tmp_path):
    failing = FakeCanvas(errors={"submissions": "HTTP 503: upstream"})
    failed = sync.refresh(COURSE, canvas_get_all=failing,
                          canvas_get_all_complete=failing.complete,
                          root=str(tmp_path), now=NOW)
    assert failed["status"] == "failed"
    assert failed["usable"] is False
    assert store.read_refresh(COURSE, root=str(tmp_path))["revision"] == 0

    succeeding = FakeCanvas(submissions=[_sub(700010)])
    retried = sync.refresh(COURSE, canvas_get_all=succeeding,
                           canvas_get_all_complete=succeeding.complete,
                           root=str(tmp_path), now="2026-07-16T13:00:00Z")
    repeated = sync.refresh(COURSE, canvas_get_all=FakeCanvas(errors={"assignments": "must not run"}),
                            canvas_get_all_complete=lambda *args, **kwargs: (_ for _ in ()).throw(
                                AssertionError("successful refresh should be reusable")),
                            root=str(tmp_path), now="2026-07-16T14:00:00Z")
    assert retried["status"] == repeated["status"] == "synced"
    assert retried["operation_id"] == repeated["operation_id"]
    assert retried["snapshot_id"] == repeated["snapshot_id"]
    assert retried["mirror_revision"] == repeated["mirror_revision"] == 1


def test_full_pass_requests_submission_comments_delta_does_not(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010)])
    sync.full_pass(COURSE, canvas_get_all=canvas,
                   canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    full_submissions_call = next(
        params for path, params in canvas.calls
        if path.endswith("/students/submissions") and "submitted_since" not in params
        and "graded_since" not in params)
    assert full_submissions_call["include[]"] == ["submission_history", "submission_comments"]

    delta_canvas = FakeCanvas(delta_submitted=[_sub(700010, attempt=2)])
    sync.delta_pass(COURSE, canvas_get_all=delta_canvas,
                    canvas_get_all_complete=delta_canvas.complete, root=str(tmp_path),
                    now="2026-07-16T13:00:00Z")
    submitted_call = next(p for path, p in delta_canvas.calls if "submitted_since" in p)
    graded_call = next(p for path, p in delta_canvas.calls if "graded_since" in p)
    assert "submission_comments" not in submitted_call.get("include[]", [])
    assert "include[]" not in graded_call


def test_full_pass_can_skip_submission_comments_for_scoring(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010)])
    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete,
                            root=str(tmp_path), now=NOW, with_comments=False)

    assert result["ok"] is True
    submissions_call = next(
        params for path, params in canvas.calls
        if path.endswith("/students/submissions")
        and "submitted_since" not in params and "graded_since" not in params)
    assert submissions_call["include[]"] == ["submission_history"]


def test_full_pass_captures_submission_comments(tmp_path):
    canvas = FakeCanvas(submissions=[
        _sub(700010, submission_comments=[
            {"author_id": 900099, "comment": "Nice work.",
             "created_at": "2026-07-01T11:00:00Z", "author_name": "Teacher T"},
        ]),
    ])
    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    assert result["ok"] is True
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert entry["current"]["submission_comments"] == [
        {"author_id": "900099", "author_role": "", "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ]
    assert "author_name" not in entry["current"]["submission_comments"][0]


def test_delta_after_full_does_not_erase_stored_comments(tmp_path):
    canvas = FakeCanvas(submissions=[
        _sub(700010, submission_comments=[
            {"author_id": 900099, "comment": "Nice work.",
             "created_at": "2026-07-01T11:00:00Z"},
        ]),
    ])
    sync.full_pass(COURSE, canvas_get_all=canvas,
                   canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    # Delta fetches (lean, no submission_comments include) never carry comments.
    delta_canvas = FakeCanvas(delta_submitted=[_sub(700010, attempt=2, body="Second draft.")])
    result = sync.delta_pass(COURSE, canvas_get_all=delta_canvas,
                             canvas_get_all_complete=delta_canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert entry["current"]["submission_comments"] == [
        {"author_id": "900099", "author_role": "", "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ]
    assert entry["current"]["attempt"] == 2


def test_mcp_get_submissions_never_leaks_comment_text(monkeypatch, tmp_path):
    from api.feedback_vault import Vault
    from api.mcp_server import tools
    from api.platform_services import workspace

    canvas = FakeCanvas(submissions=[
        _sub(700010, submission_comments=[
            {"author_id": 900099, "comment": "SECRET-FEEDBACK-TEXT",
             "created_at": "2026-07-01T11:00:00Z"},
        ]),
    ])
    # Real now_iso() (not the fixed NOW fixture) so the mirror-serve
    # freshness gate — which compares against wall-clock time — passes.
    sync.full_pass(COURSE, canvas_get_all=canvas,
                   canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=store.now_iso())

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": COURSE}])
    monkeypatch.setattr(tools, "_vault_factory",
                        lambda: Vault(str(tmp_path / "vault.json")))
    result = tools.get_submissions(COURSE, "700010")
    assert result["ok"] is True
    dumped = json.dumps(result)
    assert "SECRET-FEEDBACK-TEXT" not in dumped
    assert "submission_comments" not in dumped


def test_full_pass_prunes_deleted_assignments_and_dropped_students(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010), _sub(700020)])
    sync.full_pass(COURSE, canvas_get_all=canvas,
                   canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    # Canvas now says: assignment 700020 gone; 900001 no longer has a row on 700010.
    later = FakeCanvas(assignments=[ASSIGNMENTS[0]], submissions=[
        _sub(700010, user_id=900002),
    ])
    result = sync.full_pass(COURSE, canvas_get_all=later,
                            canvas_get_all_complete=later.complete, root=str(tmp_path),
                            now="2026-07-17T03:00:00Z")
    assert result["pruned_assignments"] == ["700020"]
    assert store.read_submissions(COURSE, "700020", root=str(tmp_path)) is None
    document = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    assert set(document["submissions"]) == {"900002"}


def test_full_pass_fetch_error_records_failure_and_writes_nothing(tmp_path):
    canvas = FakeCanvas(errors={"submissions": "HTTP 503: upstream"})
    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    assert result["ok"] is False
    state = store.read_sync(COURSE, root=str(tmp_path))
    assert state["passes"]["full"]["state"] == "unavailable"
    assert state["watermarks"]["submitted_since"] == ""
    assert store.read_assignments(COURSE, root=str(tmp_path)) is None


# --- delta pass ----------------------------------------------------------------

def _backfilled(tmp_path, submissions=None):
    canvas = FakeCanvas(submissions=submissions if submissions is not None
                        else [_sub(700010)])
    assert sync.full_pass(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                          now=NOW)["ok"] is True


def test_delta_pass_without_watermarks_falls_back_to_full(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010)])
    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    assert result["ok"] is True
    assert "submission_rows" in result  # full-pass result shape
    assert store.read_sync(COURSE, root=str(tmp_path))["passes"]["full"]["state"] == "current"


def test_delta_pass_sends_watermarks_and_merges_new_attempt(tmp_path):
    _backfilled(tmp_path)
    canvas = FakeCanvas(delta_submitted=[
        _sub(700010, attempt=2, body="Second draft.", submission_history=[
            {"attempt": 2, "submitted_at": "2026-07-02T10:00:00Z",
             "submission_type": "online_text_entry", "body": "Second draft."},
        ]),
    ])
    later = "2026-07-16T13:00:00Z"
    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=later)
    assert result["ok"] is True
    assert result["touched_assignments"] == ["700010"]

    submitted_call = next(p for path, p in canvas.calls if "submitted_since" in p)
    graded_call = next(p for path, p in canvas.calls if "graded_since" in p)
    assert submitted_call["submitted_since"] == NOW_MINUS_OVERLAP
    assert submitted_call["include[]"] == ["submission_history"]
    assert graded_call["graded_since"] == NOW_MINUS_OVERLAP
    assert "include[]" not in graded_call

    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert set(entry["attempts"]) == {"1", "2"}  # append-only across the delta
    assert entry["current"]["attempt"] == 2
    state = store.read_sync(COURSE, root=str(tmp_path))
    assert state["watermarks"]["submitted_since"] == "2026-07-16T12:50:00Z"


def test_delta_pass_is_idempotent_on_replay(tmp_path):
    _backfilled(tmp_path)
    delta = [_sub(700010, attempt=2, body="Second draft.")]
    for stamp in ("2026-07-16T13:00:00Z", "2026-07-16T13:00:00Z"):
        canvas = FakeCanvas(delta_submitted=list(delta))
        sync.delta_pass(COURSE, canvas_get_all=canvas,
                        canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=stamp)
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert set(entry["attempts"]) == {"1", "2"}
    assert entry["current"]["attempt"] == 2


def test_delta_pass_graded_only_change_updates_current(tmp_path):
    _backfilled(tmp_path)
    canvas = FakeCanvas(delta_graded=[
        _sub(700010, workflow_state="graded", score=9, grade="9",
             graded_at="2026-07-16T12:30:00Z"),
    ])
    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    current = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]["current"]
    assert current["score"] == 9
    assert current["workflow_state"] == "graded"


def test_delta_pass_error_keeps_watermarks(tmp_path):
    _backfilled(tmp_path)
    canvas = FakeCanvas(errors={"submissions": "HTTP 503: upstream"})
    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is False
    state = store.read_sync(COURSE, root=str(tmp_path))
    assert state["passes"]["delta"]["state"] == "unavailable"  # never succeeded
    assert state["watermarks"]["submitted_since"] == NOW_MINUS_OVERLAP  # unchanged


# --- no-op delta tick must not rewrite the assignments mirror file ---------------

def test_delta_pass_no_op_does_not_rewrite_assignments_file(tmp_path):
    """The 15-minute incremental sync must not rewrite the assignments mirror
    file on a tick where the assignment collection is unchanged -- only
    envelope timestamps would differ, and the file lives in a
    OneDrive-synced workspace where that write is expensive busywork."""
    _backfilled(tmp_path)
    before = store.read_assignments(COURSE, root=str(tmp_path))

    canvas = FakeCanvas()  # identical assignments/no submission deltas
    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    assert result["assignment_changes"] == {
        "added": [], "changed": [], "removed": [],
        "large_shrink": 0, "orphans_filtered": [], "orphans_pruned": [],
    }
    after_first = store.read_assignments(COURSE, root=str(tmp_path))
    assert after_first == before  # byte-identical, including last_success_at

    canvas2 = FakeCanvas()
    result2 = sync.delta_pass(COURSE, canvas_get_all=canvas2,
                              canvas_get_all_complete=canvas2.complete, root=str(tmp_path),
                              now="2026-07-16T14:00:00Z")
    assert result2["ok"] is True
    after_second = store.read_assignments(COURSE, root=str(tmp_path))
    assert after_second == after_first  # a second no-op tick still leaves it untouched


def test_delta_pass_with_assignment_change_rewrites_and_advances_freshness(tmp_path):
    """The counterpart to the no-op test above: an actual assignment change
    must still commit a rewrite and advance the file's own envelope."""
    _backfilled(tmp_path)
    before = store.read_assignments(COURSE, root=str(tmp_path))

    changed_assignments = [dict(ASSIGNMENTS[0], name="Essay 1 (revised)"), ASSIGNMENTS[1]]
    canvas = FakeCanvas(assignments=changed_assignments)
    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    assert result["assignment_changes"]["changed"] == ["700010"]

    after = store.read_assignments(COURSE, root=str(tmp_path))
    assert after != before
    assert after["assignments"]["700010"]["name"] == "Essay 1 (revised)"
    assert after["last_success_at"] == "2026-07-16T13:00:00Z"


# --- 1.0beta slice 01b: assignment deletion membership + safe pruning ------------

EXTRA_ASSIGNMENTS = ASSIGNMENTS + [
    {"id": 700030, "name": "Essay 3", "due_at": "", "points_possible": 10,
     "published": True, "html_url": "u3", "submission_types": [], "updated_at": ""},
    {"id": 700040, "name": "Essay 4", "due_at": "", "points_possible": 10,
     "published": True, "html_url": "u4", "submission_types": [], "updated_at": ""},
]


def test_delta_removing_assignments_excludes_them_from_queries_before_any_prune(tmp_path):
    """A >50% shrink defers pruning (the guard), but membership filtering
    already hides the departed assignments' submissions from aggregate
    reads immediately — before any file has been removed from disk."""
    from api.mirror import queries

    canvas = FakeCanvas(assignments=EXTRA_ASSIGNMENTS, submissions=[
        _sub(700010), _sub(700020), _sub(700030), _sub(700040),
    ])
    assert sync.full_pass(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                          now=NOW)["ok"] is True

    later = FakeCanvas(assignments=[ASSIGNMENTS[0]])  # 4 -> 1: a 75% shrink
    result = sync.delta_pass(COURSE, canvas_get_all=later,
                             canvas_get_all_complete=later.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    assert result["assignment_changes"]["removed"] == ["700020", "700030", "700040"]
    assert result["assignment_changes"]["large_shrink"] == 3
    assert result["assignment_changes"]["orphans_pruned"] == []  # deferred

    for departed in ("700020", "700030", "700040"):
        assert store.read_submissions(COURSE, departed, root=str(tmp_path)) is not None

    rows, err = queries.course_submissions(COURSE, root=str(tmp_path))
    assert err is None
    assert {row["assignment_id"] for row in rows} == {"700010"}
    data, err = queries.assignment_submissions(COURSE, "700020", root=str(tmp_path))
    assert data is None and err == queries.MIRROR_UNAVAILABLE


def test_delta_prune_removes_exactly_departed_ids(tmp_path):
    canvas = FakeCanvas(assignments=EXTRA_ASSIGNMENTS, submissions=[
        _sub(700010), _sub(700020), _sub(700030), _sub(700040),
    ])
    assert sync.full_pass(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                          now=NOW)["ok"] is True

    # Drop one of four (25% shrink) — below the guard, so this pass prunes.
    later = FakeCanvas(assignments=EXTRA_ASSIGNMENTS[:3])  # 700040 removed
    result = sync.delta_pass(COURSE, canvas_get_all=later,
                             canvas_get_all_complete=later.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    assert result["assignment_changes"]["removed"] == ["700040"]
    assert result["assignment_changes"]["large_shrink"] == 0
    assert result["assignment_changes"]["orphans_pruned"] == ["700040"]
    assert store.read_submissions(COURSE, "700040", root=str(tmp_path)) is None
    for kept in ("700010", "700020", "700030"):
        assert store.read_submissions(COURSE, kept, root=str(tmp_path)) is not None


def test_empty_complete_collection_commits_empty_index(tmp_path):
    from api.mirror import queries

    canvas = FakeCanvas(submissions=[_sub(700010), _sub(700020)])
    assert sync.full_pass(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                          now=NOW)["ok"] is True

    empty_canvas = FakeCanvas(assignments=[])
    result = sync.delta_pass(COURSE, canvas_get_all=empty_canvas,
                             canvas_get_all_complete=empty_canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    assert store.read_assignments(COURSE, root=str(tmp_path))["assignments"] == {}
    assert result["assignment_changes"]["removed"] == ["700010", "700020"]
    assert result["assignment_changes"]["large_shrink"] == 2
    assert result["assignment_changes"]["orphans_pruned"] == []

    assignments, err = queries.course_assignments(COURSE, root=str(tmp_path))
    assert err is None and assignments == []
    rows, err = queries.course_submissions(COURSE, root=str(tmp_path))
    assert err is None and rows == []


def test_large_shrink_commits_index_defers_prune_and_records_diagnostic(tmp_path):
    canvas = FakeCanvas(assignments=EXTRA_ASSIGNMENTS, submissions=[
        _sub(700010), _sub(700020), _sub(700030), _sub(700040),
    ])
    assert sync.full_pass(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                          now=NOW)["ok"] is True

    later = FakeCanvas(assignments=[ASSIGNMENTS[0]])  # 4 -> 1: a 75% shrink
    result = sync.delta_pass(COURSE, canvas_get_all=later,
                             canvas_get_all_complete=later.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    assert set(store.read_assignments(COURSE, root=str(tmp_path))["assignments"]) == {"700010"}
    assert result["assignment_changes"]["large_shrink"] == 3
    assert result["assignment_changes"]["orphans_pruned"] == []
    for departed in ("700020", "700030", "700040"):
        assert store.read_submissions(COURSE, departed, root=str(tmp_path)) is not None

    # The next delta compares against the now-smaller committed index (1), so
    # an unchanged small collection is no longer a shrink and prunes cleanly —
    # deferral is bounded to one pass, not indefinite.
    again = FakeCanvas(assignments=[ASSIGNMENTS[0]])
    result2 = sync.delta_pass(COURSE, canvas_get_all=again,
                              canvas_get_all_complete=again.complete, root=str(tmp_path),
                              now="2026-07-16T14:00:00Z")
    assert result2["ok"] is True
    assert result2["assignment_changes"]["large_shrink"] == 0
    assert sorted(result2["assignment_changes"]["orphans_pruned"]) == [
        "700020", "700030", "700040"]
    for departed in ("700020", "700030", "700040"):
        assert store.read_submissions(COURSE, departed, root=str(tmp_path)) is None


def test_delta_fetch_error_changes_nothing(tmp_path):
    _backfilled(tmp_path)
    before = store.read_assignments(COURSE, root=str(tmp_path))
    canvas = FakeCanvas(errors={"submissions": "HTTP 503: upstream"})
    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is False
    assert "assignment_changes" not in result
    assert store.read_assignments(COURSE, root=str(tmp_path)) == before
    state = store.read_sync(COURSE, root=str(tmp_path))
    assert state["watermarks"]["submitted_since"] == NOW_MINUS_OVERLAP  # unchanged


def test_incomplete_assignment_receipt_preserves_last_good_mirror_and_skips_new_quizzes(
        monkeypatch, tmp_path):
    _backfilled(tmp_path, submissions=[_sub(700010), _sub(700020)])
    store.write_new_quiz_capability(
        COURSE, capability="restricted", last_probe_at=NOW,
        retry_after="2099-01-01T00:00:00Z", evidence_category="forbidden",
        consecutive_failures=3, root=str(tmp_path))
    before_assignments = store.read_assignments(COURSE, root=str(tmp_path))
    before_state = store.read_sync(COURSE, root=str(tmp_path))
    before_capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    monkeypatch.setattr(sync.new_quizzes, "sync_metadata",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("an incomplete assignment receipt must stop first")))

    result = sync.delta_pass(
        COURSE, canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=lambda *args, **kwargs: ([], None, False),
        root=str(tmp_path), now="2026-07-16T13:00:00Z")

    assert result == {"ok": False, "error": "pagination_incomplete"}
    assert store.read_assignments(COURSE, root=str(tmp_path)) == before_assignments
    assert store.read_sync(COURSE, root=str(tmp_path))["watermarks"] == before_state["watermarks"]
    assert store.read_sync(COURSE, root=str(tmp_path))["passes"]["delta"] == {
        "state": "unavailable", "last_success_at": "",
        "last_attempt_at": "2026-07-16T13:00:00Z", "error_code": "pagination_incomplete",
    }
    assert store.read_submissions(COURSE, "700010", root=str(tmp_path)) is not None
    assert store.read_submissions(COURSE, "700020", root=str(tmp_path)) is not None
    assert store.read_new_quiz_capability(COURSE, root=str(tmp_path)) == before_capability


def test_invalid_duplicate_assignment_receipt_preserves_last_good_mirror_and_skips_new_quizzes(
        monkeypatch, tmp_path):
    _backfilled(tmp_path, submissions=[_sub(700010), _sub(700020)])
    store.write_new_quiz_capability(
        COURSE, capability="restricted", last_probe_at=NOW,
        retry_after="2099-01-01T00:00:00Z", evidence_category="forbidden",
        consecutive_failures=3, root=str(tmp_path))
    before_assignments = store.read_assignments(COURSE, root=str(tmp_path))
    before_state = store.read_sync(COURSE, root=str(tmp_path))
    before_capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    monkeypatch.setattr(sync.new_quizzes, "sync_metadata",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("an invalid assignment receipt must stop first")))

    duplicate_rows = [ASSIGNMENTS[0], dict(ASSIGNMENTS[0])]
    result = sync.delta_pass(
        COURSE, canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=lambda *args, **kwargs: (duplicate_rows, None, True),
        root=str(tmp_path), now="2026-07-16T13:00:00Z")

    assert result == {"ok": False, "error": "invalid_response"}
    assert store.read_assignments(COURSE, root=str(tmp_path)) == before_assignments
    assert store.read_sync(COURSE, root=str(tmp_path))["watermarks"] == before_state["watermarks"]
    assert store.read_sync(COURSE, root=str(tmp_path))["passes"]["delta"] == {
        "state": "unavailable", "last_success_at": "",
        "last_attempt_at": "2026-07-16T13:00:00Z", "error_code": "invalid_response",
    }
    assert store.read_submissions(COURSE, "700010", root=str(tmp_path)) is not None
    assert store.read_submissions(COURSE, "700020", root=str(tmp_path)) is not None
    assert store.read_new_quiz_capability(COURSE, root=str(tmp_path)) == before_capability


# --- coordinated Catalog receipt (1.0beta 02c) ------------------------------------

def _seed_catalog_with_modules_and_groups(tmp_path, *, course_id=COURSE, course_name="Fictional Course"):
    """Give ``course_id`` a previous Catalog document with populated modules
    and assignment-groups scopes, so coordination tests can prove those
    scopes pass through unchanged."""
    def refuses_legacy_get(path, params=None, timeout=30):
        raise AssertionError(f"unexpected canvas_get_all call: {path}")

    def complete(path, params=None, timeout=30):
        if path.endswith("/assignments"):
            return [{"id": "900", "name": "Old assignment", "published": True}], None, True
        if path.endswith("/modules"):
            return [{"id": "10", "name": "Module 1", "position": 1, "items": []}], None, True
        if path.endswith("/assignment_groups"):
            return [{"id": "44", "name": "Projects", "position": 1, "group_weight": 25}], None, True
        if path.endswith("/pages"):
            return [], None, True
        raise AssertionError(f"unexpected canvas_get_all_complete call: {path}")

    return course_catalog.refresh_catalog(
        course_id, course_name, canvas_get_all=refuses_legacy_get,
        canvas_get_all_complete=complete, root=str(tmp_path), attempted_at=NOW,
    )["catalog"]


def test_full_pass_forwards_valid_receipt_to_catalog_leaving_modules_groups_unchanged(tmp_path):
    previous_catalog = _seed_catalog_with_modules_and_groups(tmp_path)
    canvas = FakeCanvas(submissions=[_sub(700010), _sub(700020)])

    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW,
                            course_name="Fresh Name")

    assert result["ok"] is True
    catalog = course_catalog.read_catalog(COURSE, root=str(tmp_path))["catalog"]
    assert catalog["assignments"]["state"] == "current"
    assert set(catalog["assignments"]["records"]) == {"700010", "700020"}
    assert catalog["modules"] == previous_catalog["modules"]
    assert catalog["assignment_groups"] == previous_catalog["assignment_groups"]
    assert catalog["course_name"] == "Fresh Name"
    # No second Canvas call for assignments was made on Catalog's behalf.
    assert sum(1 for path, _ in canvas.calls if path.endswith("/assignments")) == 1


def test_delta_pass_forwards_valid_receipt_to_catalog_leaving_modules_groups_unchanged(tmp_path):
    _backfilled(tmp_path)
    previous_catalog = _seed_catalog_with_modules_and_groups(tmp_path)
    canvas = FakeCanvas(delta_submitted=[_sub(700010, attempt=2)])

    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z", course_name="Fresh Name")

    assert result["ok"] is True
    catalog = course_catalog.read_catalog(COURSE, root=str(tmp_path))["catalog"]
    assert catalog["assignments"]["state"] == "current"
    assert set(catalog["assignments"]["records"]) == {"700010", "700020"}
    assert catalog["modules"] == previous_catalog["modules"]
    assert catalog["assignment_groups"] == previous_catalog["assignment_groups"]
    assert catalog["course_name"] == "Fresh Name"
    assert sum(1 for path, _ in canvas.calls if path.endswith("/assignments")) == 1


def test_delta_pass_bad_receipt_still_reaches_catalog_without_affecting_mirror_result(tmp_path):
    _backfilled(tmp_path, submissions=[_sub(700010), _sub(700020)])
    previous_catalog = _seed_catalog_with_modules_and_groups(tmp_path)

    result = sync.delta_pass(
        COURSE, canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=lambda *args, **kwargs: ([], None, False),
        root=str(tmp_path), now="2026-07-16T13:00:00Z")

    assert result == {"ok": False, "error": "pagination_incomplete"}
    catalog = course_catalog.read_catalog(COURSE, root=str(tmp_path))["catalog"]
    assert catalog["assignments"]["state"] == "stale"
    assert catalog["assignments"]["error_code"] == "pagination_incomplete"
    assert catalog["assignments"]["records"] == previous_catalog["assignments"]["records"]
    assert catalog["modules"] == previous_catalog["modules"]
    assert catalog["assignment_groups"] == previous_catalog["assignment_groups"]


def test_full_pass_bad_receipt_still_reaches_catalog_without_affecting_mirror_result(tmp_path):
    previous_catalog = _seed_catalog_with_modules_and_groups(tmp_path)

    result = sync.full_pass(
        COURSE, canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=lambda *args, **kwargs: (None, "HTTP 503: do not expose this", True),
        root=str(tmp_path), now=NOW)

    # The mirror pass's own result/control-flow is exactly what it already was
    # before this receipt was ever forwarded to Catalog.
    assert result == {"ok": False, "error": "HTTP 503: do not expose this"}
    catalog = course_catalog.read_catalog(COURSE, root=str(tmp_path))["catalog"]
    assert catalog["assignments"]["state"] == "stale"
    assert catalog["assignments"]["records"] == previous_catalog["assignments"]["records"]
    # Catalog's own document never leaks the raw transport detail.
    assert "503" not in json.dumps(catalog)


def test_catalog_write_failure_does_not_affect_full_pass_result(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.course_catalog, "refresh_catalog_assignments_only",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    canvas = FakeCanvas(submissions=[_sub(700010), _sub(700020)])

    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)

    assert result["ok"] is True
    assert result["assignments"] == 2


def test_catalog_write_failure_does_not_affect_delta_pass_result(monkeypatch, tmp_path):
    _backfilled(tmp_path)
    monkeypatch.setattr(sync.course_catalog, "refresh_catalog_assignments_only",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    canvas = FakeCanvas(delta_submitted=[_sub(700010, attempt=2)])

    result = sync.delta_pass(COURSE, canvas_get_all=canvas,
                             canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")

    assert result["ok"] is True


# --- submission comments freshness sidecar (1.0beta Batch 6) ---------------------

def test_full_pass_with_comments_marks_sidecar_current(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010, submission_comments=[
        {"author_id": 900099, "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ])])
    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    assert result["ok"] is True
    sidecar = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert sidecar["state"] == "current"
    assert sidecar["last_success_at"] == NOW


def test_full_pass_fetch_error_degrades_sidecar(tmp_path):
    canvas = FakeCanvas(errors={"submissions": "HTTP 503: upstream"})
    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    assert result["ok"] is False
    sidecar = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert sidecar["state"] == "unavailable"  # never succeeded
    assert sidecar["last_attempt_at"] == NOW


def test_delta_pass_never_advances_or_claims_comment_freshness(tmp_path):
    canvas = FakeCanvas(submissions=[_sub(700010, submission_comments=[
        {"author_id": 900099, "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ])])
    sync.full_pass(COURSE, canvas_get_all=canvas,
                   canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    before = store.read_submission_comments_state(COURSE, root=str(tmp_path))

    delta_canvas = FakeCanvas(delta_submitted=[_sub(700010, attempt=2, body="Second draft.")])
    result = sync.delta_pass(COURSE, canvas_get_all=delta_canvas,
                             canvas_get_all_complete=delta_canvas.complete, root=str(tmp_path),
                             now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    after = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert after == before  # unchanged: delta never claims comment freshness


def test_focused_assignment_refresh_never_advances_or_claims_comment_freshness(tmp_path):
    store.merge_submissions(COURSE, "700010", [_sub(700010)], root=str(tmp_path), replace=True)
    before = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert before["state"] == "unavailable"  # sidecar has never been touched

    def focused_canvas(path, params=None, timeout=30):
        return [_sub(700010, attempt=2, body="Focused second draft.")], None

    result = sync.sync_assignment_submissions(
        COURSE, "700010", canvas_get_all=focused_canvas, root=str(tmp_path), now=NOW,
    )
    assert result["ok"] is True
    after = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert after == before


def test_refresh_submissions_course_delta_never_advances_or_claims_comment_freshness(tmp_path):
    _backfilled(tmp_path)  # full_pass already marked the sidecar current
    before = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert before["state"] == "current"

    canvas = FakeCanvas(delta_submitted=[_sub(700010, attempt=2)])
    result = sync.refresh_submissions_course_delta(
        COURSE, canvas_get_all=canvas, root=str(tmp_path), now="2026-07-16T13:00:00Z")
    assert result["ok"] is True
    after = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert after == before


def test_roster_pass_never_advances_or_claims_comment_freshness(tmp_path):
    canvas = FakeCanvas()
    before = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    result = sync.roster_pass(COURSE, canvas_get_all=canvas,
                              canvas_get_all_complete=canvas.complete,
                              root=str(tmp_path), now=NOW)
    assert result["ok"] is True
    after = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert after == before  # roster does not touch the comment sidecar at all


# --- roster pass ------------------------------------------------------------------

def test_roster_pass_updates_roster_only(tmp_path):
    canvas = FakeCanvas()
    result = sync.roster_pass(COURSE, canvas_get_all=canvas,
                              canvas_get_all_complete=canvas.complete,
                              root=str(tmp_path), now=NOW)
    assert result == {"ok": True, "students": 1}
    assert store.read_roster(COURSE, root=str(tmp_path)) is not None
    assert store.read_assignments(COURSE, root=str(tmp_path)) is None
    assert store.read_sync(COURSE, root=str(tmp_path))["passes"]["roster"]["state"] == "current"


def test_roster_pass_failure_degrades(tmp_path):
    canvas = FakeCanvas()
    sync.roster_pass(COURSE, canvas_get_all=canvas,
                     canvas_get_all_complete=canvas.complete, root=str(tmp_path), now=NOW)
    failing = FakeCanvas(errors={"users": "HTTP 401: token"})
    result = sync.roster_pass(COURSE, canvas_get_all=failing,
                              canvas_get_all_complete=failing.complete, root=str(tmp_path),
                              now="2026-07-16T13:00:00Z")
    assert result["ok"] is False
    entry = store.read_sync(COURSE, root=str(tmp_path))["passes"]["roster"]
    assert entry["state"] == "stale"
    assert entry["last_success_at"] == NOW


# --- roster completeness + suspicious-wipe guard (roster teacher-trace fix) -------

def test_roster_pass_incomplete_receipt_preserves_last_good_roster(tmp_path):
    """roster_pass must require a proven-complete read before it can replace
    the roster, exactly like the assignment collection already requires for
    itself. A partial page counts as a failed pass, never as the new roster."""
    canvas = FakeCanvas()
    assert sync.roster_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete,
                            root=str(tmp_path), now=NOW)["ok"] is True
    before = store.read_roster(COURSE, root=str(tmp_path))

    result = sync.roster_pass(
        COURSE, canvas_get_all=canvas,
        canvas_get_all_complete=lambda *a, **k: ([], None, False),
        root=str(tmp_path), now="2026-07-16T13:00:00Z")

    assert result == {"ok": False, "error": "pagination_incomplete"}
    assert store.read_roster(COURSE, root=str(tmp_path)) == before
    entry = store.read_sync(COURSE, root=str(tmp_path))["passes"]["roster"]
    assert entry["state"] == "stale"
    assert entry["error_code"] == "pagination_incomplete"


def test_roster_pass_refuses_empty_wipe_against_nonempty_roster(tmp_path):
    """A complete read that comes back with zero students must never replace
    an existing nonempty roster. Skyward syncs and counselor section moves
    can make a legitimate Canvas answer look like this transiently, and the
    mirror must not treat a single empty page as the new truth."""
    canvas = FakeCanvas()
    assert sync.roster_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete,
                            root=str(tmp_path), now=NOW)["ok"] is True
    before = store.read_roster(COURSE, root=str(tmp_path))

    empty_canvas = FakeCanvas(users=[])
    result = sync.roster_pass(
        COURSE, canvas_get_all=empty_canvas,
        canvas_get_all_complete=empty_canvas.complete,
        root=str(tmp_path), now="2026-07-16T13:00:00Z")

    assert result == {"ok": False, "error": "roster_wipe_refused"}
    assert store.read_roster(COURSE, root=str(tmp_path)) == before
    entry = store.read_sync(COURSE, root=str(tmp_path))["passes"]["roster"]
    assert entry["state"] == "stale"
    assert entry["error_code"] == "roster_wipe_refused"


def test_roster_pass_allows_empty_roster_on_first_sync(tmp_path):
    """A genuinely empty course (no prior mirrored roster at all) must still
    be able to write its first roster: the wipe guard only fires once there
    is a nonempty roster on record to lose."""
    empty_canvas = FakeCanvas(users=[])
    result = sync.roster_pass(COURSE, canvas_get_all=empty_canvas,
                              canvas_get_all_complete=empty_canvas.complete,
                              root=str(tmp_path), now=NOW)
    assert result == {"ok": True, "students": 0}
    document = store.read_roster(COURSE, root=str(tmp_path))
    assert document["students"] == {}
    assert document["state"] == "current"


def test_full_pass_refuses_empty_wipe_and_never_commits_submissions(tmp_path):
    """The same guard applies inside full_pass, and a refused roster must
    stop the pass before submissions are ever fetched or committed on the
    strength of a roster this pass just refused."""
    canvas = FakeCanvas(submissions=[_sub(700010), _sub(700020)])
    assert sync.full_pass(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                          now=NOW)["ok"] is True
    before_roster = store.read_roster(COURSE, root=str(tmp_path))
    before_submissions = store.read_submissions(COURSE, "700010", root=str(tmp_path))

    def complete_with_empty_users(path, params=None, timeout=30):
        if path.endswith("/users"):
            return [], None, True
        return canvas.complete(path, params, timeout)

    def refuses_canvas_get_all(path, params=None, timeout=30):
        raise AssertionError(
            f"full_pass must not fetch sections/submissions after refusing "
            f"the roster: {path}")

    result = sync.full_pass(
        COURSE, canvas_get_all=refuses_canvas_get_all,
        canvas_get_all_complete=complete_with_empty_users,
        root=str(tmp_path), now="2026-07-17T03:00:00Z")

    assert result == {"ok": False, "error": "roster_wipe_refused"}
    assert store.read_roster(COURSE, root=str(tmp_path)) == before_roster
    assert store.read_submissions(COURSE, "700010", root=str(tmp_path)) == before_submissions
    state = store.read_sync(COURSE, root=str(tmp_path))
    assert state["passes"]["full"]["state"] == "stale"
    assert state["passes"]["full"]["error_code"] == "roster_wipe_refused"


# --- roster/full: sections-fetch failure must not blank section names ------------

def test_roster_pass_sections_error_preserves_previous_section_names(tmp_path):
    """A failed sections fetch must not blank section names Canvas never
    actually reported as gone. The previous mirrored section map survives
    a sections-only outage."""
    canvas = FakeCanvas()
    assert sync.roster_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete,
                            root=str(tmp_path), now=NOW)["ok"] is True
    assert store.read_roster(COURSE, root=str(tmp_path))["sections"] == {"800001": "Period 1"}

    failing_sections = FakeCanvas(errors={"sections": "HTTP 503: upstream"})
    result = sync.roster_pass(
        COURSE, canvas_get_all=failing_sections,
        canvas_get_all_complete=failing_sections.complete,
        root=str(tmp_path), now="2026-07-16T13:00:00Z")

    assert result["ok"] is True
    document = store.read_roster(COURSE, root=str(tmp_path))
    assert document["sections"] == {"800001": "Period 1"}


def test_full_pass_sections_error_preserves_previous_section_names(tmp_path):
    """The same guard applies inside full_pass: a sections-fetch failure
    there must not blank section names either, even though students and
    submissions refresh normally in the same pass."""
    canvas = FakeCanvas(submissions=[_sub(700010), _sub(700020)])
    assert sync.full_pass(COURSE, canvas_get_all=canvas,
                          canvas_get_all_complete=canvas.complete, root=str(tmp_path),
                          now=NOW)["ok"] is True
    assert store.read_roster(COURSE, root=str(tmp_path))["sections"] == {"800001": "Period 1"}

    failing_sections = FakeCanvas(errors={"sections": "HTTP 503: upstream"},
                                  submissions=[_sub(700010), _sub(700020)])
    result = sync.full_pass(COURSE, canvas_get_all=failing_sections,
                            canvas_get_all_complete=failing_sections.complete,
                            root=str(tmp_path), now="2026-07-17T03:00:00Z")

    assert result["ok"] is True
    document = store.read_roster(COURSE, root=str(tmp_path))
    assert document["sections"] == {"800001": "Period 1"}


# --- workspace guard -----------------------------------------------------------------

def test_passes_report_unconfigured_workspace(monkeypatch):
    from api.platform_services import workspace
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    canvas = FakeCanvas()
    result = sync.full_pass(COURSE, canvas_get_all=canvas,
                            canvas_get_all_complete=canvas.complete)
    assert result == {"ok": False, "error": "workspace not configured"}


# --- focused assignment submissions -----------------------------------------------

def test_focused_assignment_refresh_only_calls_one_endpoint_and_keeps_course_state(tmp_path):
    """This named scope is narrower than a delta, so it must not claim a
    successful pass or advance either course watermark."""
    store.merge_submissions(COURSE, "700010", [_sub(700010)], root=str(tmp_path), replace=True)
    store.record_pass(
        COURSE, "delta", ok=True, attempted_at=NOW,
        watermarks={"submitted_since": NOW_MINUS_OVERLAP,
                    "graded_since": NOW_MINUS_OVERLAP}, root=str(tmp_path),
    )
    before_state = store.read_sync(COURSE, root=str(tmp_path))
    calls = []

    def focused_canvas(path, params=None, timeout=30):
        calls.append((path, dict(params or {})))
        assert path == f"/api/v1/courses/{COURSE}/assignments/700010/submissions"
        return [_sub(700010, attempt=2, body="Focused second draft.", submission_history=[
            {"attempt": 2, "submitted_at": "2026-07-02T10:00:00Z",
             "submission_type": "online_text_entry", "body": "Focused second draft."},
        ])], None

    result = sync.sync_assignment_submissions(
        COURSE, "700010", canvas_get_all=focused_canvas, root=str(tmp_path), now=NOW,
    )

    assert result["ok"] is True
    assert calls == [(
        f"/api/v1/courses/{COURSE}/assignments/700010/submissions",
        {"per_page": 100, "include[]": ["submission_history"]},
    )]
    assert not any("/api/quiz/v1/" in path or path.endswith("/assignments")
                   or path.endswith("/students/submissions") or path.endswith("/users")
                   for path, _params in calls)
    document = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    assert document["submissions"]["900001"]["current"]["attempt"] == 2
    assert set(document["submissions"]["900001"]["attempts"]) == {"1", "2"}
    assert store.read_sync(COURSE, root=str(tmp_path)) == before_state

    replay = sync.sync_assignment_submissions(
        COURSE, "700010", canvas_get_all=focused_canvas, root=str(tmp_path), now=NOW,
    )
    assert replay["ok"] is True
    replayed = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    assert replayed == document


def test_focused_assignment_refresh_failure_preserves_last_good_without_pass_mutation(tmp_path):
    store.merge_submissions(COURSE, "700010", [_sub(700010, body="Last good body.")],
                            root=str(tmp_path), replace=True)
    store.record_pass(
        COURSE, "delta", ok=True, attempted_at=NOW,
        watermarks={"submitted_since": NOW_MINUS_OVERLAP,
                    "graded_since": NOW_MINUS_OVERLAP}, root=str(tmp_path),
    )
    before_document = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    before_state = store.read_sync(COURSE, root=str(tmp_path))

    result = sync.sync_assignment_submissions(
        COURSE, "700010",
        canvas_get_all=lambda *_args, **_kwargs: (None, "HTTP 503: upstream"),
        root=str(tmp_path), now="2026-07-16T13:00:00Z",
    )

    assert result["ok"] is False
    assert result["error_code"]
    assert store.read_submissions(COURSE, "700010", root=str(tmp_path)) == before_document
    assert store.read_sync(COURSE, root=str(tmp_path)) == before_state
