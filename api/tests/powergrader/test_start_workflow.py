"""Direct laws for PowerGrader session-start orchestration."""

import pytest
from api.powergrader import start_workflow


def _mirror_scopes(monkeypatch, *, assignment, current, roster=None,
                   attempts=None, states=None):
    from api.powergrader import assignment_refresh

    states = states or {"roster": "current", "assignments": "current",
                        "submissions": "current"}
    monkeypatch.setattr(assignment_refresh.workspace, "workspace_root",
                        lambda: "C:/mirror")
    monkeypatch.setattr(assignment_refresh.read_service, "private_roster",
                        lambda *args, **kwargs: {"state": states["roster"], "records": roster or [],
                                                 "last_success_at": "2026-09-15T10:00:00Z"})
    monkeypatch.setattr(assignment_refresh.read_service, "private_assignments",
                        lambda *args, **kwargs: {"state": states["assignments"], "records": [assignment],
                                                 "last_success_at": "2026-09-15T10:00:00Z"})
    monkeypatch.setattr(assignment_refresh.read_service, "private_submissions",
                        lambda *args, **kwargs: {"state": states["submissions"], "records": [current],
                                                 "last_success_at": "2026-09-15T10:00:00Z"})
    monkeypatch.setattr(assignment_refresh.mirror_store, "read_roster",
                        lambda *args, **kwargs: {"state": "current", "students": {}, "sections": {}})
    monkeypatch.setattr(assignment_refresh.mirror_store, "read_assignments",
                        lambda *args, **kwargs: {"state": "current", "assignments": {assignment["id"]: assignment}})
    monkeypatch.setattr(assignment_refresh.mirror_store, "read_submissions",
                        lambda *args, **kwargs: {"state": "current", "submissions": {current["user_id"]: {
                            "current": current, "attempts": attempts or {"1": {
                                "attempt": 1, "submitted_at": current["submitted_at"],
                                "attachment_names": []}}
                        }}})


def test_teacher_guidance_projection_is_deterministic_and_explicit():
    long_text = "Beginning direction.\n\n" + (
        "Background material.\n\n" * 700
    ) + "Middle scoring directive: teachers must use the rubric criteria and evidence.\n\n" + (
        "More background.\n\n" * 700
    ) + "Ending direction."

    first_text, first_meta = start_workflow.project_teacher_scoring_guidance(long_text)
    second_text, second_meta = start_workflow.project_teacher_scoring_guidance(long_text)

    assert first_text == second_text
    assert first_meta == second_meta
    assert first_meta["compacted"] is True
    assert len(first_text) <= start_workflow.MAX_TEACHER_SCORING_GUIDANCE_CHARS
    assert "compacted" in first_text
    assert first_meta["effective_chars"] == len(first_text)
    assert first_meta["omitted_chars"] == len(long_text) - len(first_text)
    assert first_meta["omitted_units"] > 0
    assert "Beginning direction." in first_text
    assert "Ending direction." in first_text
    assert "must use the rubric criteria" in first_text


def test_teacher_guidance_projection_keeps_directive_near_end_of_middle_unit():
    middle = ("middle context " * 2500) + "teachers must assign points using the rubric criteria"
    text = "Beginning direction.\n\n" + middle + "\n\nEnding direction."

    effective, metadata = start_workflow.project_teacher_scoring_guidance(text)

    assert metadata["compacted"] is True
    assert len(effective) <= start_workflow.MAX_TEACHER_SCORING_GUIDANCE_CHARS
    assert "teachers must assign points using the rubric criteria" in effective


def test_short_teacher_guidance_projection_is_identity():
    text = "Score evidence clearly."
    effective, metadata = start_workflow.project_teacher_scoring_guidance(text)
    assert effective == text
    assert metadata == {
        "compacted": False,
        "original_chars": len(text),
        "effective_chars": len(text),
        "omitted_chars": 0,
        "omitted_units": 0,
    }


