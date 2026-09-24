"""Offline tests for the CanvasMirror on-disk store.

The test conftest isolates machine-local cache paths. Explicit roots exercise
the corresponding identity vault, while mirror documents stay machine-local.
Fabricated data uses generic names and large made-up Canvas IDs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from api.mirror import course_context, read_service, store

COURSE = "111"

USERS = [
    {"id": 900001, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900001",
     "email": "learner@example.invalid",  # must NOT be stored
     "enrollments": [{"course_section_id": 800001}]},
    {"id": 900002, "name": "Learner Two", "sortable_name": "Two, Learner",
     "short_name": "Learner Two", "sis_user_id": "SIS-900002",
     "enrollments": [{"course_section_id": 800002}]},
]
SECTIONS = {"800001": "Period 1", "800002": "Period 2"}

ASSIGNMENTS = [
    {"id": 700010, "name": "Essay 1", "due_at": "2026-07-01T23:59:00Z",
     "points_possible": 10, "published": True,
     "html_url": "https://example.invalid/700010",
     "submission_types": ["online_text_entry"],
     "updated_at": "2026-06-01T00:00:00Z",
     "description": "Write a five-paragraph essay."},
]


def _submission_row(user_id=900001, attempt=1, body="First draft.",
                    history=None, **overrides):
    row = {
        "assignment_id": 700010, "user_id": user_id,
        "workflow_state": "submitted", "submitted_at": f"2026-07-0{attempt}T10:00:00Z",
        "graded_at": None, "score": None, "grade": None,
        "late": False, "missing": False, "excused": False,
        "attempt": attempt, "grade_matches_current_submission": True,
        "submission_type": "online_text_entry", "body": body,
    }
    if history is not None:
        row["submission_history"] = history
    row.update(overrides)
    return row


# --- roster ------------------------------------------------------------------

def test_roster_round_trip_keeps_only_consumer_fields(tmp_path, monkeypatch):
    from api.platform_services import workspace
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    store.write_roster(COURSE, USERS, SECTIONS, root=str(tmp_path))
    document = store.read_roster(COURSE, root=str(tmp_path))
    assert document["state"] == "current"
    assert set(document["students"]) == {"900001", "900002"}
    student = document["students"]["900001"]
    assert student["name"] == "Learner One"
    assert student["enrollments"] == [{"course_section_id": "800001"}]
    assert "email" not in student
    assert document["sections"] == SECTIONS
    assert read_service.private_roster(COURSE, max_age_hours=6)["state"] == "current"

    store.write_roster(COURSE, USERS, SECTIONS)
    assert store.read_roster(COURSE, root=str(tmp_path))["state"] == "current"


def test_roster_unions_sections_for_a_user_listed_once_per_enrollment(tmp_path):
    """Canvas can list one user once per enrollment rather than once with
    every enrollment attached. A student mid-transfer is in the new section
    before the old one is dropped, so last-wins would drop them out of the
    section they are still sitting in."""
    mover = {"id": 900001, "name": "Mover", "sortable_name": "Mover",
             "short_name": "M", "sis_user_id": "SIS-1"}
    store.write_roster(
        COURSE,
        [{**mover, "enrollments": [{"course_section_id": 800001}]},
         {**mover, "enrollments": [{"course_section_id": 800002}]}],
        SECTIONS, root=str(tmp_path))
    document = store.read_roster(COURSE, root=str(tmp_path))
    assert document["students"]["900001"]["enrollments"] == [
        {"course_section_id": "800001"}, {"course_section_id": "800002"}]


def test_roster_read_returns_none_for_missing_or_corrupt(tmp_path):
    assert store.read_roster(COURSE, root=str(tmp_path)) is None
    path = store.roster_path(COURSE, str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert store.read_roster(COURSE, root=str(tmp_path)) is None
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"schema_version": 99}, handle)
    assert store.read_roster(COURSE, root=str(tmp_path)) is None


def test_roster_course_id_mismatch_reads_as_absent(tmp_path):
    store.write_roster(COURSE, USERS, SECTIONS, root=str(tmp_path))
    other_path = store.roster_path("222", str(tmp_path))
    os.makedirs(os.path.dirname(other_path), exist_ok=True)
    os.replace(store.roster_path(COURSE, str(tmp_path)), other_path)
    assert store.read_roster("222", root=str(tmp_path)) is None


# --- assignments ---------------------------------------------------------------

def test_assignments_round_trip_slim_shape(tmp_path):
    store.write_assignments(COURSE, ASSIGNMENTS, root=str(tmp_path))
    document = store.read_assignments(COURSE, root=str(tmp_path))
    row = document["assignments"]["700010"]
    assert row == {
        "id": "700010", "name": "Essay 1", "due_at": "2026-07-01T23:59:00Z",
        "points_possible": 10, "published": True,
        "html_url": "https://example.invalid/700010",
        "submission_types": ["online_text_entry"],
        "updated_at": "2026-06-01T00:00:00Z",
        "description": "Write a five-paragraph essay.",
        "quiz_id": "", "is_quiz": False, "quiz_kind": "",
        "is_quiz_lti_assignment": False,
    }


def test_build_assignments_document_does_not_write_to_disk(tmp_path):
    document = store.build_assignments_document(
        COURSE, ASSIGNMENTS, attempted_at="2026-07-18T12:00:00Z")
    assert document["assignments"]["700010"]["name"] == "Essay 1"
    assert document["last_success_at"] == "2026-07-18T12:00:00Z"
    assert document["last_attempt_at"] == "2026-07-18T12:00:00Z"
    assert store.read_assignments(COURSE, root=str(tmp_path)) is None  # nothing written


def test_write_assignments_matches_build_assignments_document_plus_write(tmp_path):
    built = store.build_assignments_document(
        COURSE, ASSIGNMENTS, attempted_at="2026-07-18T12:00:00Z")
    written = store.write_assignments(
        COURSE, ASSIGNMENTS, root=str(tmp_path), attempted_at="2026-07-18T12:00:00Z")
    assert written == built
    assert store.read_assignments(COURSE, root=str(tmp_path)) == built


def test_normalize_assignment_description_is_permissive_string_like_body(tmp_path):
    # Additive field (Batch 5/Student Reports): same permissive-string handling
    # as the existing `body` field on submissions — non-string defaults to "".
    row = store.normalize_assignment({**ASSIGNMENTS[0], "description": 12345})
    assert row["description"] == ""
    row = store.normalize_assignment({k: v for k, v in ASSIGNMENTS[0].items() if k != "description"})
    assert row["description"] == ""
    # Every other field is unaffected by the addition.
    row = store.normalize_assignment(ASSIGNMENTS[0])
    assert row["name"] == "Essay 1"
    assert row["points_possible"] == 10
    assert row["submission_types"] == ["online_text_entry"]


def test_normalize_assignment_persists_student_free_new_quiz_classification():
    row = store.normalize_assignment({
        **ASSIGNMENTS[0], "id": 700011,
        "submission_types": ["external_tool"],
        "quiz_id": 880011,
        "is_quiz_lti_assignment": True,
        "rubric": [{"description": "must not be copied", "points": 1}],
    })
    assert {key: row[key] for key in (
        "quiz_id", "is_quiz", "quiz_kind", "is_quiz_lti_assignment",
    )} == {
        "quiz_id": "880011", "is_quiz": True, "quiz_kind": "new_quiz",
        "is_quiz_lti_assignment": True,
    }


# --- submissions: merge semantics ------------------------------------------------

def test_merge_submissions_stores_submission_comments_exact_shape(tmp_path):
    row = _submission_row(submission_comments=[
        {"author_id": 900099, "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z",
         "author_name": "Teacher T", "avatar_path": "/x.png"},
        {"comment": "Second note."},  # missing author_id/created_at
        "not-a-dict",  # skipped
    ])
    store.merge_submissions(COURSE, "700010", [row], root=str(tmp_path))
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    comments = entry["current"]["submission_comments"]
    assert comments == [
        {"author_id": "900099", "author_role": "", "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
        {"author_id": "", "author_role": "", "comment": "Second note.", "created_at": ""},
    ]
    for comment in comments:
        assert set(comment) == {"author_id", "author_role", "comment", "created_at"}


def test_delta_merge_without_comments_preserves_previously_stored_comments(tmp_path):
    row_with_comments = _submission_row(submission_comments=[
        {"author_id": 900099, "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ])
    store.merge_submissions(COURSE, "700010", [row_with_comments], root=str(tmp_path))
    # Simulated delta row has no submission_comments key at all (as delta fetches omit it).
    delta_row = _submission_row(attempt=2, body="Second draft.")
    assert "submission_comments" not in delta_row
    store.merge_submissions(COURSE, "700010", [delta_row], root=str(tmp_path))
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert entry["current"]["submission_comments"] == [
        {"author_id": "900099", "author_role": "", "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ]
    assert entry["current"]["attempt"] == 2  # other current fields still updated


def test_replace_merge_without_comments_preserves_previously_stored_comments(tmp_path):
    row_with_comments = _submission_row(submission_comments=[
        {"author_id": 900099, "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ])
    store.merge_submissions(COURSE, "700010", [row_with_comments], root=str(tmp_path))
    bare_row = _submission_row(attempt=2, body="Second draft.")
    store.merge_submissions(COURSE, "700010", [bare_row], root=str(tmp_path), replace=True)
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert entry["current"]["submission_comments"] == [
        {"author_id": "900099", "author_role": "", "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ]


def test_merge_submissions_is_idempotent_with_comments(tmp_path):
    rows = [_submission_row(submission_comments=[
        {"author_id": 900099, "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ])]
    first = store.merge_submissions(COURSE, "700010", rows, root=str(tmp_path),
                                    attempted_at="2026-07-01T12:00:00Z")
    second = store.merge_submissions(COURSE, "700010", rows, root=str(tmp_path),
                                     attempted_at="2026-07-01T12:00:00Z")
    assert first == second


def test_merge_submissions_records_current_and_attempts(tmp_path):
    row = _submission_row(history=[
        {"attempt": 1, "submitted_at": "2026-07-01T10:00:00Z",
         "submission_type": "online_text_entry", "body": "First draft.",
         "attachments": [{"filename": "draft.pdf"}]},
    ])
    store.merge_submissions(COURSE, "700010", [row], root=str(tmp_path))
    document = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    entry = document["submissions"]["900001"]
    assert entry["current"]["workflow_state"] == "submitted"
    assert entry["current"]["user_id"] == "900001"
    assert entry["attempts"]["1"]["body"] == "First draft."
    assert entry["attempts"]["1"]["attachment_names"] == ["draft.pdf"]


def test_current_carries_per_student_due_and_late_fields(tmp_path):
    row = _submission_row(cached_due_date="2026-07-02T23:59:00Z",
                          seconds_late=172800)
    store.merge_submissions(COURSE, "700010", [row], root=str(tmp_path))
    current = store.read_submissions(
        COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]["current"]
    assert current["cached_due_date"] == "2026-07-02T23:59:00Z"
    assert current["seconds_late"] == 172800


def test_current_late_fields_default_to_none_when_absent(tmp_path):
    store.merge_submissions(COURSE, "700010", [_submission_row()], root=str(tmp_path))
    current = store.read_submissions(
        COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]["current"]
    assert current["cached_due_date"] is None
    assert current["seconds_late"] is None


def test_current_carries_url_field_with_permissive_string_handling(tmp_path):
    # Additive field (Batch 5/Student Reports): the student's submitted URL for
    # an online_url submission, same permissive-string handling as `body`.
    row = _submission_row(url="https://example.invalid/my-site")
    store.merge_submissions(COURSE, "700010", [row], root=str(tmp_path))
    current = store.read_submissions(
        COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]["current"]
    assert current["url"] == "https://example.invalid/my-site"
    # Every other field remains unaffected by the addition.
    assert current["body"] == "First draft."
    assert current["workflow_state"] == "submitted"


def test_current_url_defaults_to_empty_string_when_absent_or_non_string(tmp_path):
    store.merge_submissions(COURSE, "700010", [_submission_row()], root=str(tmp_path))
    current = store.read_submissions(
        COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]["current"]
    assert current["url"] == ""

    store.merge_submissions(COURSE, "700010", [_submission_row(user_id=900002, url=12345)],
                            root=str(tmp_path))
    current = store.read_submissions(
        COURSE, "700010", root=str(tmp_path))["submissions"]["900002"]["current"]
    assert current["url"] == ""


def test_merge_is_idempotent(tmp_path):
    rows = [_submission_row()]
    first = store.merge_submissions(COURSE, "700010", rows, root=str(tmp_path),
                                    attempted_at="2026-07-01T12:00:00Z")
    second = store.merge_submissions(COURSE, "700010", rows, root=str(tmp_path),
                                     attempted_at="2026-07-01T12:00:00Z")
    assert first == second


def test_attempts_are_append_only_across_deltas(tmp_path):
    store.merge_submissions(COURSE, "700010", [_submission_row(attempt=1)],
                            root=str(tmp_path))
    # Second delta carries only attempt 2 (no history for attempt 1).
    store.merge_submissions(COURSE, "700010",
                            [_submission_row(attempt=2, body="Second draft.")],
                            root=str(tmp_path))
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert set(entry["attempts"]) == {"1", "2"}
    assert entry["current"]["attempt"] == 2
    assert entry["attempts"]["1"]["body"] == "First draft."
    assert entry["attempts"]["2"]["body"] == "Second draft."


def test_delta_merge_keeps_users_not_in_the_batch(tmp_path):
    store.merge_submissions(COURSE, "700010", [
        _submission_row(user_id=900001), _submission_row(user_id=900002),
    ], root=str(tmp_path))
    store.merge_submissions(COURSE, "700010",
                            [_submission_row(user_id=900001, attempt=2)],
                            root=str(tmp_path))
    document = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    assert set(document["submissions"]) == {"900001", "900002"}


def test_replace_merge_prunes_dropped_users_but_keeps_attempts(tmp_path):
    store.merge_submissions(COURSE, "700010", [
        _submission_row(user_id=900001, attempt=1),
        _submission_row(user_id=900002, attempt=1),
    ], root=str(tmp_path))
    store.merge_submissions(COURSE, "700010", [_submission_row(user_id=900001, attempt=2)],
                            root=str(tmp_path), replace=True)
    document = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    assert set(document["submissions"]) == {"900001"}  # 900002 pruned
    # attempt 1 survived the rewrite even though the new row only had attempt 2
    assert set(document["submissions"]["900001"]["attempts"]) == {"1", "2"}


def test_unsubmitted_rows_are_stored_without_attempts(tmp_path):
    row = _submission_row(workflow_state="unsubmitted", submitted_at=None,
                          attempt=None, missing=True, body="")
    store.merge_submissions(COURSE, "700010", [row], root=str(tmp_path))
    entry = store.read_submissions(COURSE, "700010", root=str(tmp_path))["submissions"]["900001"]
    assert entry["current"]["missing"] is True
    assert entry["attempts"] == {}


def test_prune_submission_files(tmp_path):
    store.merge_submissions(COURSE, "700010", [_submission_row()], root=str(tmp_path))
    store.merge_submissions(COURSE, "700020", [_submission_row(assignment_id=700020)],
                            root=str(tmp_path))
    removed = store.prune_submission_files(COURSE, ["700010"], root=str(tmp_path))
    assert removed == ["700020"]
    assert store.list_submission_assignment_ids(COURSE, root=str(tmp_path)) == ["700010"]


# --- sync state -------------------------------------------------------------------

def test_read_sync_defaults_when_missing(tmp_path):
    document = store.read_sync(COURSE, root=str(tmp_path))
    assert document["passes"]["full"]["state"] == "unavailable"
    assert document["watermarks"] == {"submitted_since": "", "graded_since": ""}


def test_record_pass_success_advances_watermarks(tmp_path):
    document = store.record_pass(
        COURSE, "delta", ok=True, attempted_at="2026-07-16T12:00:00Z",
        watermarks={"submitted_since": "2026-07-16T11:50:00Z",
                    "graded_since": "2026-07-16T11:50:00Z"},
        root=str(tmp_path))
    assert document["passes"]["delta"]["state"] == "current"
    assert document["passes"]["delta"]["last_success_at"] == "2026-07-16T12:00:00Z"
    assert document["watermarks"]["submitted_since"] == "2026-07-16T11:50:00Z"


def test_record_pass_failure_degrades_stale_then_unavailable(tmp_path):
    # Never succeeded -> unavailable, watermarks untouched.
    document = store.record_pass(COURSE, "full", ok=False, error_code="canvas_unavailable",
                                 root=str(tmp_path))
    assert document["passes"]["full"]["state"] == "unavailable"
    # Succeed once, then fail -> stale, last_success preserved.
    store.record_pass(COURSE, "full", ok=True, attempted_at="2026-07-16T12:00:00Z",
                      root=str(tmp_path))
    document = store.record_pass(COURSE, "full", ok=False, error_code="canvas_unavailable",
                                 attempted_at="2026-07-16T13:00:00Z", root=str(tmp_path))
    assert document["passes"]["full"]["state"] == "stale"
    assert document["passes"]["full"]["last_success_at"] == "2026-07-16T12:00:00Z"
    assert document["passes"]["full"]["error_code"] == "canvas_unavailable"


def test_failed_pass_never_advances_watermarks(tmp_path):
    store.record_pass(COURSE, "delta", ok=True, watermarks={"submitted_since": "A"},
                      root=str(tmp_path))
    store.record_pass(COURSE, "delta", ok=False, error_code="x",
                      root=str(tmp_path))
    assert store.read_sync(COURSE, root=str(tmp_path))["watermarks"]["submitted_since"] == "A"


# --- submission comments freshness sidecar (1.0beta Batch 6) ---------------------

def test_read_submission_comments_state_defaults_when_absent(tmp_path):
    document = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert document == store.default_submission_comments_state(COURSE)
    assert document["state"] == "unavailable"
    assert document["last_success_at"] == ""
    assert document["last_attempt_at"] == ""
    assert document["error_code"] == ""


def test_record_submission_comments_state_success_marks_current(tmp_path):
    document = store.record_submission_comments_state(
        COURSE, ok=True, attempted_at="2026-07-18T12:00:00Z", root=str(tmp_path))
    assert document["state"] == "current"
    assert document["last_success_at"] == "2026-07-18T12:00:00Z"
    assert document["last_attempt_at"] == "2026-07-18T12:00:00Z"
    assert document["error_code"] == ""
    assert store.read_submission_comments_state(COURSE, root=str(tmp_path)) == document


def test_record_submission_comments_state_failure_after_success_degrades_to_stale(tmp_path):
    store.record_submission_comments_state(
        COURSE, ok=True, attempted_at="2026-07-18T12:00:00Z", root=str(tmp_path))
    document = store.record_submission_comments_state(
        COURSE, ok=False, error_code="timeout", attempted_at="2026-07-18T13:00:00Z",
        root=str(tmp_path))
    assert document["state"] == "stale"
    assert document["last_success_at"] == "2026-07-18T12:00:00Z"  # preserved
    assert document["last_attempt_at"] == "2026-07-18T13:00:00Z"
    assert document["error_code"] == "timeout"


def test_record_submission_comments_state_failure_with_no_prior_success_is_unavailable(tmp_path):
    document = store.record_submission_comments_state(
        COURSE, ok=False, error_code="connection", attempted_at="2026-07-18T13:00:00Z",
        root=str(tmp_path))
    assert document["state"] == "unavailable"
    assert document["last_success_at"] == ""
    assert document["last_attempt_at"] == "2026-07-18T13:00:00Z"
    assert document["error_code"] == "connection"


def test_read_submission_comments_state_corrupt_file_reads_as_default_unavailable(tmp_path):
    path = store.submission_comments_state_path(COURSE, str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    document = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert document == store.default_submission_comments_state(COURSE)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"schema_version": 99}, handle)
    document = store.read_submission_comments_state(COURSE, root=str(tmp_path))
    assert document == store.default_submission_comments_state(COURSE)


def test_submission_comments_state_failure_leaves_last_good_submission_files_untouched(tmp_path):
    store.merge_submissions(COURSE, "700010", [_submission_row(submission_comments=[
        {"author_id": 900099, "comment": "Nice work.",
         "created_at": "2026-07-01T11:00:00Z"},
    ])], root=str(tmp_path))
    before = store.read_submissions(COURSE, "700010", root=str(tmp_path))

    store.record_submission_comments_state(
        COURSE, ok=False, error_code="timeout", root=str(tmp_path))

    after = store.read_submissions(COURSE, "700010", root=str(tmp_path))
    assert after == before


# --- unconfigured workspace --------------------------------------------------------

def test_writers_raise_without_workspace(monkeypatch):
    from api.platform_services import workspace
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    attempted_writes = [
        lambda: store.write_roster(COURSE, USERS, SECTIONS),
        lambda: store.write_groups(COURSE, []),
        lambda: store.write_assignments(COURSE, []),
        lambda: store.merge_submissions(COURSE, "700010", []),
        lambda: store.prune_submission_files(COURSE, []),
        lambda: store.invalidate_groups(COURSE),
        lambda: store.merge_group_category(COURSE, {"category_id": "800001", "groups": []}),
        lambda: store.mark_groups_stale(COURSE),
        lambda: store.begin_refresh(COURSE, operation_id="op-synthetic"),
        lambda: store.finish_refresh(COURSE, operation_id="op-synthetic", ok=True),
        lambda: store.record_course_context(COURSE, ok=True),
        lambda: store.write_late_policy(COURSE, {}),
        lambda: store.invalidate_late_policy(COURSE),
        lambda: store.write_new_quiz_capability(
            COURSE, capability="unknown", last_probe_at="2026-09-24T00:00:00Z"),
        lambda: store.record_pass(COURSE, "full", ok=True),
        lambda: store.record_submission_comments_state(COURSE, ok=True),
    ]
    for write in attempted_writes:
        with pytest.raises(ValueError, match="workspace not configured"):
            write()
    assert not (Path(workspace.canvas_mirror_root()) / COURSE).exists()
    assert store.read_roster(COURSE) is None


# --- course lifecycle context -------------------------------------------------

def test_course_context_strict_allowlist_and_conservative_classification(tmp_path):
    def canvas_get(path, params=None, timeout=20):
        assert path == "/api/v1/courses/111"
        assert params == {"include[]": "concluded"}
        return {
            "workflow_state": "completed", "concluded": True,
            "end_at": "2026-07-15T00:00:00Z",
            "term": {"end_at": "2026-07-16T00:00:00Z", "name": "Forbidden Term"},
            "name": "Forbidden Course", "sis_course_id": "forbidden-sis",
        }, None

    def canvas_get_all(path, params=None, timeout=30):
        assert path == "/api/v1/courses/111/enrollments"
        assert params == {"user_id": "self", "per_page": 100,
                          "state[]": ["active", "invited", "completed", "inactive"]}
        return [{"enrollment_state": "completed", "user_id": "never-store"},
                {"enrollment_state": "inactive", "role": "TeacherEnrollment"}], None

    context = course_context.refresh_course_context(
        COURSE, canvas_get=canvas_get, canvas_get_all=canvas_get_all,
        root=str(tmp_path), now="2026-07-16T12:00:00Z")
    assert set(context) == {
        "schema_version", "course_id", "state", "last_success_at", "last_attempt_at",
        "error_code", "lifecycle", "course_workflow_state", "course_concluded",
        "course_end_at", "term_end_at", "enrollment_states",
    }
    assert context["lifecycle"] == "concluded"
    assert context["enrollment_states"] == ["completed", "inactive"]
    assert context["course_workflow_state"] == "completed"
    raw = json.dumps(context)
    assert "Forbidden" not in raw and "never-store" not in raw and "TeacherEnrollment" not in raw

    assert course_context.classify_lifecycle(["active"]) == "current"
    assert course_context.classify_lifecycle(["completed"]) == "concluded"
    assert course_context.classify_lifecycle(["active", "completed"]) == "current"
    assert course_context.classify_lifecycle([]) == "unknown"


def test_course_context_invalid_file_is_absent_and_failure_preserves_last_good_only(tmp_path):
    path = store.course_context_path(COURSE, str(tmp_path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"schema_version": 1, "course_id": COURSE, "name": "forbidden"}, handle)
    assert store.read_course_context(COURSE, root=str(tmp_path)) == store.default_course_context(COURSE)

    good = store.record_course_context(
        COURSE, ok=True, attempted_at="2026-07-16T12:00:00Z", lifecycle="concluded",
        course_workflow_state="completed", course_concluded=True,
        course_end_at="2026-07-15T00:00:00Z", term_end_at="2026-07-16T00:00:00Z",
        enrollment_states=["completed"], root=str(tmp_path))
    store.write_assignments(COURSE, ASSIGNMENTS, root=str(tmp_path))
    store.record_pass(COURSE, "delta", ok=True, attempted_at="2026-07-16T12:00:00Z",
                      watermarks={"submitted_since": "watermark", "graded_since": "watermark"},
                      root=str(tmp_path))
    store.write_new_quiz_capability(
        COURSE, capability="restricted", last_probe_at="2026-07-16T12:00:00Z",
        retry_after="2026-07-17T12:00:00Z", evidence_category="forbidden",
        consecutive_failures=3, root=str(tmp_path))
    assignments_before = store.read_assignments(COURSE, root=str(tmp_path))
    sync_before = store.read_sync(COURSE, root=str(tmp_path))
    capability_before = store.read_new_quiz_capability(COURSE, root=str(tmp_path))

    stale = course_context.refresh_course_context(
        COURSE, canvas_get=lambda *args, **kwargs: (None, "HTTP 403: raw private details"),
        canvas_get_all=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not reached")),
        root=str(tmp_path), now="2026-07-16T13:00:00Z")
    assert stale["state"] == "stale"
    assert stale["error_code"] == "forbidden"
    for key in ("last_success_at", "lifecycle", "course_workflow_state", "course_concluded",
                "course_end_at", "term_end_at", "enrollment_states"):
        assert stale[key] == good[key]
    assert store.read_assignments(COURSE, root=str(tmp_path)) == assignments_before
    assert store.read_sync(COURSE, root=str(tmp_path)) == sync_before
    assert store.read_new_quiz_capability(COURSE, root=str(tmp_path)) == capability_before


# --- groups: merge_group_category (targeted post-write reconciliation) -------------

CATEGORY_A = {
    "category_id": "7", "category_name": "Reading groups",
    "groups": [{
        "id": "8", "name": "Blue",
        "memberships": [{"id": "9", "user_id": "900001"}],
    }],
}
CATEGORY_B = {
    "category_id": "20", "category_name": "Math groups",
    "groups": [{
        "id": "21", "name": "Advanced",
        "memberships": [{"id": "22", "user_id": "900002"}],
    }],
}


def test_merge_group_category_skips_without_previous_document(tmp_path):
    """A lone category is never treated as the course's complete membership."""
    result = store.merge_group_category(
        COURSE, {"category_id": "7", "category_name": "Reading groups", "groups": []},
        root=str(tmp_path))
    assert result is None
    assert store.read_groups(COURSE, root=str(tmp_path)) is None


