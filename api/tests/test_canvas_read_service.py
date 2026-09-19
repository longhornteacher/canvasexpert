"""Contract tests for the local-only typed Canvas read spine."""

from __future__ import annotations

import pytest

from api.mirror import read_service, store


COURSE = "course-1"
STAMP = "2026-07-18T12:00:00Z"


def _populate(root):
    store.write_roster(COURSE, [{
        "id": "student-1", "name": "Synthetic learner", "sortable_name": "Learner, Synthetic",
        "short_name": "Synthetic", "enrollments": [],
    }], {}, root=root, attempted_at=STAMP)
    store.write_groups(COURSE, [{
        "category_id": "category-1", "category_name": "Teams", "groups": [{
            "id": "group-1", "name": "Team 1",
            "memberships": [{"id": "membership-1", "user_id": "student-1"}],
        }],
    }], root=root, attempted_at=STAMP)
    store.write_assignments(COURSE, [{
        "id": "assignment-1", "name": "Practice", "due_at": "", "points_possible": 10,
        "published": True, "submission_types": [],
    }], root=root, attempted_at=STAMP)
    store.merge_submissions(COURSE, "assignment-1", [{
        "assignment_id": "assignment-1", "user_id": "student-1",
        "workflow_state": "submitted", "submitted_at": STAMP, "score": None,
    }], root=root, attempted_at=STAMP, replace=True)
    for pass_name in ("full", "roster"):
        store.record_pass(COURSE, pass_name, ok=True, attempted_at=STAMP, root=root)


def _catalog_reader(course_id):
    scope = {"state": "current", "last_success_at": STAMP,
             "last_attempt_at": STAMP, "error_code": ""}
    return {"catalog": {
        "version": 3, "course_id": course_id, "course_name": "Synthetic course",
        "assignments": {**scope, "records": {"assignment-1": {
            "id": "assignment-1", "name": "Practice", "description_text": "",
        }}},
        "modules": {**scope, "records": [{"id": "module-1", "name": "Unit 1"}]},
        "assignment_groups": {**scope, "records": [{"id": "group-1", "name": "Work"}]},
    }, "source": "canonical", "warnings": []}


def test_private_scopes_have_exact_copied_local_envelopes(tmp_path):
    _populate(str(tmp_path))
    readers = (
        read_service.private_roster,
        read_service.private_groups,
        read_service.private_assignments,
        read_service.private_submissions,
    )
    expected_keys = {
        "course_id", "scope", "state", "capability", "source", "last_success_at",
        "last_attempt_at", "canvas_observed_at", "retry_after", "generation",
        "mirror_revision", "snapshot_id", "refresh_state", "error_code", "records",
    }

    results = [reader(COURSE, root=str(tmp_path)) for reader in readers]

    assert {result["scope"] for result in results} == {
        read_service.PRIVATE_ROSTER, read_service.PRIVATE_GROUPS,
        read_service.PRIVATE_ASSIGNMENTS, read_service.PRIVATE_SUBMISSIONS,
    }
    assert all(set(result) == expected_keys for result in results)
    assert all(result["source"] == "mirror" and result["state"] == "current" for result in results)
    assert all(result["capability"] == "supported" for result in results)
    assert results[1]["records"][0]["groups"][0]["memberships"] == [
        {"id": "membership-1", "user_id": "student-1"},
    ]

    results[2]["records"][0]["name"] = "changed only in caller"
    stored = store.read_assignments(COURSE, root=str(tmp_path))
    assert stored["assignments"]["assignment-1"]["name"] == "Practice"


def test_private_assignments_freshness_tracks_sync_not_the_file_envelope(tmp_path):
    """private_assignments must report freshness from the full/delta
    sync-pass envelope (store.read_sync), not from the assignments file's
    own envelope -- so a no-op sync tick that leaves the (unchanged)
    assignments file unwritten still reports current, modeled on the
    identical existing behavior for private_submissions."""
    root = str(tmp_path)
    old_file_stamp = "2026-07-01T00:00:00Z"
    store.write_assignments(COURSE, [{
        "id": "assignment-1", "name": "Practice", "due_at": "", "points_possible": 10,
        "published": True, "submission_types": [],
    }], root=root, attempted_at=old_file_stamp)  # the file's own envelope is old/unwritten-since

    fresh_sync_stamp = "2026-07-18T11:00:00Z"
    store.record_pass(COURSE, "delta", ok=True, attempted_at=fresh_sync_stamp, root=root)

    result = read_service.private_assignments(
        COURSE, root=root, max_age_hours=6, now="2026-07-18T12:00:00Z")

    assert result["state"] == "current"
    assert result["last_success_at"] == fresh_sync_stamp
    assert result["records"]


