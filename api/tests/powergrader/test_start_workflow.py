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


def test_scoring_guidance_length_limit_only_applies_to_teacher_guidance():
    long_rubric = "x" * (start_workflow.MAX_TEACHER_SCORING_GUIDANCE_CHARS + 1)

    assert start_workflow.scoring_guidance_length_error(
        {"source": "canvas_expert_rubric"}, long_rubric,
    ) is None
    assert start_workflow.scoring_guidance_length_error(
        {"source": "canvas_rubric"}, long_rubric,
    ) is None
    assert start_workflow.scoring_guidance_length_error(
        {"source": "teacher_guidance"}, long_rubric,
    ) == {
        "ok": False,
        "error": "Scoring guidance is too long; provide at most 12,000 characters.",
        "code": "scoring_guidance_too_long",
    }


@pytest.mark.parametrize(
    ("source", "expected_error"),
    [
        ("canvas_expert_rubric", False),
        ("canvas_rubric", False),
        ("teacher_guidance", True),
    ],
)
def test_run_start_session_applies_length_limit_only_to_teacher_guidance(
    monkeypatch, tmp_path, source, expected_error,
):
    long_text = "x" * (start_workflow.MAX_TEACHER_SCORING_GUIDANCE_CHARS + 1)
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    assignment = {"name": "Argument Essay", "points_possible": 10}
    rubric_name = ""
    scoring_guidance = ""
    if source == "canvas_rubric":
        assignment["rubric"] = [{"description": long_text, "points": 10}]
    elif source == "canvas_expert_rubric":
        rubric_name = "Long Expert Rubric"
        monkeypatch.setattr(
            start_workflow.ai_workflow.context, "load_rubric_text", lambda _name: long_text,
        )
    else:
        scoring_guidance = long_text

    monkeypatch.setattr(
        start_workflow.assignment_refresh,
        "refresh_assignment",
        lambda *_args, **_kwargs: (
            [{"submission_type": "online_text_entry", "workflow_state": "submitted",
              "submitted_at": "2026-09-12T10:00:00Z"}],
            assignment,
            {},
        ),
    )
    ai_rubric_texts = []

    def fail_after_norms_resolved(**kwargs):
        ai_rubric_texts.append(kwargs["rubric_text_override"])
        return {"ok": False, "error": "AI workflow reached", "privacy_steps": []}

    monkeypatch.setattr(start_workflow.ai_workflow, "run_ai_workflow", fail_after_norms_resolved)

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name=rubric_name, persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false", save_session=lambda _session: None,
        scoring_session=True, scoring_guidance=scoring_guidance,
    )

    if expected_error:
        assert result == {
            "ok": False,
            "payload": {
                "ok": False,
                "error": "Scoring guidance is too long; provide at most 12,000 characters.",
                "code": "scoring_guidance_too_long",
            },
        }
        assert ai_rubric_texts == []
    else:
        assert result == {
            "ok": False,
            "payload": {"ok": False, "error": "AI workflow reached", "privacy_steps": []},
        }
        assert len(ai_rubric_texts) == 1
        assert len(ai_rubric_texts[0]) > start_workflow.MAX_TEACHER_SCORING_GUIDANCE_CHARS


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
    monkeypatch.setattr(
        "api.webui.deps.list_rubric_files",
        lambda: [{"label": "Argument Writing"}],
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
            "No usable Canvas rubric is attached. Choose a Canvas Expert rubric "
            "or provide scoring guidance."
        ),
        "assignment_name": "Argument Essay",
        "rubric_labels": ["Argument Writing"],
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
    monkeypatch.setattr(
        "api.webui.deps.list_rubric_files",
        lambda: (_ for _ in ()).throw(AssertionError("scoring norms must not be requested")),
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
        "api.webui.deps.list_rubric_files",
        lambda: (_ for _ in ()).throw(AssertionError("scoring norms must not be requested")),
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

    assert result == {"status": "mirror", "manifest_path": None}
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
