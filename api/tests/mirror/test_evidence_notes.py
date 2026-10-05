"""Contained agent-note revision and privacy laws."""
from __future__ import annotations

import pytest

from api.mirror.evidence_notes import (
    NoteConflict, build_revision, check_expected_revision, mark_stale, new_note_id,
    publish_note,
)
from api.mirror.evidence_publish import EvidencePublisher
from api.tests.mirror.acquisition_samples import SyntheticVault

SOURCE = "a" * 64


def _publisher(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    return EvidencePublisher(workspace_root=root, source_key=SOURCE,
                             course_id="1", vault=SyntheticVault()), root


def test_defaults_are_provisional_and_teacher_only():
    revision = build_revision(note_id=new_note_id(), category="summary", text="Draft note.", revision=1)
    assert revision.status == "provisional"
    assert revision.parent_revision is None


def test_teacher_confirmed_requires_explicit_status():
    revision = build_revision(note_id=new_note_id(), category="teacher_directive",
                              text="Use this rubric.", revision=1, status="teacher_confirmed")
    assert revision.status == "teacher_confirmed"


def test_invalid_category_and_status_are_refused():
    with pytest.raises(ValueError):
        build_revision(note_id="n", category="made_up", text="x", revision=1)
    with pytest.raises(ValueError):
        build_revision(note_id="n", category="summary", text="x", revision=1, status="confirmed")


def test_optimistic_revision_conflict_is_refused():
    check_expected_revision(3, 3)
    with pytest.raises(NoteConflict):
        check_expected_revision(4, 3)


def test_source_change_marks_note_stale_without_erasing():
    revision = build_revision(note_id="n", category="comparison", text="Compared.",
                              revision=1, evidence_revisions=("rev-a",))
    stale = mark_stale(revision, current_evidence=("rev-b",))
    assert stale.stale is True
    assert stale.text == revision.text
    unchanged = mark_stale(revision, current_evidence=("rev-a",))
    assert unchanged.stale is False


def test_publish_note_scrubs_text_and_omits_pii(tmp_path):
    publisher, root = _publisher(tmp_path)
    revision = build_revision(note_id="n1", category="summary",
                              text="Avery Sample wrote a strong claim.", revision=1)
    publish_note(publisher=publisher, assignment_id="10", revision=revision,
                 writer_key="writer-a", run_id="run-a")
    facts = [f for f in publisher.store.scan().facts.values() if f["kind"] == "note"]
    assert len(facts) == 1
    assert "Avery" not in facts[0]["payload"]["text"]
    safe_bytes = b"".join(p.read_bytes() for p in (root / "CanvasMirror").rglob("*.json"))
    assert b"Avery" not in safe_bytes


def test_append_only_revisions_keep_prior_revision(tmp_path):
    publisher, root = _publisher(tmp_path)
    first = build_revision(note_id="n1", category="summary", text="First.", revision=1)
    publish_note(publisher=publisher, assignment_id="10", revision=first,
                 writer_key="writer-a", run_id="run-a")
    second = build_revision(note_id="n1", category="summary", text="Second.", revision=2,
                            parent_revision=1)
    publish_note(publisher=publisher, assignment_id="10", revision=second,
                 writer_key="writer-a", run_id="run-b")
    notes = [f for f in publisher.store.scan().facts.values() if f["kind"] == "note"]
    assert len(notes) == 2
    assert {note["payload"]["revision"] for note in notes} == {1, 2}
