"""Synthetic CanvasMirror v2 New Quiz coverage; no live identifiers or data."""
from __future__ import annotations

import json
import os

from api.mirror import new_quizzes, store
from api.powergrader import assignment_refresh, canvas_fetch, new_quiz_fetch
from api.platform_services import config, workspace


COURSE = "course-synthetic"
ASSIGNMENT = "quiz-synthetic"
NOW = "2026-07-16T12:00:00Z"


def _assignment():
    return {
        "id": ASSIGNMENT, "name": "Fictional Quiz", "description": "Explain.",
        "points_possible": 10, "published": True,
        "is_quiz_lti_assignment": True, "submission_types": ["external_tool"],
    }


def _items():
    return [{
        "id": "outer-essay", "points_possible": 10,
        "entry": {"id": "essay-1", "interaction_type_slug": "essay",
                   "item_body": "<p>Explain the fictional result.</p>"},
    }]


def _attempt(attempt, answer, *, result_id="", evidence=None, submitted=None):
    return {
        "user_id": "student-synthetic", "user": {"name": "Fictional Student"},
        "workflow_state": "submitted", "submission_type": "external_tool",
        "submitted_at": submitted or f"2026-07-{attempt:02d}T10:00:00Z",
        "body": answer, "new_quiz_attempt": attempt,
        "new_quiz_reported_at": submitted or f"2026-07-{attempt:02d}T10:00:00Z",
        "new_quiz_items": [{"item_id": "essay-1", "type": "essay",
                             "prompt": "Prompt", "raw_html_answer": answer,
                             "possible": 10, "earned_score": attempt,
                             "status": "NotGraded", "files": []}],
        "attachments": evidence or [],
        "new_quiz_result_identity": ({"quiz_session_id": f"session-{attempt}",
                                       "result_id": result_id} if result_id else {}),
        "assignment": _assignment(),
    }


def test_snapshot_retains_attempts_identity_and_relative_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    evidence_path = tmp_path / "Courses" / "answer.txt"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text("synthetic", encoding="utf-8")
    latest = _attempt(2, "latest", result_id="result-2", evidence=[{
        "filename": "answer.txt", "item_id": "essay-1", "evidence_id": "file-2",
        "local_path": str(evidence_path), "download_status": "downloaded",
        "extraction_status": "validated", "url": "https://signed.invalid/file",
    }])
    older = _attempt(1, "older", result_id="result-1")

    result = new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[older, latest], latest=[latest], root=str(tmp_path),
        attempted_at=NOW,
    )
    assert result == {"ok": True, "state": "current", "students": 1, "attempts": 2}
    student = new_quizzes.read_student(COURSE, ASSIGNMENT, "student-synthetic", root=str(tmp_path))
    assert [entry["attempt"] for entry in student["attempts"]] == [1, 2]
    assert student["current"]["attempt"] == 2
    assert student["current"]["result_identity"]["result_id"] == "result-2"
    assert student["current"]["evidence"][0]["relative_path"] == os.path.relpath(evidence_path, tmp_path)
    raw = json.dumps(student)
    assert "signed.invalid" not in raw and "url" not in raw

    snapshot, error = new_quizzes.read_fresh_snapshot(
        COURSE, ASSIGNMENT, root=str(tmp_path), max_age_hours=6, now=NOW,
    )
    assert error is None and snapshot["source"] == "mirror"
    assert snapshot["students"][0]["new_quiz_attempt"] == 2


