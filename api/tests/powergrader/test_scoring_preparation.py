"""Assignment-scoped preparation laws and examples."""
from __future__ import annotations

import json

import pytest

from api.powergrader import scoring_preparation
from api.powergrader import session_store


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
    monkeypatch.setattr(scoring_preparation.scoring_artifacts, "build_scoring_artifacts", lambda **_kwargs: {
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


def _wire_with_activation(monkeypatch, tmp_path, *, assignment=None, guidance="Guidance"):
    """Wire preparation through an injected lifecycle-activation seam."""
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
            [_submission()], assignment or _assignment(),
            {"status": "mirror", "manifest_path": None},
        ),
    )
    bundle_path = tmp_path / "safe-bundle.json"
    bundle_path.write_text(json.dumps({"students": [{"pseudonym": "Pikachu", "responses": [{
        "item_id": "item-1", "prompt": "Explain.", "response": "A response.", "possible": 10,
    }]}]}), encoding="utf-8")
    monkeypatch.setattr(scoring_preparation.scoring_artifacts, "build_scoring_artifacts", lambda **_kwargs: {
        "ok": True, "privacy_steps": [], "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        "ai_by_uid": {}, "ai_item_by_uid": {}, "copilot_packet": None,
    })
    monkeypatch.setattr(scoring_preparation.session_builder, "build_students", lambda **_kwargs: [{
        "user_id": "u1", "status": "pending", "posted": False,
    }])
    activated = []
    saved = {}

    def activate(session):
        activated.append(session["session_id"])
        saved.setdefault(session["session_id"], session)
        return []

    return saved, activated, lambda: scoring_preparation.prepare_scoring_session(
        "c1", "a1", guidance, refresh_course=lambda _course: True,
        save_session=lambda session: saved.setdefault(session["session_id"], session),
        activate_session=activate,
    )


def test_successful_preparation_activates_through_the_lifecycle_owner(monkeypatch, tmp_path):
    saved, activated, prepare = _wire_with_activation(
        monkeypatch, tmp_path, assignment=_assignment(
            rubric=[{"description": "Reasoning", "points": 10, "ratings": []}],
        ))
    result = prepare()

    assert result["ok"] is True
    assert activated == [result["scoring_session_id"]]
    assert set(saved) == set(activated)


def test_successful_preparation_defaults_to_the_store_lifecycle_owner(monkeypatch, tmp_path):
    """Production preparation cannot bypass activation."""
    _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}],
    ))
    calls = []

    def activate(session, **_kwargs):
        calls.append(session["session_id"])
        return ["older"]

    monkeypatch.setattr(scoring_preparation.session_store, "activate_scoring_session", activate)
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Guidance", refresh_course=lambda _course: True,
        save_session=lambda _session: pytest.fail("activation must own the save"),
    )

    assert result["ok"] is True
    assert calls == [result["scoring_session_id"]]


def test_basis_stage_teacher_input_never_activates_or_saves(monkeypatch, tmp_path):
    saved, activated, prepare = _wire_with_activation(
        monkeypatch, tmp_path, assignment=_assignment(rubric=[]), guidance="")
    result = prepare()

    assert result["code"] == "needs_scoring_norms"
    assert result["stage"] == "basis"
    assert activated == []
    assert saved == {}


@pytest.mark.parametrize("assignment, submissions, code", [
    ({"quiz_kind": "new_quiz", "is_quiz_lti_assignment": True}, None,
     "new_quiz_writing_requires_assignment"),
    ({"rubric": [{"description": "Reasoning", "points": 10, "ratings": []}]}, [],
     "nothing_to_grade"),
])
def test_typed_blockers_never_activate_a_session(monkeypatch, tmp_path, assignment,
                                                 submissions, code):
    saved, activated, _prepare = _wire_with_activation(
        monkeypatch, tmp_path, assignment=_assignment(**assignment))
    if submissions is not None:
        monkeypatch.setattr(
            scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
            lambda _c, _a: ([], _assignment(**assignment), {"status": "mirror"}))

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Guidance", refresh_course=lambda _course: True,
        save_session=lambda session: saved.setdefault(session["session_id"], session),
        activate_session=lambda session: activated.append(session["session_id"]) or [])

    assert result["code"] == code
    assert activated == []
    assert saved == {}


def test_mirror_failure_never_activates_a_session(monkeypatch, tmp_path):
    saved, activated, _prepare = _wire_with_activation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
        lambda _c, _a: (None, None, {"code": "mirror_projection_unavailable"}))

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: True,
        save_session=lambda session: saved.setdefault(session["session_id"], session),
        activate_session=lambda session: activated.append(session["session_id"]) or [])

    assert result["ok"] is False
    assert result["code"] == "mirror_projection_unavailable"
    assert activated == []
    assert saved == {}


def test_refresh_failure_never_activates_a_session(monkeypatch, tmp_path):
    saved, activated, _prepare = _wire_with_activation(monkeypatch, tmp_path)

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: False,
        save_session=lambda session: saved.setdefault(session["session_id"], session),
        activate_session=lambda session: activated.append(session["session_id"]) or [])

    assert result["code"] == "mirror_refresh_failed"
    assert activated == []


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


def test_preparation_keeps_private_assignmentforge_corrections_on_the_session(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}],
    ))
    monkeypatch.setattr(scoring_preparation.assignmentforge, "for_assignment", lambda *_args: {
        "tier": "Red",
        "corrections": {"item-1": {"shared": {"answer": "A", "why": "B"}, "by_tier": None}},
    })

    result = prepare()

    session = saved[result["scoring_session_id"]]
    assert session["assignmentforge_tier"] == "Red"
    assert session["assignmentforge_corrections"]["item-1"]["shared"]["answer"] == "A"


