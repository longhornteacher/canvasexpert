"""Durable evidence feeds scoring; held items cannot be scored as complete."""
from __future__ import annotations

import hashlib

import pytest

from api.mirror import evidence_scoring
from api.mirror.evidence_acquisition import (
    CourseAcquisitionReceipt, ScopeReceipt, publish_captured_attachment,
    publish_course_receipt,
)
from api.mirror.evidence_extraction import ExtractionCache, extract_captured_attachments
from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_jobs import AttachmentJobStore, enqueue_from_receipt
from api.mirror.evidence_paths import local_source_root, source_key_for_origin
from api.mirror.evidence_publish import EvidencePublisher
from api.tests.mirror.extraction import document_samples
from api.tests.mirror.test_evidence_publish import SyntheticVault

ORIGIN = "https://canvas.example.edu"
SOURCE = source_key_for_origin(ORIGIN)


def _seed(tmp_path, monkeypatch, *, filename="essay.docx", data=None, capture=True):
    """Publish an attachment association and (optionally) its extraction."""
    from api import runtime_paths
    monkeypatch.setattr(runtime_paths, "local_cache_dir", lambda: tmp_path / "_System")
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    publisher = EvidencePublisher(workspace_root=root, source_key=SOURCE,
                                  course_id="1", vault=SyntheticVault())
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z", (
        ScopeReceipt("assignment.submissions", "10", (
            {"user_id": "991001", "assignment_id": "10", "attempt": 1,
             "submitted_at": "2026-01-01T00:00:00Z", "body": "",
             "attachments": [{"id": 501, "filename": filename, "size": 6}]},
        ), True),
    ))
    publish_course_receipt(publisher=publisher, receipt=receipt,
                           writer_key="writer-a", run_id="run-a")
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    enqueue_from_receipt(jobs, receipt, source_key=SOURCE,
                         pseudonym_for=lambda raw: "Pikachu")
    job = jobs.claim()[0]
    if capture:
        payload = data if data is not None else document_samples.build_docx(paragraphs=("Durable body.",))
        digest = hashlib.sha256(payload).hexdigest()
        jobs.record(job.job_id, status="captured", digest=digest)
        job = jobs.get(job.job_id)
        publish_captured_attachment(publisher=publisher, job=job, digest=digest,
                                    writer_key="writer-a", run_id="run-b")
        cache = ExtractionCache(tmp_path / "extraction.sqlite3")
        extract_captured_attachments(
            publisher_for=lambda course: publisher, jobs=jobs, cache=cache,
            recover_original=lambda d: payload,
            run_adapter=lambda name, data, filename: __import__(
                "api.mirror.extraction.registry", fromlist=["load_adapter"]
            ).load_adapter(name)(data, filename=filename),
            writer_key="writer-a", run_id="run-c")
    index_path = local_source_root(SOURCE, root) / "query.sqlite3"
    EvidenceIndex(index_path).ingest(publisher.store.scan(), selected_courses=["1"])
    return root


def test_read_assignment_evidence_returns_extracted_text(tmp_path, monkeypatch):
    root = _seed(tmp_path, monkeypatch)
    evidence = evidence_scoring.read_assignment_evidence(
        course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    assert evidence.available is True
    assert len(evidence.students) == 1
    student = evidence.students[0]
    assert "Durable body." in student.text
    assert student.evidence_complete is True
    assert student.held is False


def test_merge_into_bundle_fills_empty_response_and_marks_complete(tmp_path, monkeypatch):
    root = _seed(tmp_path, monkeypatch)
    bundle = {"students": [{"pseudonym": "Pikachu",
                            "responses": [{"item_id": "10", "response": ""}]}]}
    summary = evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    assert summary["available"] is True
    response = bundle["students"][0]["responses"][0]
    assert "Durable body." in response["response"]
    assert response["_evidence_complete"] is True
    assert response["_held"] is False


def test_pending_original_holds_student_without_text(tmp_path, monkeypatch):
    root = _seed(tmp_path, monkeypatch, capture=False)
    evidence = evidence_scoring.read_assignment_evidence(
        course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    student = evidence.students[0]
    assert student.held is True
    assert student.evidence_complete is False
    assert "original_pending" in student.gaps


def test_held_item_cannot_be_scored_even_with_readable_text():
    from api import feedback_results
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "response": "Readable partial text.", "_held": True,
         "_evidence_complete": False}]}]}
    verdict = feedback_results.validate_results(
        [{"pseudonym": "Pikachu", "item_id": "10", "score": 5, "feedback": "Good."}],
        bundle=bundle)
    assert verdict["ok"] is False
    assert any("held" in error for error in verdict["errors"])


def test_packet_holds_marked_response_despite_text():
    from api.powergrader import scoring_packet
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "prompt": "Write.", "response": "Readable partial text.",
         "_held": True, "_evidence_complete": False}]}]}
    session = {"session_id": "s", "course_id": "1", "assignment_id": "10",
               "students": [{"user_id": "991001"}]}
    page = scoring_packet.build_packet(
        session, bundle, offset=0, limit=10)
    assert page["held"] == 1
    assert page["held_pseudonyms"] == ["Pikachu"]
    assert page["students"] == []