def test_merge_group_category_replaces_only_matching_category(tmp_path):
    store.write_groups(COURSE, [CATEGORY_A, CATEGORY_B], root=str(tmp_path),
                       attempted_at="2026-07-18T00:00:00Z")

    incoming = {
        "category_id": "7", "category_name": "Reading groups",
        "groups": [{
            "id": "8", "name": "Blue", "student_ids": ["900001", "900003"],
            "memberships": [
                {"id": "9", "user_id": "900001"},
                {"id": "30", "user_id": "900003"},
            ],
        }],
    }
    merged = store.merge_group_category(COURSE, incoming, root=str(tmp_path),
                                        attempted_at="2026-07-18T01:00:00Z")

    assert merged["state"] == "current"
    assert merged["last_success_at"] == "2026-07-18T01:00:00Z"
    assert merged["last_attempt_at"] == "2026-07-18T01:00:00Z"
    assert merged["error_code"] == ""
    # Every other category passes through byte-identical.
    other = next(c for c in merged["categories"] if c["category_id"] == "20")
    assert other == CATEGORY_B
    changed = next(c for c in merged["categories"] if c["category_id"] == "7")
    assert changed["groups"] == [{
        "id": "8", "name": "Blue",
        "memberships": [
            {"id": "9", "user_id": "900001"},
            {"id": "30", "user_id": "900003"},
        ],
    }]
    # Persisted, not just returned.
    assert store.read_groups(COURSE, root=str(tmp_path)) == merged


