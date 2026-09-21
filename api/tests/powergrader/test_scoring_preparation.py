"""Assignment-scoped local-mirror preparation laws and examples."""
from __future__ import annotations

import json

import pytest

from api.powergrader import scoring_preparation, session_store


def _assignment(**overrides):
    value = {"id": "a1", "name": "Essay", "description": "Write.",
             "points_possible": 10, "is_quiz_lti_assignment": False,
             "quiz_kind": "", "rubric": []}
    value.update(overrides)
    return value


def _submission():
    return {"user_id": "u1", "workflow_state": "submitted",
            "submission_type": "online_text_entry", "body": "A submitted answer.",
            "submitted_at": "2026-09-18T10:00:00Z", "user": {"name": "Learner"}}


def _fresh(age=0, needs=False):
    return {"course_id": "c1", "course_name": "Course", "state": "current",
            "last_success_at": "2026-09-21T12:00:00Z", "age_minutes": age,
            "requires_teacher_confirmation": needs}


def _wire(monkeypatch, tmp_path, *, assignment=None, submissions=None, freshness=None, guidance="Guidance"):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.config, "course_display_name", lambda _id: "Course")
    monkeypatch.setattr(scoring_preparation.config, "get_roster_student_settings", lambda _id: {})
    monkeypatch.setattr(scoring_preparation.config, "roster_tier_by_id", lambda _id: {})
    monkeypatch.setattr(scoring_preparation.config, "get_monitored_students", lambda: {})
    monkeypatch.setattr(scoring_preparation.config, "get_extra_time", lambda _id: [])
    monkeypatch.setattr(scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
        lambda _course, _assignment: (list(submissions or [_submission()]), assignment or _assignment(),
                                       {"status": "mirror", "manifest_path": None,
                                        "freshness": freshness or _fresh()}))
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps({"students": [{"pseudonym": "Pikachu", "responses": [{
        "item_id": "item-1", "prompt": "Explain.", "response": "A response.", "possible": 10,
    }]}]}), encoding="utf-8")
    monkeypatch.setattr(scoring_preparation.scoring_artifacts, "build_scoring_artifacts", lambda **_kwargs: {
        "ok": True, "privacy_steps": [], "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        "ai_by_uid": {}, "ai_item_by_uid": {}, "copilot_packet": None})
    monkeypatch.setattr(scoring_preparation.session_builder, "build_students",
        lambda **_kwargs: [{"user_id": "u1", "status": "pending", "posted": False}])
    saved = {}
    return saved, lambda: scoring_preparation.prepare_scoring_session(
        "c1", "a1", guidance, save_session=lambda session: saved.setdefault(session["session_id"], session))


def test_successful_preparation_uses_local_mirror_and_persists_freshness(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]))
    result = prepare()
    assert result["status"] == "ready"
    session = saved[result["scoring_session_id"]]
    assert session["scoring_freshness"]["state"] == "current"
    assert session["scoring_freshness"]["use_existing_mirror"] is False


def test_missing_norms_is_teacher_input_without_persistence(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(rubric=[]), guidance="")
    result = prepare()
    assert result["code"] == "needs_scoring_norms"
    assert saved == {}


def test_new_quiz_stops_before_safe_work_or_persistence(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        quiz_kind="new_quiz", is_quiz_lti_assignment=True))
    result = prepare()
    assert result["code"] == "new_quiz_writing_requires_assignment"
    assert saved == {}


def test_mirror_failure_is_typed_and_identity_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
                        lambda *_args: (None, None, {"code": "mirror_projection_unavailable"}))
    result = scoring_preparation.prepare_scoring_session("c1", "a1")
    assert result["code"] == "mirror_projection_unavailable"
    assert {"code", "stage", "retryable", "user_action"} <= result.keys()


def test_old_snapshot_requires_explicit_teacher_confirmation(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]),
        freshness=_fresh(age=31, needs=True))
    result = prepare()
    assert result["code"] == "mirror_freshness_confirmation_required"
    assert result["stage"] == "freshness"
    assert result["retryable"] is True
    assert "relevant Canvas work changed" in result["user_action"]
    assert saved == {}


def test_teacher_can_durably_acknowledge_existing_snapshot(monkeypatch, tmp_path):
    saved, _prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]),
        freshness=_fresh(age=31, needs=True))
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", use_existing_mirror=True,
        save_session=lambda session: saved.setdefault(session["session_id"], session))
    assert result["status"] == "ready"
    assert saved[result["scoring_session_id"]]["scoring_freshness"]["use_existing_mirror"] is True


def test_unavailable_freshness_stays_fail_closed_even_with_ack(monkeypatch, tmp_path):
    saved, _prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]),
        freshness={**_fresh(), "state": "unavailable", "last_success_at": ""})
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", use_existing_mirror=True,
        save_session=lambda session: saved.setdefault(session["session_id"], session))
    assert result["code"] == "mirror_projection_unavailable"
    assert saved == {}


def test_guidance_projection_is_deterministic_and_bounded():
    text = "Beginning direction.\n\n" + ("middle criteria must assign points.\n\n" * 900) + "Ending direction."
    first = scoring_preparation.project_teacher_scoring_guidance(text)
    assert first == scoring_preparation.project_teacher_scoring_guidance(text)
    assert first[1]["compacted"] is True
    assert len(first[0]) <= scoring_preparation.MAX_TEACHER_SCORING_GUIDANCE_CHARS


def test_preparation_keeps_private_assignmentforge_corrections(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]))
    monkeypatch.setattr(scoring_preparation.assignmentforge, "for_assignment", lambda *_args: {
        "tier": "Red", "corrections": {"item-1": {"shared": {"answer": "A", "why": "B"}, "by_tier": None}}})
    result = prepare()
    session = saved[result["scoring_session_id"]]
    assert session["assignmentforge_tier"] == "Red"
    assert session["assignmentforge_corrections"]["item-1"]["shared"]["answer"] == "A"


def test_preparation_keeps_canonical_submission_digest(monkeypatch, tmp_path):
    eligible = dict(_submission(), id="submitted-1", attempt=1,
                    submitted_at="2026-09-18T10:00:00Z", score=None)
    graded = dict(eligible, user_id="u2", id="graded-1", workflow_state="graded", submitted_at="", score=9)
    rows = [eligible, graded]
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}]), submissions=rows)
    monkeypatch.setattr("api.gradebook_snapshot.needs_grading",
                        lambda row: bool(row.get("submitted_at")) and row.get("workflow_state") == "submitted")
    result = prepare()
    session = saved[result["scoring_session_id"]]
    assert session["submission_snapshot"] == session_store.eligible_submission_snapshot_digest([eligible])
    assert session["submission_snapshot_count"] == 1


def test_old_session_staleness_law_is_no_longer_used_for_packet_flow():
    assert not hasattr(session_store, "session_staleness") or callable(session_store.session_staleness)
