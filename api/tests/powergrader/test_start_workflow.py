"""Direct laws for PowerGrader session-start orchestration."""

import json

import pytest

from api import feedback_artifacts
from api.powergrader import start_workflow


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


def test_mixed_new_quiz_session_keeps_only_ungraded_manual_work_in_safe_packet(
    monkeypatch, tmp_path,
):
    pending_items = [
        {"item_id": "choice-1", "type": "multiple_choice", "prompt": "Choose.",
         "raw_html_answer": "Option A", "possible": 1, "earned_score": 1},
        {"item_id": "essay-1", "type": "essay", "prompt": "Explain.",
         "raw_html_answer": "Synthetic reasoning.", "possible": 4, "earned_score": None},
        {"item_id": "formula-1", "type": "formula", "prompt": "Calculate.",
         "raw_html_answer": "x = 2", "possible": 1, "earned_score": None},
    ]
    submissions = [
        {
            "user_id": "pending-user", "user": {"name": "Pending Learner"},
            "workflow_state": "pending_review", "submitted_at": "2026-09-12T10:00:00Z",
            "submission_type": "external_tool", "score": 1,
            "new_quiz_attempt": 1, "new_quiz_items": pending_items,
        },
        {
            "user_id": "graded-user", "user": {"name": "Graded Learner"},
            "workflow_state": "graded", "submitted_at": "2026-09-12T09:00:00Z",
            "submission_type": "external_tool", "score": 5,
            "new_quiz_attempt": 1,
            "new_quiz_items": [{
                "item_id": "essay-1", "type": "essay", "prompt": "Explain.",
                "raw_html_answer": "Already graded.", "possible": 4, "earned_score": 4,
            }],
        },
    ]
    monkeypatch.setattr(start_workflow.workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(start_workflow.config, "course_display_name", lambda _course_id: "Course")
    monkeypatch.setattr(
        start_workflow.assignment_refresh,
        "refresh_assignment",
        lambda *_args, **_kwargs: (
            submissions,
            {"name": "Mixed Quiz", "points_possible": 6,
             "is_quiz_lti_assignment": True},
            {"status": "ok"},
        ),
    )
    for name in (
        "get_roster_student_settings", "roster_tier_by_id", "get_monitored_students",
    ):
        monkeypatch.setattr(start_workflow.config, name, lambda *_args: {})
    monkeypatch.setattr(start_workflow.config, "get_extra_time", lambda *_args: [])
    monkeypatch.setattr(start_workflow.writing_timeline, "is_tracked_assignment", lambda _a: False)

    safe_path = tmp_path / "bundle.json"
    captured_submitted = []

    class _Vault:
        def get_or_assign(self, canvas_id, _name="", _sis_id="", **_kwargs):
            return {"pending-user": "Pikachu", "graded-user": "Eevee"}[str(canvas_id)]

    def fake_ai_workflow(**kwargs):
        captured_submitted.extend(kwargs["submitted"])
        bundle = feedback_artifacts.pseudonymize_submissions(
            kwargs["submitted"], _Vault(), kwargs["assignment_name"],
        )
        safe_path.write_text(json.dumps(bundle), encoding="utf-8")
        return {
            "ok": True, "privacy_steps": [],
            "privacy_artifacts": {"safe_bundle": str(safe_path)},
            "ai_by_uid": {}, "ai_item_by_uid": {}, "ai_failures": {},
            "copilot_packet": None,
        }

    monkeypatch.setattr(start_workflow.ai_workflow, "run_ai_workflow", fake_ai_workflow)
    saved = {}

    result = start_workflow.run_start_session(
        course_id="course-1", assignment_id="assignment-1", mode="packet",
        watch_late="false", auto_post="false", rubric_name="", persona_id="",
        feedback_pattern_id="", model_id="", response_kind="scr", source_text="",
        source_files_json="", source_uploads=None, oral_reading_passage="",
        oral_reading_enabled="false", save_session=lambda session: saved.update(session),
        scoring_session=True, scoring_guidance="Use the teacher-scored essay only.",
    )

    assert result["ok"] is True
    assert [row["user_id"] for row in captured_submitted] == ["pending-user"]
    safe = json.loads(safe_path.read_text(encoding="utf-8"))
    assert [row["item_id"] for row in safe["students"][0]["responses"]] == ["essay-1"]
    assert [student["user_id"] for student in saved["students"]] == ["pending-user"]
    assert [item["item_id"] for item in saved["students"][0]["new_quiz_items"]] == [
        "choice-1", "essay-1", "formula-1",
    ]
    assert saved["students"][0]["new_quiz_items"][0]["earned_score"] == 1
    assert saved["students"][0]["speedgrader_required"] is True
    assert saved["new_quiz_item_finalization_supported"] is True