def test_oversized_teacher_guidance_reaches_ai_with_bounded_projection(monkeypatch, tmp_path):
    long_text = ("Beginning direction.\n\n" + "Background.\n\n" * 900 +
                 "Middle rubric directive: must score evidence.\n\n" +
                 "Ending direction.")
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    monkeypatch.setattr(
        start_workflow.assignment_refresh, "refresh_assignment",
        lambda *_args, **_kwargs: (
            [{"submission_type": "online_text_entry", "workflow_state": "submitted",
              "submitted_at": "2026-09-12T10:00:00Z"}],
            {"name": "Argument Essay", "points_possible": 10}, {},
        ),
    )
    captured = []
    monkeypatch.setattr(
        start_workflow.ai_workflow, "run_ai_workflow",
        lambda **kwargs: (captured.append(kwargs["rubric_text_override"]) or
                          {"ok": False, "error": "AI workflow reached", "privacy_steps": []}),
    )

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false", save_session=lambda _session: None,
        scoring_session=True, scoring_guidance=long_text,
    )

    assert result["payload"]["error"] == "AI workflow reached"
    assert len(captured) == 1
    assert len(captured[0]) <= start_workflow.MAX_TEACHER_SCORING_GUIDANCE_CHARS
    assert "scoring_guidance_too_long" not in str(result)


def test_oversized_teacher_guidance_keeps_complete_session_copy(monkeypatch, tmp_path):
    long_text = "BEGIN\n\n" + ("background\n\n" * 1000) + "MIDDLE must score rubric evidence\n\nEND"
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    monkeypatch.setattr(
        start_workflow.assignment_refresh, "refresh_assignment",
        lambda *_args, **_kwargs: (
            [{"user_id": "u1", "submission_type": "online_text_entry",
              "workflow_state": "submitted", "submitted_at": "2026-09-12T10:00:00Z"}],
            {"name": "Argument Essay", "points_possible": 10}, {},
        ),
    )
    monkeypatch.setattr(start_workflow.ai_workflow, "run_ai_workflow", lambda **_kwargs: {
        "ok": True, "privacy_steps": [], "privacy_artifacts": {}, "ai_by_uid": {},
        "ai_item_by_uid": {}, "ai_failures": {}, "copilot_packet": None,
    })
    monkeypatch.setattr(start_workflow.config, "get_roster_student_settings", lambda _id: {})
    monkeypatch.setattr(start_workflow.config, "roster_tier_by_id", lambda _id: {})
    monkeypatch.setattr(start_workflow.config, "get_monitored_students", lambda: {})
    monkeypatch.setattr(start_workflow.config, "get_extra_time", lambda _id: [])
    monkeypatch.setattr(start_workflow.session_builder, "build_students", lambda **_kwargs: [])
    monkeypatch.setattr(start_workflow, "build_start_success_payload", lambda **_kwargs: {"ok": True})
    saved = {}
    monkeypatch.setattr(start_workflow, "build_start_session", lambda **kwargs: {
        "session_id": kwargs["session_id"], "students": [],
    })

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false", save_session=lambda session: saved.update(session),
        scoring_session=True, scoring_guidance=long_text,
    )

    assert result["ok"] is True
    assert saved["scoring_rubric_text"] == long_text
    assert saved["effective_scoring_rubric_text"] != long_text
    projection = saved["scoring_guidance_projection"]
    assert projection["compacted"] is True
    assert projection["effective_chars"] == len(saved["effective_scoring_rubric_text"])
    assert projection["omitted_chars"] == len(long_text) - projection["effective_chars"]


def test_missing_scoring_norms_names_the_resolved_assignment(monkeypatch, tmp_path):
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    monkeypatch.setattr(
        start_workflow.assignment_refresh,
        "refresh_assignment",
        lambda *_args, **_kwargs: (
            [{"submission_type": "online_text_entry", "workflow_state": "submitted",
              "submitted_at": "2026-09-12T10:00:00Z"}],
            {"name": "Argument Essay", "points_possible": 10},
            {},
        ),
    )
    result = start_workflow.run_start_session(
        course_id="course-1",
        assignment_id="assignment-1",
        mode="packet",
        watch_late="false",
        auto_post="false",
        rubric_name="",
        persona_id="",
        feedback_pattern_id="",
        model_id="",
        response_kind="scr",
        source_text="",
        source_files_json="",
        source_uploads=None,
        oral_reading_passage="",
        oral_reading_enabled="false",
        save_session=lambda _session: None,
        scoring_session=True,
        scoring_guidance="",
    )

    assert result["ok"] is False
    assert result["payload"] == {
        "ok": False,
        "code": "needs_scoring_norms",
        "error": (
            "No usable Canvas rubric is attached. Provide bounded scoring guidance "
            "for this assignment."
        ),
        "assignment_name": "Argument Essay",
    }


