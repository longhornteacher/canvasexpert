from api.powergrader.session_builder import build_students
from api.powergrader.session_actions import _payload


def test_entered_score_falls_back_to_score_plus_deduction():
    student = build_students(
        submitted=[{"user_id": "synthetic", "score": 7, "entered_score": None,
                    "points_deducted": 2, "attempt": 1, "submitted_at": "2026-09-01T12:00:00Z"}],
        ai_by_uid={}, monitored={}, extra_time_map={})[0]
    assert student["submission_baseline"]["entered_score"] == 9
    assert student["submission_baseline"]["canvas_score"] == 7


def test_no_effort_policy_keeps_fractional_entered_score():
    payload = _payload({"teacher_score": 7.5,
                        "grading": {"floor_percent": None, "points_possible": 10}})
    assert payload["submission"]["posted_grade"] == "7.5"