def test_invalidate_responses_marks_snapshot_stale_for_live_refetch(tmp_path):
    latest = _attempt(1, "answer", result_id="result-1")
    new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[latest], latest=[latest], root=str(tmp_path),
        attempted_at=NOW,
    )
    snapshot, error = new_quizzes.read_fresh_snapshot(
        COURSE, ASSIGNMENT, root=str(tmp_path), max_age_hours=6, now=NOW)
    assert error is None and snapshot is not None

    result = new_quizzes.invalidate_responses(
        COURSE, ASSIGNMENT, root=str(tmp_path), attempted_at="2026-07-16T12:05:00Z")
    assert result is not None
    # Stale now -> the next read falls back through the existing live native
    # chain rather than serving pre-write item scores.
    snapshot, error = new_quizzes.read_fresh_snapshot(
        COURSE, ASSIGNMENT, root=str(tmp_path), max_age_hours=6, now=NOW)
    assert snapshot is None and error == "stale"
    assert new_quizzes.response_freshness(
        COURSE, ASSIGNMENT, root=str(tmp_path), max_age_hours=6, now=NOW) == ""


def test_invalidate_responses_is_a_no_op_without_a_snapshot(tmp_path):
    assert new_quizzes.invalidate_responses(COURSE, ASSIGNMENT, root=str(tmp_path)) is None
    # No responses envelope is fabricated for an assignment never fetched.
    assert new_quizzes.read_sync(COURSE, root=str(tmp_path))["responses"] == {}


def test_attempts_are_append_preserving_across_snapshots(tmp_path):
    # First snapshot captures attempts 1 and 2.
    new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[_attempt(1, "older"), _attempt(2, "middle")],
        root=str(tmp_path), attempted_at=NOW,
    )
    # A later report only carries attempt 3 — earlier attempts must survive,
    # while current/latest reflect the new fetch alone.
    result = new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[_attempt(3, "newest")],
        root=str(tmp_path), attempted_at="2026-07-16T13:00:00Z",
    )
    assert result["ok"] is True
    student = new_quizzes.read_student(COURSE, ASSIGNMENT, "student-synthetic", root=str(tmp_path))
    assert [entry["attempt"] for entry in student["attempts"]] == [1, 2, 3]
    assert student["current"]["attempt"] == 3
    assert student["latest_attempt"] == 3
    # Replaying the same snapshot stays idempotent.
    new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[_attempt(3, "newest")],
        root=str(tmp_path), attempted_at="2026-07-16T13:00:00Z",
    )
    replayed = new_quizzes.read_student(COURSE, ASSIGNMENT, "student-synthetic", root=str(tmp_path))
    assert replayed == student


def test_malformed_attempt_value_degrades_instead_of_crashing(tmp_path):
    broken = _attempt(1, "answer")
    broken["new_quiz_attempt"] = "not-a-number"
    result = new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[broken], root=str(tmp_path), attempted_at=NOW,
    )
    assert result["state"] in {"current", "incomplete"}  # wrote, did not raise
    student = new_quizzes.read_student(COURSE, ASSIGNMENT, "student-synthetic", root=str(tmp_path))
    assert student is not None


def test_scrub_removes_preview_and_download_urls(tmp_path):
    leaky = _attempt(1, "answer")
    leaky["preview_url"] = "https://tenant.invalid/preview?verifier=SECRET"
    leaky["download_url"] = "https://tenant.invalid/files/1/download?verifier=SECRET"
    new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[leaky], root=str(tmp_path), attempted_at=NOW,
    )
    student = new_quizzes.read_student(COURSE, ASSIGNMENT, "student-synthetic", root=str(tmp_path))
    raw = json.dumps(student)
    assert "SECRET" not in raw and "preview_url" not in raw and "download_url" not in raw


def test_duplicate_or_stale_snapshots_fail_closed(tmp_path):
    duplicate = _attempt(1, "one", result_id="result-1")
    duplicate_again = _attempt(1, "one-again", result_id="result-1b")
    result = new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[duplicate, duplicate_again], latest=[duplicate],
        root=str(tmp_path), attempted_at=NOW,
    )
    assert result["state"] == "incomplete"
    snapshot, error = new_quizzes.read_fresh_snapshot(
        COURSE, ASSIGNMENT, root=str(tmp_path), max_age_hours=6, now=NOW,
    )
    assert snapshot is None and error == "incomplete"

    current = _attempt(1, "current", result_id="result-current")
    new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[current], latest=[current], root=str(tmp_path),
        attempted_at="2026-07-01T12:00:00Z",
    )
    snapshot, error = new_quizzes.read_fresh_snapshot(
        COURSE, ASSIGNMENT, root=str(tmp_path), max_age_hours=6, now=NOW,
    )
    assert snapshot is None and error == "stale"