def test_mirror_failure_is_typed_and_identity_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(scoring_preparation.assignment_refresh, "prepare_assignment_from_mirror",
                        lambda *_args: (None, None, {"code": "mirror_submission_row_invalid"}))
    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: True,
    )
    assert result["code"] == "mirror_submission_row_invalid"
    assert {"code", "stage", "retryable", "user_action"} <= result.keys()


# ── Scoring-refresh failure facts ───────────────────────────────────────────

def test_scoring_refresh_failure_preserves_safe_lifecycle_facts(monkeypatch, tmp_path):
    """CONTRACT: a failed scoring refresh keeps its own safe typed facts.

    The runner's stable code and opaque lifecycle identifiers survive instead
    of flattening to one generic message. Nothing private crosses the boundary.
    """
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: {
            "ok": False, "usable": False, "state": "failed",
            "error_code": "mirror_scope_unavailable",
            "operation_id": "opaque-operation",
            "mirror_revision": 7,
            "snapshot_id": "snap-1",
        },
    )

    assert result["ok"] is False
    assert result["code"] == "mirror_scope_unavailable"
    assert result["stage"] == "refresh"
    assert result["retryable"] is True
    assert result["operation_id"] == "opaque-operation"
    assert result["mirror_revision"] == 7
    assert result["snapshot_id"] == "snap-1"
    assert result["refresh_state"] == "failed"


def test_scoring_refresh_failure_without_a_code_stays_generic(monkeypatch, tmp_path):
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: False,
    )

    assert result["code"] == "mirror_refresh_failed"
    assert result["stage"] == "refresh"
    assert "operation_id" not in result


def test_scoring_refresh_failure_never_forwards_arbitrary_exception_text(monkeypatch, tmp_path):
    """CONTRACT: detailed cause is private operational diagnostics only."""
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))
    private = "C:\\Users\\teacher\\OneDrive\\CanvasExpert\\private\\roster.json"

    def explode(_course):
        raise RuntimeError(private)

    result = scoring_preparation.prepare_scoring_session("c1", "a1", refresh_course=explode)

    assert result["code"] == "mirror_refresh_failed"
    assert private not in str(result)
    assert "RuntimeError" not in str(result)


def test_ordinary_refresh_success_is_not_evidence_of_a_scoring_refresh(monkeypatch, tmp_path):
    """CONTRACT: the two refreshes are distinct scopes.

    ``refresh_mirror`` uses ordinary course/roster/group scopes; scoring uses
    the ``course.scoring_refresh`` full rebuild. A runner that reports success
    without a usable scoring revision must not be treated as refreshed.
    """
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: str(tmp_path))

    result = scoring_preparation.prepare_scoring_session(
        "c1", "a1", refresh_course=lambda _course: {
            "ok": True, "usable": False, "state": "succeeded",
            "error_code": "scoring_revision_unusable",
        },
    )

    assert result["ok"] is False
    assert result["stage"] == "refresh"
    assert result["code"] == "scoring_revision_unusable"


def test_preparation_binds_mirror_revision_and_submission_snapshot(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}],
    ))
    monkeypatch.setattr(scoring_preparation.assignment_refresh,
                        "prepare_assignment_from_mirror",
                        lambda _c, _a: ([_submission()], _assignment(rubric=[
                            {"description": "Reasoning", "points": 10, "ratings": []},
                        ]), {"status": "mirror", "mirror_revision": "r7"}))
    result = prepare()
    session = saved[result["scoring_session_id"]]
    assert session["mirror_revision"] == "r7"
    assert session["submission_snapshot"]


def test_refresh_retry_reuses_private_norms_guidance(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(rubric=[]))
    calls = iter([False, True])
    first = scoring_preparation.prepare_scoring_session(
        "c1", "a1", "Use evidence.", refresh_course=lambda _course: next(calls),
        save_session=lambda session: saved.setdefault(session["session_id"], session),
    )
    assert first["code"] == "mirror_refresh_failed"
    second = prepare()
    assert second["status"] == "ready"


def test_ready_payload_reports_missing_packet(monkeypatch, tmp_path):
    saved, prepare = _wire(monkeypatch, tmp_path, assignment=_assignment(
        rubric=[{"description": "Reasoning", "points": 10, "ratings": []}],
    ))
    result = prepare()
    session = saved[result["scoring_session_id"]]
    session["privacy_artifacts"]["safe_bundle"] = str(tmp_path / "gone.json")
    assert scoring_preparation._ready_payload(session)["code"] == "packet_missing"


def test_newer_submission_invalidates_the_frozen_snapshot():
    original = [{"user_id": "u1", "id": "s1", "attempt": 1,
                 "submitted_at": "2026-09-18T10:00:00Z", "workflow_state": "submitted"}]
    session = {"mirror_revision": "r1",
               "submission_snapshot": session_store.submission_snapshot_digest(original)}
    newer = [dict(original[0], id="s2", attempt=2,
                  submitted_at="2026-09-18T11:00:00Z")]
    result = session_store.session_staleness(
        session, mirror_revision="r1", submission_snapshot=newer)
    assert result == {"stale": True, "code": "submission_identity_mismatch",
                      "reason": "submission_snapshot_changed"}
