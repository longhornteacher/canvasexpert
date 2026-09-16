"""Assignment-scoped preparation laws and examples."""
from __future__ import annotations

import json

from api.powergrader import scoring_preparation


def _assignment(**overrides):
    value = {
        "id": "a1", "name": "Essay", "description": "Write.",
        "points_possible": 10, "is_quiz_lti_assignment": False,
        "quiz_kind": "", "rubric": [],
    }
    value.update(overrides)
    return value


def _submission():
    return {
        "user_id": "u1", "workflow_state": "submitted", "submission_type": "online_text_entry",
        "body": "A submitted answer.", "user": {"name": "Learner"},
    }


def _wire(monkeypatch, tmp_path, *, assignment=None, submissions=None):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.config, "course_display_name", lambda _id: "Course")
    monkeypatch.setattr(scoring_preparation.config, "get_roster_student_settings", lambda _id: {})
    monkeypatch.setattr(scoring_preparation.config, "roster_tier_by_id", lambda _id: {})
    monkeypatch.setattr(scoring_preparation.config, "get_monitored_students", lambda: {})
    monkeypatch.setattr(scoring_preparation.config, "get_extra_time", lambda _id: [])
    monkeypatch.setattr(scoring_preparation.gradebook_snapshot, "needs_grading", lambda _row: True)
    monkeypatch.setattr(
        scoring_preparation.assignment_refresh,
        "prepare_assignment_from_mirror",
        lambda _course, _assignment: (
            list(submissions or [_submission()]), assignment or _assignment(),
            {"status": "mirror", "manifest_path": None},
        ),
    )
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps({"students": [{"pseudonym": "Pikachu", "responses": [{
        "item_id": "item-1", "prompt": "Explain.", "response": "A response.", "possible": 10,
    }]}]}), encoding="utf-8")
    monkeypatch.setattr(scoring_preparation.ai_workflow, "run_ai_workflow", lambda **_kwargs: {
        "ok": True, "privacy_steps": [], "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        "ai_by_uid": {}, "ai_item_by_uid": {}, "copilot_packet": None,
    })
    monkeypatch.setattr(scoring_preparation.session_builder, "build_students", lambda **_kwargs: [{
        "user_id": "u1", "status": "pending", "posted": False,
    }])
    saved = {}
    return saved, lambda: scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Guidance", refresh_course=lambda _course: True,
        save_session=lambda session: saved.setdefault(session["session_id"], session),
    )


def test_guidance_projection_is_deterministic_and_bounded():
    text = "Beginning direction.\n\n" + ("middle criteria must assign points.\n\n" * 900) + "Ending direction."
    first = scoring_preparation.project_teacher_scoring_guidance(text)
    second = scoring_preparation.project_teacher_scoring_guidance(text)
    assert first == second
    assert first[1]["compacted"] is True
    assert len(first[0]) <= scoring_preparation.MAX_TEACHER_SCORING_GUIDANCE_CHARS


def test_new_quiz_stops_before_norms_safe_work_or_persistence(monkeypatch, tmp_path):
    saved, _ = _wire(monkeypatch, tmp_path, assignment=_assignment(
        quiz_kind="new_quiz", is_quiz_lti_assignment=True,
    ))
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: True,
        save_session=lambda session: saved.setdefault(session["session_id"], session),
    )
    assert result["code"] == "new_quiz_writing_requires_assignment"
    assert result["stage"] == "classify"
    assert result["retryable"] is False
    assert saved == {}


def test_missing_norms_is_teacher_input_without_a_saved_session(monkeypatch, tmp_path):
    saved, _ = _wire(monkeypatch, tmp_path, assignment=_assignment(rubric=[]))
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: True,
        save_session=lambda session: saved.setdefault(session["session_id"], session),
    )
    assert result["ok"] is True
    assert result["status"] == "needs_teacher_input"
    assert result["code"] == "needs_scoring_norms"
    assert saved == {}


def test_ordinary_text_preparation_saves_one_assignment_session(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}],
    ))
    result = prepare()
    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["scoring_session_id"] in saved
    session = saved[result["scoring_session_id"]]
    assert session["session_kind"] == "scoring_assignment"
    assert all("parent" not in key for key in session)


def test_mirror_failure_is_typed_and_identity_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
                        lambda *_args: (None, None, {"code": "mirror_submission_row_invalid"}))
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: True,
    )
    assert result["code"] == "mirror_submission_row_invalid"
    assert {"code", "stage", "retryable", "user_action"} <= result.keys()