def test_corrupt_v2_files_are_absent_and_identical_refresh_is_idempotent(tmp_path):
    os.makedirs(os.path.dirname(new_quizzes.quiz_path(COURSE, ASSIGNMENT, str(tmp_path))), exist_ok=True)
    with open(new_quizzes.quiz_path(COURSE, ASSIGNMENT, str(tmp_path)), "w", encoding="utf-8") as handle:
        handle.write("not-json")
    assert new_quizzes.read_quiz(COURSE, ASSIGNMENT, root=str(tmp_path)) is None

    row = _attempt(1, "same", result_id="result-same")
    kwargs = {
        "assignment": _assignment(), "items": _items(),
        "normalized_attempts": [row], "latest": [row],
        "root": str(tmp_path), "attempted_at": NOW,
    }
    new_quizzes.write_response_snapshot(COURSE, ASSIGNMENT, **kwargs)
    first = (tmp_path / "_System" / "Canvas Mirror" / COURSE / "new_quizzes" /
             ASSIGNMENT / "students" / "student-synthetic.v2.json").read_text(encoding="utf-8")
    new_quizzes.write_response_snapshot(COURSE, ASSIGNMENT, **kwargs)
    second = (tmp_path / "_System" / "Canvas Mirror" / COURSE / "new_quizzes" /
              ASSIGNMENT / "students" / "student-synthetic.v2.json").read_text(encoding="utf-8")
    assert first == second


