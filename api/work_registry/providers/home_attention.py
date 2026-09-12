"""Aggregate-only Home attention reductions over shared discovery reads."""

from __future__ import annotations

from collections import defaultdict

from . import as_datetime, WorkCourseReads, check_deadline, finding, text


_TA_MARKER = "TA SCORE + FEEDBACK"
PROVEN_STAFF_ROLES = {
    "admin", "administrator", "instructor", "staff", "ta", "teacher",
    "teaching_assistant",
}


def _number(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _assignment_map(assignments: list[dict]) -> dict[str, dict]:
    return {
        text(assignment.get("id")): assignment
        for assignment in assignments
        if isinstance(assignment, dict) and text(assignment.get("id"))
    }


def _author_id(comment: dict) -> str:
    author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
    direct = text(comment.get("author_id"))
    nested = text(author.get("id"))
    if direct and nested and direct != nested:
        return ""
    return direct or nested


def author_role(comment: dict) -> str:
    author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
    return text(
        comment.get("author_role") or comment.get("author_type")
        or author.get("role") or author.get("type")
    ).casefold()


def _ordered_comments(submission: dict) -> list[tuple[object, dict, str]] | None:
    """Return uniquely ordered comments only when identity and order are provable."""
    raw_comments = submission.get("submission_comments")
    if not isinstance(raw_comments, list):
        return None
    ordered = []
    seen_times = set()
    for comment in raw_comments:
        if not isinstance(comment, dict):
            return None
        author_id = _author_id(comment)
        created_at = as_datetime(comment.get("created_at"))
        if not author_id or created_at is None or created_at in seen_times:
            return None
        seen_times.add(created_at)
        ordered.append((created_at, comment, author_id))
    return sorted(ordered, key=lambda item: item[0])


def _is_ta_comment(comment: dict, author_id: str, student_id: str) -> bool:
    if author_id == student_id:
        return False
    return text(comment.get("comment")).lstrip().startswith(_TA_MARKER)


def _is_proven_staff(comment: dict, author_id: str, student_id: str) -> bool:
    return author_id != student_id and author_role(comment) in PROVEN_STAFF_ROLES


def classify_comment_follow_up(submission: dict) -> str:
    """Classify only an evidence-backed aggregate follow-up state.

    The returned label is intentionally not a thread-state claim and never carries
    comment text or an author identifier outside this transient reduction.
    """
    student_id = text(submission.get("user_id"))
    ordered = _ordered_comments(submission)
    if not student_id or not ordered:
        return "none"
    while ordered and _is_ta_comment(ordered[-1][1], ordered[-1][2], student_id):
        ordered.pop()
    if not ordered:
        return "none"
    _, latest, latest_author_id = ordered[-1]
    if latest_author_id == student_id:
        return "definite"
    if _is_proven_staff(latest, latest_author_id, student_id):
        return "none"
    if any(author_id == student_id for _, _, author_id in ordered[:-1]):
        return "uncertain"
    return "none"


def _submission_rows(course_id: str, *, reads: WorkCourseReads) -> tuple[dict[str, dict], list[dict]]:
    check_deadline(reads._deadline)
    assignments = reads.assignments()
    check_deadline(reads._deadline)
    submissions = reads.submissions(include_comments=True)
    return _assignment_map(assignments), submissions


def _aggregate_row() -> dict:
    return {
        "total": 0,
        "pending": 0,
        "affected": 0,
        "latest_submitted_at": "",
        "latest_attempt_number": 0,
        "due_at": "",
    }


def _add_submission(row: dict, submission: dict, assignment: dict) -> None:
    row["total"] += 1
    row["pending"] += 1
    row["affected"] += 1
    submitted_at = text(submission.get("submitted_at"))
    row["latest_submitted_at"] = max(row["latest_submitted_at"], submitted_at)
    row["latest_attempt_number"] = max(
        row["latest_attempt_number"],
        max(_number(submission.get("attempt"), _number(submission.get("submission_attempt"))), 0),
    )
    row["due_at"] = text(assignment.get("due_at"))


def _eligible_submission(submission: dict) -> bool:
    return (
        isinstance(submission, dict)
        and text(submission.get("workflow_state")).casefold() in {"submitted", "pending_review"}
        and bool(text(submission.get("assignment_id")))
    )


def scan_comment_follow_up(course_id: str, *, now, reads: WorkCourseReads) -> list[dict]:
    """Project definite and uncertain student-response follow-up counts by assignment."""
    assignments, submissions = _submission_rows(course_id, reads=reads)
    aggregates = {
        "definite": defaultdict(_aggregate_row),
        "uncertain": defaultdict(_aggregate_row),
    }
    for submission in submissions:
        if not _eligible_submission(submission):
            continue
        assignment_id = text(submission.get("assignment_id"))
        assignment = assignments.get(assignment_id)
        if assignment is None:
            continue
        state = classify_comment_follow_up(submission)
        if state in aggregates:
            _add_submission(aggregates[state][assignment_id], submission, assignment)
    output = []
    for state, kind in (("definite", "grade.followup"), ("uncertain", "grade.staff_check")):
        for assignment_id in sorted(aggregates[state]):
            row = aggregates[state][assignment_id]
            output.append(finding(
                kind=kind,
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


__all__ = [
    "classify_comment_follow_up", "scan_comment_follow_up",
]
