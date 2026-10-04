"""Contained, append-only agent notes in one course namespace.

Notes are assignment-scoped by default, provisional and teacher-only unless an
explicit teacher direction marks them confirmed. Source changes mark derived
notes stale without erasing them. Publication uses the normal privacy boundary;
a note containing unresolved PII is refused before any safe byte is written.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from uuid import uuid4

from api.mirror.evidence_schema import canonical_bytes

NOTE_CATEGORIES = frozenset({"summary", "comparison", "feedback_draft", "teacher_directive"})
NOTE_STATUSES = frozenset({"provisional", "teacher_confirmed"})


class NoteConflict(ValueError):
    """Optimistic revision check failed; the caller must re-read and retry."""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__("note_revision_conflict")


@dataclass(frozen=True)
class NoteRevision:
    note_id: str
    category: str
    status: str
    text: str
    revision: int
    parent_revision: int | None
    superseded_ref: str | None
    evidence_revisions: tuple[str, ...]
    stale: bool = False

    def digest(self) -> str:
        return hashlib.sha256(canonical_bytes({
            "note_id": self.note_id, "category": self.category, "status": self.status,
            "text": self.text, "revision": self.revision,
            "parent_revision": self.parent_revision,
            "superseded_ref": self.superseded_ref,
            "evidence_revisions": list(self.evidence_revisions),
        })).hexdigest()


def new_note_id() -> str:
    return uuid4().hex


def build_revision(*, note_id: str, category: str, text: str, revision: int,
                   parent_revision: int | None = None,
                   superseded_ref: str | None = None,
                   evidence_revisions: tuple[str, ...] = (),
                   status: str = "provisional") -> NoteRevision:
    """Build one validated revision; defaults are provisional and teacher-only."""
    if category not in NOTE_CATEGORIES:
        raise ValueError("invalid_note_category")
    if status not in NOTE_STATUSES:
        raise ValueError("invalid_note_status")
    if type(revision) is not int or revision < 1:
        raise ValueError("invalid_note_revision")
    if not isinstance(text, str):
        raise ValueError("invalid_note_text")
    return NoteRevision(note_id=note_id, category=category, status=status, text=text,
                        revision=revision, parent_revision=parent_revision,
                        superseded_ref=superseded_ref,
                        evidence_revisions=tuple(evidence_revisions))


def check_expected_revision(current: int, expected: int) -> None:
    """Refuse a stale write rather than silently losing a concurrent revision."""
    if current != expected:
        raise NoteConflict(expected, current)


def mark_stale(revision: NoteRevision, *, current_evidence: tuple[str, ...]) -> NoteRevision:
    """Mark a derived note stale when its source evidence changed; never erase it."""
    stale = bool(revision.evidence_revisions) and set(revision.evidence_revisions) != set(current_evidence)
    if stale == revision.stale:
        return revision
    return NoteRevision(note_id=revision.note_id, category=revision.category,
                        status=revision.status, text=revision.text,
                        revision=revision.revision, parent_revision=revision.parent_revision,
                        superseded_ref=revision.superseded_ref,
                        evidence_revisions=revision.evidence_revisions, stale=stale)


def publish_note(*, publisher, assignment_id: str, revision: NoteRevision,
                 writer_key: str, run_id: str) -> str:
    """Publish one scrubbed note revision and its scope commit.

    The note text passes through the publisher's privacy boundary; a note
    containing unresolved PII is refused before any safe byte is written.
    """
    from api.mirror.evidence_publish import _utc_now
    payload = {
        "assignment_id": assignment_id, "note_id": revision.note_id,
        "category": revision.category, "status": revision.status,
        "text": publisher._scrub_text(revision.text), "revision": revision.revision,
        "parent_revision": revision.parent_revision,
        "superseded_ref": revision.superseded_ref,
        "evidence_revisions": list(revision.evidence_revisions),
        "stale": revision.stale,
    }
    key = f"note:{assignment_id}:{revision.note_id}:{revision.revision}"
    _, digest = publisher._fact("note", key, payload)
    snapshot = publisher.store.scan()
    scope_key = (publisher.source_key, publisher.course_id, "assignment.notes", assignment_id)
    state = snapshot.scopes.get(scope_key)
    refs = sorted({ref for ref in (state.current_refs if state else ())
                   if snapshot.facts.get(ref, {}).get("entity_key") != key} | {digest})
    members = sorted(set(state.member_keys if state else ()) | {key})
    record = {
        "schema_version": 1, "source_key": publisher.source_key,
        "course_id": publisher.course_id, "scope": "assignment.notes",
        "scope_id": assignment_id, "writer_key": writer_key, "run_id": run_id,
        "parents": list(state.heads if state else ()),
        "acquisition_started_at": _utc_now(), "acquisition_finished_at": _utc_now(),
        "mode": "snapshot", "membership_complete": bool(state and state.membership_complete),
        "record_refs": refs, "member_keys": members, "gaps": [], "watermarks": {},
    }
    return publisher.store.publish_commit(record)