def test_stale_or_incomplete_cache_falls_back_to_focused_consumer(monkeypatch, tmp_path):
    monkeypatch.setattr(assignment_refresh.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(assignment_refresh.workspace, "read_assignment_evidence_manifest", lambda *args: None)
    monkeypatch.setattr(assignment_refresh.workspace, "assignment_evidence_conflicts", lambda *args: [])
    monkeypatch.setattr(assignment_refresh.workspace, "write_assignment_evidence_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(config, "course_display_name", lambda _course_id: "Fictional Course")
    monkeypatch.setattr(config, "mirror_serve_max_age_hours", lambda: 6.0)
    calls = []

    def focused(course_id, assignment_id, **kwargs):
        calls.append(kwargs)
        return ([{"user_id": "student-synthetic", "submission_type": "online_text_entry"}],
                {"id": assignment_id, "name": "Fictional Assignment", "is_quiz_lti_assignment": False}, None)

    monkeypatch.setattr(assignment_refresh.canvas_fetch, "fetch_submissions", focused)
    for state in ("stale", "incomplete"):
        monkeypatch.setattr(assignment_refresh.new_quizzes, "read_fresh_snapshot", lambda *args, _state=state, **kwargs: (None, _state))
        assignment_refresh.refresh_assignment(COURSE, ASSIGNMENT, session_id=f"session-{state}")
        assert "cached_new_quiz" not in calls[-1]


def test_metadata_sync_is_separate_and_prunes_only_after_complete_pass(tmp_path):
    calls = []

    def canvas(path, params=None, timeout=30):
        calls.append(path)
        if path.endswith("/quizzes"):
            return ([{"id": ASSIGNMENT, "title": "Fictional Quiz", "points_possible": 10}], None)
        if path.endswith("/items"):
            return (_items(), None)
        raise AssertionError(path)

    result = new_quizzes.sync_metadata(
        COURSE, [_assignment()], canvas_get_all=canvas, root=str(tmp_path), now=NOW,
    )
    assert result["ok"] is True and result["quizzes"] == 1
    quiz = new_quizzes.read_quiz(COURSE, ASSIGNMENT, root=str(tmp_path))
    assert quiz["quiz"]["title"] == "Fictional Quiz"
    assert quiz["items"]["essay-1"]["points_possible"] == 10
    assert calls == [
        f"/api/quiz/v1/courses/{COURSE}/quizzes",
        f"/api/quiz/v1/courses/{COURSE}/quizzes/{ASSIGNMENT}/items",
    ]


def test_metadata_sync_falls_back_narrowly_when_collection_has_no_matching_id(tmp_path):
    calls = []

    def canvas(path, params=None, timeout=30):
        calls.append(path)
        if path.endswith("/quizzes"):
            return [], None
        if path.endswith(f"/quizzes/{ASSIGNMENT}"):
            return [{"id": ASSIGNMENT, "title": "Fallback Quiz"}], None
        if path.endswith("/items"):
            return _items(), None
        raise AssertionError(path)

    result = new_quizzes.sync_metadata(
        COURSE, [_assignment()], canvas_get_all=canvas, root=str(tmp_path), now=NOW,
    )
    assert result["quizzes"] == 1
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes/{ASSIGNMENT}",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes/{ASSIGNMENT}/items"]


def test_metadata_sync_skips_unchanged_quizzes_until_trueup(tmp_path):
    calls = []

    def canvas(path, params=None, timeout=30):
        calls.append(path)
        if path.endswith("/quizzes"):
            return ([{"id": ASSIGNMENT, "title": "Fictional Quiz", "points_possible": 10}], None)
        if path.endswith("/items"):
            return (_items(), None)
        raise AssertionError(path)

    new_quizzes.sync_metadata(COURSE, [_assignment()], canvas_get_all=canvas,
                              root=str(tmp_path), now=NOW)
    assert len(calls) == 2

    # Unchanged assignment updated_at, under the daily true-up: a delta tick
    # costs zero Canvas requests, and the metadata envelope stays current.
    result = new_quizzes.sync_metadata(COURSE, [_assignment()], canvas_get_all=canvas,
                                       root=str(tmp_path), now="2026-07-16T12:15:00Z")
    assert result == {"ok": True, "state": "current", "quizzes": 0,
                      "skipped": 1, "failures": [], "capability": "supported",
                      "skipped_restricted": False, "circuit_opened": False,
                      "circuit_cleared": False}
    assert len(calls) == 2
    envelope = new_quizzes.read_sync(COURSE, root=str(tmp_path))["metadata"]
    assert envelope["state"] == "current"
    assert envelope["last_success_at"] == "2026-07-16T12:15:00Z"

    # A changed assignment updated_at forces a refetch.
    changed = dict(_assignment(), updated_at="2026-07-16T13:00:00Z")
    new_quizzes.sync_metadata(COURSE, [changed], canvas_get_all=canvas,
                              root=str(tmp_path), now="2026-07-16T13:05:00Z")
    assert len(calls) == 4

    # Past the daily true-up age, even unchanged metadata refetches (item
    # edits inside the LTI tool may not bump the assignment's updated_at).
    new_quizzes.sync_metadata(COURSE, [changed], canvas_get_all=canvas,
                              root=str(tmp_path), now="2026-07-18T12:00:00Z")
    assert len(calls) == 6


def test_response_snapshot_does_not_downgrade_synced_quiz_metadata(tmp_path):
    rich_quiz = {
        "id": ASSIGNMENT, "title": "Rich Quiz Document", "quiz_type": "assignment",
        "time_limit": 45, "allowed_attempts": 2, "workflow_state": "published",
    }
    new_items = [{
        "id": "outer-new", "points_possible": 10,
        "entry": {"id": "essay-new", "interaction_type_slug": "essay",
                   "item_body": "New prompt."},
    }]
    new_assignment = {**_assignment(), "name": "Updated Assignment Name"}
    new_quizzes.write_quiz_metadata(
        COURSE, ASSIGNMENT, assignment=_assignment(), quiz=rich_quiz,
        items=_items(), root=str(tmp_path), attempted_at=NOW,
    )
    row = _attempt(1, "response")
    row["new_quiz_items"][0]["item_id"] = "essay-new"
    row["new_quiz_items"][0]["prompt"] = "New prompt."
    new_quizzes.write_fetch_snapshot(
        COURSE, ASSIGNMENT, assignment=new_assignment, items=new_items,
        normalized_attempts=[row], latest=[row], root=str(tmp_path), attempted_at=NOW,
    )

    document = new_quizzes.read_quiz(COURSE, ASSIGNMENT, root=str(tmp_path))
    assert document["quiz"]["title"] == "Rich Quiz Document"
    assert document["quiz"]["quiz_type"] == "assignment"
    assert document["quiz"]["time_limit"] == 45
    assert document["assignment"]["name"] == "Updated Assignment Name"
    assert set(document["items"]) == {"essay-new"}


def test_malformed_item_join_and_unmatched_attempt_are_explicit(tmp_path):
    malformed = _attempt(1, "answer")
    malformed["new_quiz_items"][0]["item_id"] = "item-not-in-catalog"
    unmatched = _attempt(2, "unmatched")
    unmatched["user_id"] = "student-unmatched"
    unmatched["new_quiz_join_state"] = "unmatched"
    unmatched["new_quiz_join_error"] = "student_submission_missing"
    result = new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[malformed, unmatched], latest=[malformed],
        root=str(tmp_path), attempted_at=NOW,
    )
    assert result["state"] == "incomplete"
    student = new_quizzes.read_student(COURSE, ASSIGNMENT, "student-synthetic", root=str(tmp_path))
    assert student["attempts"][0]["join_state"] == "incomplete"
    unmatched_doc = new_quizzes.read_student(COURSE, ASSIGNMENT, "student-unmatched", root=str(tmp_path))
    assert unmatched_doc["attempts"][0]["join_error"] == "student_submission_missing"


