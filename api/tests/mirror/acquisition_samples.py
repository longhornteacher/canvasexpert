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


class SyntheticVault:
    """Synthetic identities supporting the real roster registration surface."""

    def __init__(self):
        self.people = {
            "991001": ("Pikachu", "Avery Sample"),
            "991002": ("Eevee", "Morgan Sample"),
        }
        self.sis_ids = {}
        self.nicknames = {}

    def entries(self):
        return [{"canvas_id": raw, "real_name": name, "pseudonym": pseudo,
                 "sis_id": self.sis_ids.get(raw, ""),
                 "nicknames": list(self.nicknames.get(raw, []))}
                for raw, (pseudo, name) in self.people.items()]

    def all_real_identifiers(self):
        names = {name for _, name in self.people.values()}
        names.update(n for values in self.nicknames.values() for n in values)
        return names, set(self.people) | set(self.sis_ids.values())

    def get_or_assign(self, raw, real_name="", sis_id=""):
        raw = str(raw)
        if raw not in self.people:
            pseudo = {"synthetic-user-01": "Pikachu", "synthetic-user-02": "Eevee"}.get(raw)
            if pseudo is None:
                raise ValueError("unresolved")
            self.people[raw] = (pseudo, real_name)
        self.remember_identity(raw, real_name, sis_id)
        return self.people[raw][0]

    def remember_identity(self, raw, name, sis_id):
        raw = str(raw)
        if raw in self.people and name:
            self.people[raw] = (self.people[raw][0], name)
        if sis_id:
            self.sis_ids[raw] = str(sis_id)

    def add_nicknames(self, raw, names):
        self.nicknames.setdefault(str(raw), []).extend(names)

    def require_stable(self, raw):
        if str(raw) not in self.people:
            raise ValueError("unresolved")

    def save(self):
        pass


def synthetic_documents():
    from io import BytesIO
    from docx import Document
    document = Document()
    document.add_paragraph("Synthetic thesis sentence.")
    stream = BytesIO()
    document.save(stream)
    return {"9001": stream.getvalue(), "9002": b"%PDF-1.4 not a real pdf"}


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
                     ({"id": 1, "name": "ELA 7", "workflow_state": "available"},), True),
        ScopeReceipt("course.assignments", course_id,
                     ({"id": 10, "name": "Narrative writing", "description": "Write a narrative.",
                       "points_possible": 20},
                      {"id": 11, "name": "Independent reading"}), True),
        ScopeReceipt("course.roster", course_id,
                     ({"id": "synthetic-user-01", "name": "Avery Sample"},
                      {"id": "synthetic-user-02", "name": "Morgan Sample"}), True),
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
    if variant == "read_path":
        scopes.append(ScopeReceipt("course.sections", course_id,
                                   ({"id": 500, "name": "Period 1"},), True))
        submissions[0]["attachments"] = [
            {"id": "9001", "filename": "essay.docx", "size": 100,
             "url": "https://canvas.example.test/files/9001"},
            {"id": "9002", "filename": "broken.pdf", "size": 100,
             "url": "https://canvas.example.test/files/9002"},
        ]
    elif variant == "groups":
        scopes.append(ScopeReceipt("course.groups", course_id, (
            {"id": "30", "name": "Blue", "group_category_id": "cat-1",
             "group_category_name": "Teams", "user_ids": ["synthetic-user-01"]},
            {"_category_only": True, "group_category_id": "cat-2",
             "group_category_name": "Unassigned"},
        ), True))
    elif variant == "second_course":
        course_id = "2"
        scopes = [
            ScopeReceipt("course.context", course_id,
                         ({"id": 2, "name": "Science 7"},), True),
            ScopeReceipt("course.assignments", course_id,
                         ({"id": 20, "name": "Lab report"},), True),
            ScopeReceipt("course.roster", course_id,
                         ({"id": "synthetic-user-02", "name": "Morgan Sample"},), True),
            ScopeReceipt("assignment.submissions", "20",
                         (_submission("synthetic-user-02", "20", 1,
                                      "Second course response.", finish),), True),
        ]
    elif variant == "full":
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
                               ({"id": 1, "name": "ELA 7", "workflow_state": "available"},),
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