def test_private_assignments_freshness_ages_out_when_sync_is_old_even_if_file_looks_fresh(tmp_path):
    root = str(tmp_path)
    fresh_file_stamp = "2026-07-18T11:55:00Z"
    store.write_assignments(COURSE, [{
        "id": "assignment-1", "name": "Practice", "due_at": "", "points_possible": 10,
        "published": True, "submission_types": [],
    }], root=root, attempted_at=fresh_file_stamp)  # the file's own envelope looks fresh

    old_sync_stamp = "2026-07-18T04:00:00Z"  # 8h before "now" below, past the 6h window
    store.record_pass(COURSE, "full", ok=True, attempted_at=old_sync_stamp, root=root)

    result = read_service.private_assignments(
        COURSE, root=root, max_age_hours=6, now="2026-07-18T12:00:00Z")

    assert result["state"] == "stale"
    assert result["last_success_at"] == old_sync_stamp
    assert result["records"]  # staleness is metadata only; records are still served


def test_age_limit_labels_last_good_private_records_stale(tmp_path):
    _populate(str(tmp_path))

    result = read_service.private_roster(
        COURSE, root=str(tmp_path), max_age_hours=1, now="2026-07-18T14:00:00Z")

    assert result["state"] == "stale"
    assert result["records"]
    assert result["source"] == "mirror"


def test_catalog_scopes_adapt_only_catalog_records_without_forbidden_fields():
    readers = (
        read_service.catalog_assignments,
        read_service.catalog_modules,
        read_service.catalog_assignment_groups,
    )

    catalog_result = _catalog_reader(COURSE)
    results = [reader(COURSE, catalog_reader=lambda _course_id: catalog_result) for reader in readers]

    assert [result["scope"] for result in results] == [
        read_service.CATALOG_ASSIGNMENTS, read_service.CATALOG_MODULES,
        read_service.CATALOG_ASSIGNMENT_GROUPS,
    ]
    assert all(result["source"] == "catalog" and result["state"] == "current" for result in results)
    assert "html_url" not in results[0]["records"][0]
    results[0]["records"][0]["name"] = "changed only in caller"
    assert catalog_result["catalog"]["assignments"]["records"]["assignment-1"]["name"] == "Practice"


# --- private.submission_comments (1.0beta Batch 6) ------------------------------

def test_private_submission_comments_envelope_comes_from_sidecar_not_full_delta(tmp_path):
    _populate(str(tmp_path))  # full/delta passes current as of STAMP
    comment_stamp = "2026-07-18T15:00:00Z"  # the comment sidecar succeeds later, separately
    store.record_submission_comments_state(COURSE, ok=True, attempted_at=comment_stamp,
                                           root=str(tmp_path))

    submissions = read_service.private_submissions(COURSE, root=str(tmp_path))
    comments = read_service.private_submission_comments(COURSE, root=str(tmp_path))

    assert comments["scope"] == read_service.PRIVATE_SUBMISSION_COMMENTS
    assert comments["state"] == "current"
    assert comments["last_success_at"] == comment_stamp
    assert comments["last_success_at"] != submissions["last_success_at"]
    assert comments["generation"] != submissions["generation"]
    assert comments["records"] == submissions["records"]  # reuses the same normalized rows


def test_private_submission_comments_max_age_hours_ages_current_to_stale(tmp_path):
    _populate(str(tmp_path))
    store.record_submission_comments_state(COURSE, ok=True, attempted_at=STAMP, root=str(tmp_path))

    result = read_service.private_submission_comments(
        COURSE, root=str(tmp_path), max_age_hours=1, now="2026-07-18T14:00:00Z")

    assert result["state"] == "stale"
    assert result["records"]


def test_private_submission_comments_missing_sidecar_is_unavailable_with_records_intact(tmp_path):
    _populate(str(tmp_path))  # sidecar never recorded

    result = read_service.private_submission_comments(COURSE, root=str(tmp_path))

    assert result["state"] == "unavailable"
    assert result["last_success_at"] == ""
    assert result["records"]  # reused submission records are unaffected by the sidecar


def test_private_submission_comments_corrupt_sidecar_is_unavailable_with_records_intact(tmp_path):
    _populate(str(tmp_path))
    store.record_submission_comments_state(COURSE, ok=True, attempted_at=STAMP, root=str(tmp_path))
    path = store.submission_comments_state_path(COURSE, str(tmp_path))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")

    result = read_service.private_submission_comments(COURSE, root=str(tmp_path))

    assert result["state"] == "unavailable"
    assert result["records"]


def test_read_dispatches_private_submission_comments(tmp_path):
    _populate(str(tmp_path))
    store.record_submission_comments_state(COURSE, ok=True, attempted_at=STAMP, root=str(tmp_path))

    result = read_service.read(read_service.PRIVATE_SUBMISSION_COMMENTS, COURSE, root=str(tmp_path))

    assert result["scope"] == read_service.PRIVATE_SUBMISSION_COMMENTS
    assert result["state"] == "current"


def test_only_local_display_and_offline_intents_are_implemented(tmp_path):
    _populate(str(tmp_path))

    offline = read_service.read(
        read_service.PRIVATE_ROSTER, COURSE, intent=read_service.OFFLINE, root=str(tmp_path))
    assert offline["source"] == "mirror"
    with pytest.raises(ValueError, match="not local"):
        read_service.read(read_service.PRIVATE_ROSTER, COURSE,
                          intent=read_service.AUTHORITATIVE_LIVE, root=str(tmp_path))