def test_scored_nonmanual_catalog_replacement_does_not_poison_freshness(tmp_path):
    scored_choice = _attempt(1, "Option A", result_id="result-1")
    scored_choice["new_quiz_items"] = [{
        "item_id": "choice-retired", "type": "multiple_choice",
        "prompt": "Choose.", "raw_html_answer": "Option A",
        "possible": 1, "earned_score": 0, "status": "Scored", "files": [],
    }]
    current_catalog = [{
        "id": "outer-choice", "points_possible": 1,
        "entry": {"id": "choice-current", "interaction_type_slug": "multiple_choice",
                  "item_body": "Choose."},
    }]

    result = new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=current_catalog,
        normalized_attempts=[scored_choice], root=str(tmp_path), attempted_at=NOW,
    )

    assert result["state"] == "current"
    snapshot, error = new_quizzes.read_fresh_snapshot(
        COURSE, ASSIGNMENT, root=str(tmp_path), max_age_hours=6, now=NOW,
    )
    assert error is None
    preserved = snapshot["students"][0]["new_quiz_items"][0]
    assert preserved["item_id"] == "choice-retired"
    assert preserved["earned_score"] == 0
    assert new_quizzes._is_safely_auto_scored_item(
        {"type": "multiple_choice", "earned_score": 0},
    ) is True
    assert new_quizzes._is_safely_auto_scored_item(
        {"type": "", "earned_score": 1},
    ) is False


