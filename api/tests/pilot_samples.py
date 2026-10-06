"""Deterministic synthetic builders for performance and discovery fixtures.

Everything here is fabricated: names are ``Sample`` placeholders, ids are
sequential integers, and every path is derived from a caller-supplied root.
No teacher, student or Canvas data is used. The named pytest fixtures that wrap
these builders live in ``api/tests/conftest.py`` and
``api/tests/mirror/conftest.py``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from api.mirror.evidence_acquisition import (
    CourseAcquisitionReceipt, ScopeReceipt, publish_course_receipt,
)
from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_publish import EvidencePublisher
from api.tests.mirror.acquisition_samples import SyntheticVault

PILOT_SOURCE = "d" * 64
PILOT_COURSES = ("1", "2", "3")
PILOT_STUDENTS = 30
PILOT_ASSIGNMENTS = 30
PILOT_SESSIONS = 25
PILOT_UNPUBLISHED = 2  # trailing assignments per course that are not published

# NOTE: receipt publication currently drops the Canvas `excused` flag from submission
# facts, so the "excused" rows below arrive as plain unsubmitted rows in the index.
# Submission mix by (student + assignment) % len(MIX). Keys describe the
# gradebook law: only submitted/pending_review rows with a timestamp, on a
# published assignment, for a non-excused roster member count as ungraded.
SUBMISSION_MIX = (
    "submitted", "graded", "pending_scored", "unsubmitted", "late_submitted",
    "excused", "resubmitted", "submitted", "graded", "unsubmitted",
)


class PilotVault(SyntheticVault):
    """Synthetic vault with a stable roster for every pilot course."""

    def __init__(self, courses=PILOT_COURSES, students=PILOT_STUDENTS):
        super().__init__()
        self.people = {}
        for course in courses:
            for number in range(1, students + 1):
                self.people[student_id(course, number)] = (
                    f"Pseudo{course}x{number:02d}", f"Student{course}n{number:02d} Sample")

    def get_or_assign(self, raw, real_name="", sis_id=""):
        raw = str(raw)
        if raw not in self.people:
            raise ValueError("unresolved")
        self.remember_identity(raw, real_name, sis_id)
        return self.people[raw][0]


def student_id(course, number) -> str:
    return f"99{course}{int(number):03d}"


def assignment_id(course, number) -> str:
    return f"{course}{int(number):03d}"


def scale_receipt(n_scopes, *, history=1, students=1, course="1"):
    """One roster plus ``n_scopes`` single-assignment submission scopes.

    ``history`` is the number of retained earlier attempts per submission and
    ``students`` the submissions per scope (the roster always has at least two); both are explicit so cost laws can
    vary scope count and retained-history size independently.
    """
    roster_ids = [student_id(course, n) for n in range(1, max(students, 2) + 1)]
    scopes = [ScopeReceipt("course.roster", course, tuple(
        {"id": raw, "name": f"Student{course}n{index:02d} Sample"}
        for index, raw in enumerate(roster_ids, 1)), True)]
    for index in range(n_scopes):
        aid = str(100 + index)
        rows = []
        for owner in roster_ids[:students]:
            rows.append({
                "user_id": owner, "assignment_id": aid, "attempt": history + 1,
                "submitted_at": "2026-01-02T00:00:00Z", "body": "Synthetic prose.",
                "submission_history": [
                    {"attempt": attempt, "submitted_at": f"2026-01-01T{attempt:02d}:00:00Z",
                     "body": f"Earlier synthetic prose {attempt}."}
                    for attempt in range(1, history + 1)]})
        scopes.append(ScopeReceipt("assignment.submissions", aid, tuple(rows), True))
    return CourseAcquisitionReceipt(course, "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z",
                                    tuple(scopes))


def _submission_row(course, student, assignment, kind):
    row = {"user_id": student_id(course, student), "assignment_id": assignment_id(course, assignment),
           "attempt": 1, "submitted_at": None, "body": "", "workflow_state": "unsubmitted",
           "score": None, "late": False, "excused": False, "missing": kind == "unsubmitted"}
    stamp = f"2026-01-{(assignment % 27) + 1:02d}T12:00:00Z"
    if kind in {"submitted", "late_submitted", "resubmitted", "pending_scored", "graded"}:
        row.update(submitted_at=stamp, body=f"Synthetic response {student}-{assignment}.",
                   workflow_state="submitted")
    if kind == "late_submitted":
        row["late"] = True
    if kind == "pending_scored":
        row.update(workflow_state="pending_review", score=0 if student % 2 else 7.5)
    if kind == "graded":
        row.update(workflow_state="graded", score=9.0)
    if kind == "resubmitted":
        row.update(attempt=3, submission_history=[
            {"attempt": 1, "submitted_at": "2025-12-01T12:00:00Z", "body": "First synthetic draft."},
            {"attempt": 2, "submitted_at": "2025-12-15T12:00:00Z", "body": "Second synthetic draft."}])
    if kind == "excused":
        row.update(excused=True, workflow_state="unsubmitted")
    return row


def pilot_course_receipt(course, *, students=PILOT_STUDENTS, assignments=PILOT_ASSIGNMENTS):
    """Pilot-scale course: roster, assignments, retained attempts, mixed states."""
    rows = [{"id": assignment_id(course, number), "name": f"Assignment {number:02d}",
             "points_possible": 10, "published": number <= assignments - PILOT_UNPUBLISHED,
             "due_at": f"2026-02-{(number % 27) + 1:02d}T05:00:00Z"}
            for number in range(1, assignments + 1)]
    scopes = [
        ScopeReceipt("course.context", course,
                     ({"id": course, "name": f"Pilot Course {course}",
                       "workflow_state": "available"},), True),
        ScopeReceipt("course.assignments", course, tuple(rows), True),
        ScopeReceipt("course.roster", course, tuple(
            {"id": student_id(course, n), "name": f"Student{course}n{n:02d} Sample"}
            for n in range(1, students + 1)), True),
    ]
    for number in range(1, assignments + 1):
        submissions = tuple(
            _submission_row(course, student, number,
                            SUBMISSION_MIX[(student + number) % len(SUBMISSION_MIX)])
            for student in range(1, students + 1))
        scopes.append(ScopeReceipt("assignment.submissions", assignment_id(course, number),
                                   submissions, True))
    return CourseAcquisitionReceipt(course, "2026-01-30T00:00:00Z", "2026-01-30T00:01:00Z",
                                    tuple(scopes))


@dataclass(frozen=True)
class PilotEvidence:
    root: Path
    source: str
    courses: tuple
    index: EvidenceIndex
    revision: str
    vault: PilotVault

    def expected_ungraded(self, course) -> dict[str, int]:
        """Per-assignment ungraded counts under the gradebook law (published only)."""
        counts = {}
        for number in range(1, PILOT_ASSIGNMENTS - PILOT_UNPUBLISHED + 1):
            counts[assignment_id(course, number)] = sum(
                1 for student in range(1, PILOT_STUDENTS + 1)
                if SUBMISSION_MIX[(student + number) % len(SUBMISSION_MIX)]
                in {"submitted", "late_submitted", "resubmitted", "pending_scored"})
        return counts


def build_pilot_evidence(tmp_path: Path, *, courses=PILOT_COURSES,
                         students=PILOT_STUDENTS, assignments=PILOT_ASSIGNMENTS) -> PilotEvidence:
    """Publish the pilot courses through the real publisher and ingest one index."""
    tmp_path = Path(tmp_path)
    root = tmp_path / "workspace"
    vault = PilotVault(courses, students)
    snapshots = []
    for course in courses:
        receipt = pilot_course_receipt(course, students=students, assignments=assignments)
        publisher = EvidencePublisher(workspace_root=root, source_key=PILOT_SOURCE,
                                      course_id=course, vault=vault)
        result = publish_course_receipt(publisher=publisher, receipt=receipt,
                                        writer_key="writer-pilot", run_id=f"pilot-{course}")
        assert len(result.successful_scopes) == len(receipt.scopes) and not result.gaps
        snapshots.append(publisher.store.scan())
    index = EvidenceIndex(tmp_path / "local" / "query.sqlite3")
    revision = index.ingest_many(snapshots, selected_courses=list(courses))
    return PilotEvidence(root, PILOT_SOURCE, tuple(courses), index, revision, vault)


def pilot_session_record(session_id, *, course, assignment, status="ready",
                         created="2026-01-30T08:00:00", students=PILOT_STUDENTS, **extra):
    return {
        "session_id": session_id, "session_kind": "scoring_assignment",
        "course_id": course, "assignment_id": assignment,
        "assignment_name": f"Assignment {assignment}", "created": created,
        "status": status, "mode": "packet", "storage_model": "shared_work.v1",
        "students": [{"user_id": f"synthetic-user-{n:02d}", "status": "approved",
                      "posted": False, "real_name": f"Synthetic Student {n:02d}"}
                     for n in range(1, students + 1)],
        **extra,
    }


def write_large_safe_bundle(root: Path, *, students=PILOT_STUDENTS, kilobytes_each=64) -> Path:
    """One large synthetic SAFE bundle, so summary reads can prove they skip it."""
    path = Path(root) / "For AI" / "pilot-large-bundle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    filler = "Synthetic essay sentence. " * (kilobytes_each * 1024 // 26)
    bundle = {"students": [{"pseudonym": f"Pseudo1x{n:02d}",
                            "responses": [{"item_id": "item-1", "response": filler}]}
                           for n in range(1, students + 1)]}
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def build_pilot_sessions(session_store, root: Path, *, courses=PILOT_COURSES) -> dict:
    """Write exactly 25 session records through the real ``session_store``.

    12 single actionable scopes, 5 scopes with a superseded predecessor plus a
    current actionable record (10 records), and 3 terminal scopes. One
    actionable record carries a large SAFE bundle. Returns the ids by role.
    """
    ids = {"actionable": [], "superseded": [], "terminal": [], "large_bundle": None}
    cursor = iter(range(1, 10_000))

    def next_scope():
        n = next(cursor)
        course = courses[n % len(courses)]
        return course, assignment_id(course, (n // len(courses)) + 1)

    bundle = write_large_safe_bundle(root)
    statuses = ("ready", "needs_teacher_input", "staged", "ready")
    for position in range(12):
        course, assignment = next_scope()
        sid = f"pilot-actionable-{position:02d}"
        extra = {}
        if position == 0:
            extra["privacy_artifacts"] = {"safe_folder": str(bundle.parent),
                                          "safe_bundle": str(bundle), "safe_students": PILOT_STUDENTS}
            ids["large_bundle"] = sid
        session_store.save_session(pilot_session_record(
            sid, course=course, assignment=assignment,
            status=statuses[position % len(statuses)], **extra))
        ids["actionable"].append(sid)
    for position in range(5):
        course, assignment = next_scope()
        old, new = f"pilot-old-{position:02d}", f"pilot-current-{position:02d}"
        session_store.save_session(pilot_session_record(
            old, course=course, assignment=assignment, created="2026-01-01T08:00:00"))
        session_store.activate_scoring_session(pilot_session_record(
            new, course=course, assignment=assignment, created="2026-01-02T08:00:00"))
        ids["superseded"].append(old)
        ids["actionable"].append(new)
    for position, status in enumerate(("completed", "completed_with_holds", "completed")):
        course, assignment = next_scope()
        sid = f"pilot-terminal-{position:02d}"
        session_store.save_session(pilot_session_record(
            sid, course=course, assignment=assignment, status=status))
        ids["terminal"].append(sid)
    total = len(ids["actionable"]) + len(ids["superseded"]) + len(ids["terminal"])
    assert total == PILOT_SESSIONS
    return ids
