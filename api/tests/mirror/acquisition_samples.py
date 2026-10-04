"""Deterministic synthetic inputs for CanvasMirror acquisition tests.

These builders contain no live Canvas data and do not express an election
policy. They only provide stable, varied inputs for callers to exercise.
"""
from __future__ import annotations

import json
from pathlib import Path

from api.mirror.evidence_acquisition import CourseAcquisitionReceipt, ScopeReceipt


SOURCE = "c" * 64
WRITER_A = "a" * 32
WRITER_B = "b" * 32
CLAIM_OLD = "1" * 32
CLAIM_NEW = "2" * 32
INCARNATION_A = "3" * 32
INCARNATION_B = "4" * 32


def _presence(writer: str, incarnation: str, claim: str, *, lineage=(),
              counter=1, released=False, refs=()):
    return {
        "schema_version": 1,
        "writer_key": writer,
        "sources": {
            SOURCE: {
                "incarnation": incarnation,
                "claim_id": claim,
                "lineage": list(lineage),
                "heartbeat_counter": counter,
                "released": released,
                "advertised_commit_refs": list(refs),
            }
        },
    }


def presence_root(tmp_path: Path, variant: str = "advancing_incumbent") -> Path:
    """Write one deterministic owner-presence scenario and return its directory."""
    scenarios = {
        "advancing_incumbent": {
            f"{WRITER_A}.json": _presence(WRITER_A, INCARNATION_A, CLAIM_OLD, counter=8),
        },
        "stale_unchanged": {
            f"{WRITER_A}.json": _presence(WRITER_A, INCARNATION_A, CLAIM_OLD, counter=2),
        },
        "clean_release": {
            f"{WRITER_A}.json": _presence(WRITER_A, INCARNATION_A, CLAIM_OLD,
                                           counter=4, released=True),
        },
        "simultaneous_claim": {
            f"{WRITER_A}.json": _presence(WRITER_A, INCARNATION_A, CLAIM_OLD),
            f"{WRITER_B}.json": _presence(WRITER_B, INCARNATION_B, CLAIM_NEW),
        },
        "late_old_incarnation": {
            f"{WRITER_A}.json": _presence(WRITER_A, INCARNATION_B, CLAIM_NEW,
                                           lineage=(CLAIM_OLD,), counter=2),
            f"{WRITER_B}.json": _presence(WRITER_B, INCARNATION_A, CLAIM_OLD),
        },
        "future_wallclock_free": {
            f"{WRITER_A}.json": _presence(WRITER_A, INCARNATION_A, CLAIM_OLD, counter=3),
        },
    }
    try:
        documents = scenarios[variant]
    except KeyError as exc:
        raise ValueError(f"unknown_presence_variant: {variant}") from exc
    root = Path(tmp_path) / "_System" / "CanvasMirror Control" / "presence"
    root.mkdir(parents=True, exist_ok=True)
    for name, document in sorted(documents.items()):
        (root / name).write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
    return root


def _submission(user_id: str, assignment_id: str, attempt: int | None,
                body: str | None, submitted_at: str | None, **extra) -> dict:
    row = {"user_id": user_id, "assignment_id": assignment_id}
    if attempt is not None:
        row["attempt"] = attempt
    if body is not None:
        row["body"] = body
    if submitted_at is not None:
        row["submitted_at"] = submitted_at
    row.update(extra)
    return row


def course_receipt_sample(variant: str = "full") -> CourseAcquisitionReceipt:
    """Return a deterministic synthetic receipt scenario with private-shaped rows."""
    start, finish, course_id = "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z", "1"
    submissions = (
        _submission("synthetic-user-01", "10", 3, "Latest synthetic draft.",
                    "2026-01-03T12:00:00Z", late=False,
                    submission_history=(
                        _submission("synthetic-user-01", "10", 1, "Earlier draft.",
                                    "2025-12-30T12:00:00Z", late=True),
                        _submission("synthetic-user-01", "10", 2, None,
                                    "2026-01-01T12:00:00Z"),
                    )),
        _submission("synthetic-user-02", "10", 1, "Second synthetic response.",
                    "2026-01-02T12:00:00Z"),
    )
    scopes = [
        ScopeReceipt("course.context", course_id,
                     ({"id": 1, "name": "Synthetic ELA", "workflow_state": "available"},), True),
        ScopeReceipt("course.assignments", course_id,
                     ({"id": 10, "name": "Synthetic writing", "description": "Synthetic prompt.",
                       "points_possible": 20},
                      {"id": 11, "name": "Synthetic independent assignment"}), True),
        ScopeReceipt("course.roster", course_id,
                     ({"id": "synthetic-user-01", "name": "Synthetic Learner One"},
                      {"id": "synthetic-user-02", "name": "Synthetic Learner Two"}), True),
        ScopeReceipt("assignment.submissions", "10", submissions, True,
                     watermarks={"submitted_since": "2026-01-03T00:00:00Z"}),
        ScopeReceipt("assignment.comments", "10",
                     ({"id": 20, "assignment_id": 10, "user_id": "synthetic-user-01",
                       "author_role": "teacher", "comment": "Synthetic teacher comment."},), True),
        ScopeReceipt("assignment.overrides", "10",
                     ({"id": 30, "assignment_id": 10,
                       "student_ids": ["synthetic-user-01"],
                       "due_at": "2026-01-05T12:00:00Z"},), True),
    ]
    if variant == "full":
        pass
    elif variant == "partial_submissions":
        scopes[3] = ScopeReceipt("assignment.submissions", "10", submissions[:1], False,
                                 "pagination_incomplete", watermarks={"submitted_since": "old"})
    elif variant == "empty_submissions":
        scopes[3] = ScopeReceipt("assignment.submissions", "10", (), True)
    elif variant == "one_failed_assignment":
        scopes.append(ScopeReceipt("assignment.submissions", "11", (), False, "timeout"))
    elif variant == "deselected_course":
        # The exact scope watermark gives test consumers an explicit retained-only marker.
        scopes = [ScopeReceipt("course.context", course_id,
                               ({"id": 1, "name": "Synthetic ELA", "workflow_state": "available"},),
                               True, mode="snapshot", watermarks={"selection": "deselected"})]
    elif variant == "sparse_attempts":
        scopes[3] = ScopeReceipt("assignment.submissions", "10", (
            _submission("synthetic-user-01", "10", 3, None, "2026-01-03T12:00:00Z",
                        submission_history=(
                            _submission("synthetic-user-01", "10", 1, "Earlier draft.",
                                        "2025-12-30T12:00:00Z", late=True),
                            _submission("synthetic-user-01", "10", 2, None, None),
                        )),
        ), True)
    else:
        raise ValueError(f"unknown_course_receipt_variant: {variant}")
    return CourseAcquisitionReceipt(course_id, start, finish, tuple(scopes))