def test_unscored_nonmanual_catalog_mismatch_remains_explicitly_incomplete(tmp_path):
    unscored = _attempt(1, "Unscored answer")
    unscored["new_quiz_items"] = [{
        "item_id": "unsupported-retired", "type": "categorization",
        "prompt": "Sort.", "raw_html_answer": "Unscored answer",
        "possible": 1, "earned_score": None, "status": "NotGraded", "files": [],
    }]

    result = new_quizzes.write_response_snapshot(
        COURSE, ASSIGNMENT, assignment=_assignment(), items=_items(),
        normalized_attempts=[unscored], root=str(tmp_path), attempted_at=NOW,
    )

    assert result["state"] == "incomplete"
    student = new_quizzes.read_student(
        COURSE, ASSIGNMENT, "student-synthetic", root=str(tmp_path),
    )
    assert student["attempts"][0]["join_error"] == "item_catalog_join_incomplete"


def test_cached_new_quiz_path_skips_core_and_report_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(new_quiz_fetch, "canvas_headers", lambda: ({"Authorization": "synthetic"}, "https://canvas.invalid"))
    monkeypatch.setattr(canvas_fetch, "canvas_get_all", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("core read")))
    monkeypatch.setattr(canvas_fetch, "canvas_get", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("assignment read")))

    cached = {
        "source": "mirror", "assignment": _assignment(),
        "students": [_attempt(2, "cached", result_id="result-cached")],
    }
    subs, assignment, error = canvas_fetch.fetch_submissions(
        COURSE, ASSIGNMENT, session_id="session-synthetic", cached_new_quiz=cached,
        new_quiz_files=False,
    )
    assert error is None and assignment["is_quiz_lti_assignment"] is True
    assert subs[0]["new_quiz_attempt"] == 2
    assert subs[0]["body"] == "cached"


# --- New Quiz capability gate (1.0beta slice 01a) ----------------------------

def _quiz_assignment(quiz_id):
    return {
        "id": quiz_id, "name": f"Fictional Quiz {quiz_id}", "description": "Explain.",
        "points_possible": 10, "published": True,
        "is_quiz_lti_assignment": True, "submission_types": ["external_tool"],
        "updated_at": "",
    }


def test_collection_403_opens_restricted_circuit_without_item_or_detail_fanout(tmp_path):
    quizzes = [_quiz_assignment("quiz-1"), _quiz_assignment("quiz-2"), _quiz_assignment("quiz-3")]
    calls = []

    def forbidden(path, params=None, timeout=30):
        calls.append(path)
        return None, "HTTP 403: Forbidden"

    result = new_quizzes.sync_metadata(COURSE, quizzes, canvas_get_all=forbidden,
                                       root=str(tmp_path), now=NOW)
    assert result["capability"] == "restricted"
    assert result["circuit_opened"] is True
    assert len(result["failures"]) == 1
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes"]

    capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    assert capability["capability"] == "restricted"
    assert capability["evidence"] == {"category": "forbidden", "consecutive_failures": 1}
    assert capability["retry_after"] > NOW

    # A later run inside the cooldown window makes zero Canvas calls at all —
    # the whole fan-out is skipped, not just individually retried.
    def explode(path, params=None, timeout=30):
        raise AssertionError(f"no Canvas call expected during cooldown: {path}")

    result2 = new_quizzes.sync_metadata(COURSE, quizzes, canvas_get_all=explode,
                                        root=str(tmp_path), now="2026-07-16T13:00:00Z")
    assert result2 == {"ok": True, "state": "unavailable", "quizzes": 0, "skipped": 0,
                       "failures": [], "capability": "restricted",
                       "skipped_restricted": True, "circuit_opened": False,
                       "circuit_cleared": False}