def test_refreshed_graded_only_assignment_returns_nothing_to_grade_before_norms(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    monkeypatch.setattr(
        start_workflow.assignment_refresh,
        "refresh_assignment",
        lambda *_args, **_kwargs: (
            [{"submission_type": "online_text_entry", "workflow_state": "graded",
              "submitted_at": "2026-09-12T10:00:00Z", "score": 0}],
            {"name": "Resolved Essay", "points_possible": 10},
            {},
        ),
    )
    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false",
        save_session=lambda _session: (_ for _ in ()).throw(
            AssertionError("session must not be saved")
        ),
        scoring_session=True, scoring_guidance="",
    )

    assert result == {
        "ok": False,
        "payload": {
            "ok": False,
            "code": "nothing_to_grade",
            "assignment_name": "Resolved Essay",
        },
    }


def test_empty_acquisition_remains_distinct_from_nothing_to_grade(monkeypatch, tmp_path):
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    monkeypatch.setattr(
        start_workflow.assignment_refresh,
        "refresh_assignment",
        lambda *_args, **_kwargs: ([], {"name": "Unavailable Essay"}, {}),
    )

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false", save_session=lambda _session: None,
        scoring_session=True, scoring_guidance="",
    )

    assert result == {
        "ok": False,
        "payload": {
            "ok": False,
            "error": "No submissions found for this assignment.",
            "privacy_steps": [],
        },
    }


def test_new_quiz_writing_stops_before_norms_packet_or_write(monkeypatch, tmp_path):
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    monkeypatch.setattr(
        start_workflow.assignment_refresh,
        "refresh_assignment",
        lambda *_args, **_kwargs: (
            [{
                "user_id": "private-user",
                "user": {"name": "Private Learner"},
                "workflow_state": "pending_review",
                "submitted_at": "2026-09-12T10:00:00Z",
                "submission_type": "external_tool",
                "new_quiz_attempt": 1,
                "new_quiz_items": [{
                    "item_id": "essay-1",
                    "type": "essay",
                    "raw_html_answer": "Private response text.",
                    "possible": 100,
                    "earned_score": None,
                }],
            }],
            {
                "name": "Mixed Quiz",
                "points_possible": 100,
                "is_quiz_lti_assignment": True,
            },
            {"status": "ok"},
        ),
    )
    monkeypatch.setattr(
        start_workflow.ai_workflow,
        "run_ai_workflow",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("SAFE packet work must not run")),
    )

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false",
        save_session=lambda _session: (_ for _ in ()).throw(
            AssertionError("unsupported New Quiz session must not be saved")
        ),
        scoring_session=True, scoring_guidance="",
    )

    assert result == {
        "ok": False,
        "payload": {
            "ok": False,
            "code": "new_quiz_writing_requires_assignment",
            "error": (
                "Grade this New Quiz writing in Canvas. For future assessments, "
                "author each writing portion as a separate 100-point AssignmentForge assignment."
            ),
            "assignment_name": "Mixed Quiz",
        },
    }
    serialized = str(result)
    assert "private-user" not in serialized
    assert "Private Learner" not in serialized
    assert "Private response text." not in serialized


def test_mirror_preparation_returns_text_without_invoking_canvas_or_evidence(monkeypatch):
    from api.powergrader import assignment_refresh

    assignment = {
        "id": "assignment-1", "name": "Essay", "description_text": "Write.",
        "points_possible": 10, "quiz_id": "", "is_quiz": False,
        "quiz_kind": "", "is_quiz_lti_assignment": False,
    }
    current = {
        "user_id": "student-1", "assignment_id": "assignment-1",
        "workflow_state": "submitted",
        "submitted_at": "2026-09-15T10:00:00Z", "graded_at": None,
        "score": None, "grade": None, "late": False, "missing": False,
        "excused": False, "attempt": 1, "submission_type": "online_text_entry",
        "body": "A private response.", "url": "",
    }
    _mirror_scopes(monkeypatch, assignment=assignment, current=current,
                   roster=[{"id": "student-1", "name": "Learner"}])
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "fetch_submissions",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("mirror preparation must not fetch Canvas")))
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "ingest_ordinary_attachments",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("mirror preparation must not download attachments")))

    rows, prepared, result = assignment_refresh.prepare_assignment_from_mirror(
        "course-1", "assignment-1")

    assert result == {"status": "mirror", "manifest_path": None,
                      "historical_only": False}
    assert prepared["description"] == "Write."
    assert rows[0]["body"] == "A private response."
    assert rows[0]["user"]["id"] == "student-1"
    assert rows[0]["attachments"] == []


