"""Direct gradebook aggregation laws, using synthetic records."""
import pytest

from api.gradebook_snapshot import build_snapshot, needs_grading


@pytest.mark.parametrize(("submission", "expected"), [
    ({"workflow_state": " submitted ", "submitted_at": "2026-09-01"}, True),
    ({"workflow_state": "PENDING_REVIEW", "submitted_at": "2026-09-01",
      "score": 0, "submission_comments": [{"author_role": "teacher"}]}, True),
    ({"workflow_state": "graded", "submitted_at": "2026-09-01", "score": 0}, False),
    ({"workflow_state": "submitted", "submitted_at": "2026-09-01", "excused": True}, False),
    ({"workflow_state": "submitted", "submitted_at": ""}, False),
])
def test_needs_grading_is_workflow_state_law(submission, expected):
    assert needs_grading(submission) is expected


def test_snapshot_counts_only_roster_work_and_sums_student_ungraded():
    students = [{"id": i, "name": f"Learner {i}"} for i in range(1, 7)]
    assignments = [{"id": 10, "points_possible": 10},
                   {"id": 11, "published": False}]
    subs = [
        {"user_id": 1, "workflow_state": "submitted", "submitted_at": "2026-09-01",
         "submission_comments": [{"author_role": "teacher"}]},
        {"user_id": 2, "workflow_state": "pending_review", "submitted_at": "2026-09-01",
         "score": 5},
        {"user_id": 3, "workflow_state": "graded", "score": 8},
        {"user_id": 4, "workflow_state": "graded", "score": 6},
        {"user_id": 5, "workflow_state": "submitted", "submitted_at": "2026-09-01",
         "excused": True, "missing": True, "late": True},
        {"user_id": 99, "workflow_state": "graded", "score": 0,
         "submitted_at": "2026-09-01", "missing": True, "late": True},
        {"user_id": 100, "workflow_state": "submitted", "submitted_at": "2026-09-01"},
    ]
    subs = [{"assignment_id": 10, **sub} for sub in subs]
    subs.append({"assignment_id": 11, "user_id": 6, "workflow_state": "submitted",
                 "submitted_at": "2026-09-01", "missing": True, "late": True})

    result = build_snapshot(students, assignments, subs)

    assignment, = result["assignments"]
    assert result["total_ungraded"] == sum(s["ungraded"] for s in result["students"])
    assert result["total_ungraded"] == sum(a["ungraded"] for a in result["assignments"]) == 2
    assert result["total_missing"] == 0
    assert result["class_avg"] == 70
    assert (assignment["submitted"], assignment["graded"], assignment["avg_pct"]) == (2, 2, 70)
    assert (assignment["ungraded"], assignment["partially_scored"]) == (2, 1)
    assert (assignment["missing"], assignment["late"]) == (0, 0)


def test_late_ungraded_is_exact_intersection_of_needs_grading_and_late():
    students = [{"id": 1, "name": "Learner"}]
    assignments = [{"id": 10, "points_possible": 10}]
    submissions = [
        {"assignment_id": 10, "user_id": 1, "workflow_state": "submitted",
         "submitted_at": "2026-09-01", "late": True},
        {"assignment_id": 10, "user_id": 1, "workflow_state": "graded",
         "submitted_at": "2026-09-01", "score": 8, "late": True},
        {"assignment_id": 10, "user_id": 1, "workflow_state": "submitted",
         "submitted_at": "2026-09-01", "excused": True, "late": True},
        {"assignment_id": 10, "user_id": 1, "workflow_state": "submitted",
         "submitted_at": "", "late": True},
    ]

    result = build_snapshot(students, assignments, submissions)
    assignment = result["assignments"][0]
    assert assignment["late_ungraded"] == sum(
        needs_grading(row) and bool(row.get("late")) for row in submissions
    ) == 1


def test_snapshot_marks_exact_linked_bridge_and_source_partners():
    assignments = [
        {"id": 10, "name": "Renamed support"},
        {"id": 11, "name": "Renamed core"},
        {"id": 12, "name": "Renamed bridge"},
        {"id": 13, "name": "Ordinary work"},
    ]
    link = {
        "family_title": "Reading Check",
        "source_assignment_ids": ["10", "11"],
        "bridge_assignment_id": "12",
    }
    rows = build_snapshot([], assignments, [], family_links=[link])["assignments"]
    by_id = {row["id"]: row for row in rows}
    assert [by_id[value]["family_role"] for value in ("10", "11", "12", "13")] == [
        "source", "source", "bridge", "ordinary",
    ]
    assert by_id["10"]["bridge_assignment_id"] == "12"
    assert by_id["12"]["source_assignment_ids"] == ["10", "11"]
    assert by_id["12"]["family_title"] == "Reading Check"
    assert by_id["13"]["bridge_assignment_id"] == ""