def test_collection_success_item_failure_stays_supported_and_incomplete(tmp_path):
    quizzes = [_quiz_assignment("quiz-1"), _quiz_assignment("quiz-2"), _quiz_assignment("quiz-3")]
    calls = []

    def mixed(path, params=None, timeout=30):
        calls.append(path)
        if path.endswith("/quizzes"):
            return ([{"id": "quiz-1", "title": "Quiz One"},
                    {"id": "quiz-2", "title": "Quiz Two"},
                    {"id": "quiz-3", "title": "Quiz Three"}], None)
        if path.endswith("/quizzes/quiz-1/items"):
            return [], None
        if path.endswith("/quizzes/quiz-2/items"):
            return None, "HTTP 403: Forbidden"
        if path.endswith("/quizzes/quiz-3/items"):
            return [], None
        raise AssertionError(path)

    result = new_quizzes.sync_metadata(COURSE, quizzes, canvas_get_all=mixed,
                                       root=str(tmp_path), now=NOW)
    assert result["capability"] == "supported"
    assert result["circuit_opened"] is False
    assert result["state"] == "incomplete"
    assert len(result["failures"]) == 1
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes/quiz-1/items",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes/quiz-2/items",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes/quiz-3/items"]
    capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    assert capability["capability"] == "supported"
    assert capability["evidence"] == {"category": "", "consecutive_failures": 0}


def test_capability_probe_after_cooldown_failure_renews_with_exactly_one_call(tmp_path):
    quizzes = [_quiz_assignment("quiz-1"), _quiz_assignment("quiz-2"), _quiz_assignment("quiz-3")]
    store.write_new_quiz_capability(
        COURSE, capability="restricted", last_probe_at="2026-07-15T12:00:00Z",
        retry_after="2026-07-16T12:00:00Z", evidence_category="forbidden",
        consecutive_failures=3, root=str(tmp_path))

    calls = []

    def forbidden(path, params=None, timeout=30):
        calls.append(path)
        return None, "HTTP 403: Forbidden"

    result = new_quizzes.sync_metadata(COURSE, quizzes, canvas_get_all=forbidden,
                                       root=str(tmp_path), now="2026-07-16T13:00:00Z")
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes"]
    assert result["circuit_opened"] is True
    capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    assert capability["capability"] == "restricted"
    assert capability["retry_after"] > "2026-07-16T12:00:00Z"


def test_capability_transient_probe_failure_does_not_renew_cooldown(tmp_path):
    quizzes = [_quiz_assignment("quiz-1"), _quiz_assignment("quiz-2"), _quiz_assignment("quiz-3")]
    expired_retry_after = "2026-07-16T12:00:00Z"
    store.write_new_quiz_capability(
        COURSE, capability="restricted", last_probe_at="2026-07-15T12:00:00Z",
        retry_after=expired_retry_after, evidence_category="forbidden",
        consecutive_failures=3, root=str(tmp_path))

    calls = []

    def timing_out(path, params=None, timeout=30):
        calls.append(path)
        return None, "Request timed out"

    # A timeout-style probe failure is transient transport noise, not
    # restriction evidence: retry_after must stay expired (unchanged).
    result = new_quizzes.sync_metadata(COURSE, quizzes, canvas_get_all=timing_out,
                                       root=str(tmp_path), now="2026-07-16T13:00:00Z")
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes"]
    assert result["circuit_opened"] is False
    capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    assert capability["capability"] == "restricted"
    assert capability["retry_after"] == expired_retry_after
    assert capability["evidence"] == {"category": "forbidden", "consecutive_failures": 3}

    # Because the cooldown is still expired, the following run probes exactly
    # once again instead of skipping the fan-out for 24h.
    result2 = new_quizzes.sync_metadata(COURSE, quizzes, canvas_get_all=timing_out,
                                        root=str(tmp_path), now="2026-07-16T13:15:00Z")
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes"]
    assert result2["skipped_restricted"] is False