def test_merge_group_category_appends_genuinely_new_category(tmp_path):
    store.write_groups(COURSE, [CATEGORY_A], root=str(tmp_path))

    new_category = {
        "category_id": "20", "category_name": "Math groups",
        "groups": [{"id": "21", "name": "Advanced",
                    "memberships": [{"id": "22", "user_id": "900002"}]}],
    }
    merged = store.merge_group_category(COURSE, new_category, root=str(tmp_path))

    assert [c["category_id"] for c in merged["categories"]] == ["7", "20"]
    assert merged["categories"][0] == CATEGORY_A
    assert merged["categories"][1] == new_category


def test_merge_group_category_keeps_previous_name_when_incoming_name_falsy(tmp_path):
    """Only a genuinely new category (create_group_set) supplies a real name;
    every other caller only knows the category id and must not invent one."""
    store.write_groups(COURSE, [CATEGORY_A], root=str(tmp_path))

    merged = store.merge_group_category(COURSE, {
        "category_id": "7", "category_name": None,
        "groups": [{"id": "8", "name": "Blue", "memberships": []}],
    }, root=str(tmp_path))

    assert merged["categories"][0]["category_name"] == "Reading groups"
    assert merged["categories"][0]["groups"] == [{"id": "8", "name": "Blue", "memberships": []}]


def test_merge_group_category_raises_on_invalid_incoming_category_and_writes_nothing(tmp_path):
    store.write_groups(COURSE, [CATEGORY_A], root=str(tmp_path))

    with pytest.raises(ValueError):
        store.merge_group_category(COURSE, {
            "category_id": "7", "category_name": "Reading groups",
            "groups": [{"id": "8", "name": "Blue",
                       "memberships": [{"id": "", "user_id": "900001"}]}],
        }, root=str(tmp_path))

    # The failed merge attempt must not have touched the on-disk document —
    # the caller falls back to the existing whole-document invalidate_groups.
    assert store.read_groups(COURSE, root=str(tmp_path))["categories"] == [CATEGORY_A]
