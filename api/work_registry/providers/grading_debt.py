"""Cross-course grading-debt discovery with aggregate-only output."""

from __future__ import annotations

from collections import defaultdict

from api.gradebook_snapshot import needs_grading

from . import (
    CourseTimeout,
    DiscoveryDeadline,
    ProviderFailure,
    WorkCourseReads,
    check_deadline,
    finding,
    text,
)


def _number(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_assignment_map(assignments: list[dict]) -> dict[str, dict]:
    return {
        text(item.get("id")): item
        for item in assignments
        if isinstance(item, dict) and text(item.get("id"))
    }


def scan_course(course_id: str, *, now, reads: WorkCourseReads) -> list[dict]:
    """Find work whose Canvas workflow state still requires grading."""
    check_deadline(reads._deadline)
    assignments = reads.assignments()
    submissions = reads.submissions(include_comments=True)
    assignment_map = _normalize_assignment_map(assignments)
    aggregates: dict[str, dict] = defaultdict(lambda: {
        "total": 0,
        "pending": 0,
        "affected": 0,
        "latest_submitted_at": "",
        "latest_attempt_number": 0,
        "due_at": "",
    })
    for submission in submissions:
        if not isinstance(submission, dict):
            continue
        submitted_at = text(submission.get("submitted_at"))
        if not needs_grading(submission):
            continue
        assignment_id = text(submission.get("assignment_id"))
        assignment = assignment_map.get(assignment_id)
        if not assignment_id or assignment is None:
            continue
        row = aggregates[assignment_id]
        row["total"] += 1
        attempt = max(
            _number(submission.get("attempt"), _number(submission.get("submission_attempt"))),
            0,
        )
        if submitted_at > row["latest_submitted_at"]:
            row["latest_submitted_at"] = submitted_at
        row["latest_attempt_number"] = max(row["latest_attempt_number"], attempt)
        row["due_at"] = text(assignment.get("due_at"))
        row["pending"] += 1
        row["affected"] += 1
    output = []
    for assignment_id in sorted(aggregates):
        row = aggregates[assignment_id]
        if not row["pending"]:
            continue
        output.append(finding(
            kind="grade.debt",
            course_id=str(course_id),
            assignment_id=assignment_id,
            counts={key: row[key] for key in ("total", "pending", "affected")},
            now=now,
            latest_submitted_at=row["latest_submitted_at"],
            latest_attempt_number=row["latest_attempt_number"],
            due_at=row["due_at"],
            resumable_url="/gradebook",
        ))
    return output


__all__ = ["scan_course"]