def test_mirror_preparation_refuses_stale_state_with_refresh_instruction(monkeypatch):
    from api.powergrader import assignment_refresh

    assignment = {
        "id": "assignment-1", "name": "Essay", "description_text": "Write.",
        "points_possible": 10, "quiz_id": "", "is_quiz": False,
        "quiz_kind": "", "is_quiz_lti_assignment": False,
    }
    current = {"user_id": "student-1"}
    _mirror_scopes(monkeypatch, assignment=assignment, current=current,
                   states={"roster": "stale", "assignments": "current",
                           "submissions": "current"})
    monkeypatch.setattr(assignment_refresh.canvas_fetch, "fetch_submissions",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("stale mirror must not fetch Canvas")))

    rows, prepared, result = assignment_refresh.prepare_assignment_from_mirror(
        "course-1", "assignment-1")

    assert rows is None and prepared is None
    assert result["error"] == assignment_refresh.MIRROR_PREPARATION_ERROR


@pytest.mark.parametrize("stale_document", ["roster", "assignment", "submission"])
def test_mirror_preparation_requires_each_target_document_current(monkeypatch, stale_document):
    from api.powergrader import assignment_refresh

    assignment = {
        "id": "assignment-1", "name": "Essay", "description_text": "Write.",
        "points_possible": 10, "quiz_id": "", "is_quiz": False,
        "quiz_kind": "", "is_quiz_lti_assignment": False,
    }
    current = {
        "user_id": "student-1", "assignment_id": "assignment-1",
        "workflow_state": "submitted", "submitted_at": "2026-09-15T10:00:00Z",
        "graded_at": None, "score": None, "grade": None, "late": False,
        "missing": False, "excused": False, "attempt": 1,
        "submission_type": "online_text_entry", "body": "Response.", "url": "",
    }
    _mirror_scopes(monkeypatch, assignment=assignment, current=current,
                   roster=[{"id": "student-1", "name": "Learner"}])
    if stale_document == "roster":
        monkeypatch.setattr(assignment_refresh.mirror_store, "read_roster",
                            lambda *args, **kwargs: {"state": "stale"})
    elif stale_document == "assignment":
        monkeypatch.setattr(assignment_refresh.mirror_store, "read_assignments",
                            lambda *args, **kwargs: {"state": "stale"})
    else:
        monkeypatch.setattr(assignment_refresh.mirror_store, "read_submissions",
                            lambda *args, **kwargs: {"state": "stale"})

    rows, prepared, result = assignment_refresh.prepare_assignment_from_mirror(
        "course-1", "assignment-1")

    assert rows is None and prepared is None
    assert result["error"] == assignment_refresh.MIRROR_PREPARATION_ERROR


@pytest.mark.parametrize(
    ("is_quiz", "quiz_kind"),
    [(False, "quiz"), (False, "new_quiz"), (True, ""), (True, "quiz")],
)
def test_mirror_preparation_refuses_ambiguous_quiz_classification(
    monkeypatch, is_quiz, quiz_kind,
):
    from api.powergrader import assignment_refresh

    assignment = {
        "id": "assignment-1", "name": "Essay", "description_text": "Write.",
        "points_possible": 10, "quiz_id": "", "is_quiz": is_quiz,
        "quiz_kind": quiz_kind, "is_quiz_lti_assignment": False,
    }
    current = {"user_id": "student-1", "assignment_id": "assignment-1"}
    _mirror_scopes(monkeypatch, assignment=assignment, current=current,
                   roster=[{"id": "student-1", "name": "Learner"}])

    rows, prepared, result = assignment_refresh.prepare_assignment_from_mirror(
        "course-1", "assignment-1")

    assert rows is None and prepared is None
    assert result["error"] == assignment_refresh.MIRROR_PREPARATION_ERROR


def test_mirror_preparation_refuses_submission_from_another_assignment(monkeypatch):
    from api.powergrader import assignment_refresh

    assignment = {
        "id": "assignment-1", "name": "Essay", "description_text": "Write.",
        "points_possible": 10, "quiz_id": "", "is_quiz": False,
        "quiz_kind": "", "is_quiz_lti_assignment": False,
    }
    current = {
        "user_id": "student-1", "assignment_id": "other-assignment",
        "workflow_state": "submitted", "submitted_at": "2026-09-15T10:00:00Z",
        "graded_at": None, "score": None, "grade": None, "late": False,
        "missing": False, "excused": False, "attempt": 1,
        "submission_type": "online_text_entry", "body": "Response.", "url": "",
    }
    _mirror_scopes(monkeypatch, assignment=assignment, current=current,
                   roster=[{"id": "student-1", "name": "Learner"}])

    rows, prepared, result = assignment_refresh.prepare_assignment_from_mirror(
        "course-1", "assignment-1")

    assert rows is None and prepared is None
    assert result["error"] == assignment_refresh.MIRROR_PREPARATION_ERROR