def test_capability_probe_after_cooldown_success_clears_and_proceeds_normally(tmp_path):
    quizzes = [_quiz_assignment("quiz-1"), _quiz_assignment("quiz-2")]
    store.write_new_quiz_capability(
        COURSE, capability="restricted", last_probe_at="2026-07-15T12:00:00Z",
        retry_after="2026-07-16T12:00:00Z", evidence_category="forbidden",
        consecutive_failures=3, root=str(tmp_path))
    calls = []

    def succeeding(path, params=None, timeout=30):
        calls.append(path)
        if path.endswith("/quizzes"):
            return ([{"id": "quiz-1", "title": "Quiz quiz-1"},
                    {"id": "quiz-2", "title": "Quiz quiz-2"}], None)
        if path.endswith("/items"):
            return [], None
        raise AssertionError(path)

    result = new_quizzes.sync_metadata(COURSE, quizzes, canvas_get_all=succeeding,
                                       root=str(tmp_path), now="2026-07-16T13:00:00Z")
    assert result["capability"] == "supported"
    assert result["circuit_cleared"] is True
    assert result["quizzes"] == 2
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes/quiz-1/items",
                     f"/api/quiz/v1/courses/{COURSE}/quizzes/quiz-2/items"]
    capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    assert capability["capability"] == "supported"
    assert capability["retry_after"] == ""


def test_manual_collection_probe_clears_restriction_without_rewriting_fresh_metadata(tmp_path):
    assignment = _quiz_assignment("quiz-1")
    new_quizzes.write_quiz_metadata(
        COURSE, "quiz-1", assignment=assignment,
        quiz={"id": "quiz-1", "title": "Last Good Quiz"}, items=[],
        root=str(tmp_path), attempted_at=NOW,
    )
    store.write_new_quiz_capability(
        COURSE, capability="restricted", last_probe_at=NOW,
        retry_after="2099-01-01T00:00:00Z", evidence_category="forbidden",
        consecutive_failures=1, root=str(tmp_path),
    )
    calls = []

    def collection_only(path, params=None, timeout=30):
        calls.append(path)
        if path.endswith("/quizzes"):
            return [{"id": "quiz-1", "title": "Collection Title"}], None
        raise AssertionError(path)

    result = new_quizzes.sync_metadata(
        COURSE, [assignment], canvas_get_all=collection_only, root=str(tmp_path),
        now="2026-07-16T12:15:00Z", bypass_cooldown=True,
    )
    assert result["capability"] == "supported"
    assert result["circuit_cleared"] is True
    assert result["skipped"] == 1 and result["quizzes"] == 0
    assert calls == [f"/api/quiz/v1/courses/{COURSE}/quizzes"]
    quiz = new_quizzes.read_quiz(COURSE, "quiz-1", root=str(tmp_path))
    assert quiz["quiz"]["title"] == "Last Good Quiz"


def test_manual_sync_with_no_new_quiz_assignments_makes_zero_canvas_calls(tmp_path):
    def explode(path, params=None, timeout=30):
        raise AssertionError(f"empty New Quiz set must not probe: {path}")

    result = new_quizzes.sync_metadata(
        COURSE, [], canvas_get_all=explode, root=str(tmp_path), now=NOW,
        bypass_cooldown=True,
    )
    assert result == {"ok": True, "state": "current", "quizzes": 0,
                      "skipped": 0, "failures": [], "capability": "unknown",
                      "skipped_restricted": False, "circuit_opened": False,
                      "circuit_cleared": False}


def test_capability_envelope_has_no_forbidden_fields(tmp_path):
    def forbidden(path, params=None, timeout=30):
        return None, "HTTP 403: Forbidden — course concluded, contact registrar"

    new_quizzes.sync_metadata(
        COURSE, [_quiz_assignment("quiz-1"), _quiz_assignment("quiz-2"), _quiz_assignment("quiz-3")],
        canvas_get_all=forbidden, root=str(tmp_path), now=NOW,
    )
    capability = store.read_new_quiz_capability(COURSE, root=str(tmp_path))
    assert set(capability) == {"schema_version", "course_id", "capability",
                               "last_probe_at", "retry_after", "evidence"}
    assert set(capability["evidence"]) == {"category", "consecutive_failures"}
    raw = json.dumps(capability)
    assert "concluded" not in raw and "registrar" not in raw and "Forbidden" not in raw