def _ordinary_assignment():
    return {
        "id": "assignment-1", "name": "Essay", "description_text": "Write.",
        "points_possible": 10, "quiz_id": "", "is_quiz": False,
        "quiz_kind": "", "is_quiz_lti_assignment": False,
    }


def _orphan_current(**overrides):
    current = {
        "user_id": "ghost", "assignment_id": "assignment-1",
        "workflow_state": "submitted", "submitted_at": "2026-09-15T10:00:00Z",
        "graded_at": None, "score": None, "grade": None, "late": False,
        "missing": False, "excused": False, "attempt": 1,
        "submission_type": "online_text_entry", "body": "Text.", "url": "",
    }
    current.update(overrides)
    return current


def test_mirror_preparation_ignores_only_historical_orphan_row(monkeypatch):
    from api.powergrader import assignment_refresh

    current = _orphan_current(workflow_state="graded", score=7, grade="7",
                              submitted_at="", body="")
    _mirror_scopes(monkeypatch, assignment=_ordinary_assignment(),
                   current=current, roster=[{"id": "student-1", "name": "Learner"}])

    rows, prepared, result = assignment_refresh.prepare_assignment_from_mirror(
        "course-1", "assignment-1")

    assert rows == []
    assert result["status"] == "mirror"
    assert result["historical_only"] is True


@pytest.mark.parametrize("case", [
    "submitted", "pending_review", "unscored_graded", "graded_with_submitted",
    "graded_excused_no_score", "blank_user",
])
def test_mirror_preparation_fails_closed_on_unmatched_rows(monkeypatch, case):
    from api.powergrader import assignment_refresh

    overrides = {
        "submitted": {},
        "pending_review": {"workflow_state": "pending_review"},
        "unscored_graded": {"workflow_state": "graded", "submitted_at": ""},
        "graded_with_submitted": {"workflow_state": "graded", "score": 5},
        "graded_excused_no_score": {"workflow_state": "graded", "submitted_at": "",
                                    "excused": True},
        "blank_user": {"user_id": ""},
    }[case]
    _mirror_scopes(monkeypatch, assignment=_ordinary_assignment(),
                   current=_orphan_current(**overrides),
                   roster=[{"id": "student-1", "name": "Learner"}])

    rows, prepared, result = assignment_refresh.prepare_assignment_from_mirror(
        "course-1", "assignment-1")

    assert rows is None and prepared is None
    assert result["code"] == assignment_refresh.MIRROR_SUBMISSION_IDENTITY_MISMATCH_CODE
    assert result["error"] == assignment_refresh.MIRROR_SUBMISSION_IDENTITY_MISMATCH
    assert "ghost" not in str(result)


def test_historical_only_mirror_returns_nothing_to_grade(monkeypatch, tmp_path):
    current = _orphan_current(workflow_state="graded", score=7, grade="7",
                              submitted_at="", body="")
    _mirror_scopes(monkeypatch, assignment=_ordinary_assignment(),
                   current=current, roster=[{"id": "student-1", "name": "Learner"}])
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _id: "Course")

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false", save_session=lambda _s: None,
        scoring_session=True, scoring_guidance="", mirror_only=True,
    )

    assert result == {"ok": False, "payload": {
        "ok": False, "code": "nothing_to_grade", "assignment_name": "Essay",
    }}


def test_mirror_identity_mismatch_stops_before_any_ai_work(monkeypatch, tmp_path):
    _mirror_scopes(monkeypatch, assignment=_ordinary_assignment(),
                   current=_orphan_current(),
                   roster=[{"id": "student-1", "name": "Learner"}])
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _id: "Course")
    monkeypatch.setattr(start_workflow.ai_workflow, "run_ai_workflow",
                        lambda **_kw: (_ for _ in ()).throw(
                            AssertionError("mismatch must stop before SAFE work")))

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false", save_session=lambda _s: None,
        scoring_session=True, scoring_guidance="", mirror_only=True,
    )

    assert result["ok"] is False
    assert result["payload"]["code"] == "mirror_submission_identity_mismatch"
    assert "ghost" not in str(result)
