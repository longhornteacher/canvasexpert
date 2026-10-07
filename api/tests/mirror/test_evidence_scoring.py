"""Durable evidence feeds scoring; held items cannot be scored as complete."""
from __future__ import annotations

import hashlib
from contextlib import nullcontext

import pytest

from api.mirror import evidence_scoring
from api.mirror.evidence_acquisition import (
    CourseAcquisitionReceipt, ScopeReceipt, publish_captured_attachment,
    publish_course_receipt,
)
from api.mirror.evidence_extraction import extract_captured_attachments
from api.mirror.evidence_index import EvidenceIndex
from api.mirror.evidence_jobs import AttachmentJobStore, enqueue_from_receipt
from api.mirror.evidence_paths import local_source_root, source_key_for_origin
from api.mirror.evidence_publish import EvidencePublisher
from api.tests.mirror.extraction import document_samples
from api.tests.mirror.acquisition_samples import SyntheticVault

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
        extract_captured_attachments(
            publisher_scope=lambda course: nullcontext(publisher), jobs=jobs,
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
                            "responses": [{"item_id": "10", "attempt": 1, "response": ""}]}]}
    summary = evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    assert summary["available"] is True
    response = bundle["students"][0]["responses"][0]
    assert "Durable body." in response["response"]
    assert response["_evidence_complete"] is True
    assert response["_held"] is False
    assert response["_evidence_revision"]
    assert response["_evidence_block_refs"]


def test_complete_durable_text_survives_safe_attachment_preparation(tmp_path, monkeypatch):
    root = _seed(tmp_path, monkeypatch)
    bundle = {"students": [{"pseudonym": "Pikachu", "_mirror_unreadable": True,
                            "local_attachments": [{"local_path": "private-placeholder"}],
                            "responses": [{"item_id": "10", "attempt": 1, "response": ""}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    assert bundle["students"][0]["_mirror_unreadable"] is True
    from api.feedback_artifacts import _prepare_attachment_safe_bundle
    prepared, excluded, _log, _holds = _prepare_attachment_safe_bundle(bundle, str(tmp_path))
    assert excluded == []
    assert "Durable body." in prepared["students"][0]["responses"][0]["response"]
    from api.powergrader import scoring_packet
    session = {"session_id": "s", "course_id": "1", "assignment_id": "10",
               "students": [{"user_id": "991001"}]}
    page = scoring_packet.build_packet(session, prepared, offset=0, limit=10)
    assert page["held"] == []
    assert "Durable body." in page["students"][0]["text"]


def test_pending_original_holds_student_without_text(tmp_path, monkeypatch):
    root = _seed(tmp_path, monkeypatch, capture=False)
    evidence = evidence_scoring.read_assignment_evidence(
        course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    student = evidence.students[0]
    assert student.held is True
    assert student.evidence_complete is False
    assert student.reason == "file_not_read"


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
    assert len(page["held"]) == 1
    assert page["held"][0]["reason"] == "no_text"
    assert page["students"] == []


def _fake_index_reads(tmp_path, monkeypatch, records_by_view, coverage_by_view=None):
    """Install deterministic paged query rows without creating private source data."""
    from api.mirror.evidence_paths import local_source_root
    index_path = local_source_root(SOURCE, tmp_path / "workspace") / "query.sqlite3"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.touch()
    calls = []

    class FakeQuery:
        def __init__(self, _path):
            pass

        def read(self, view, *, offset=0, limit=100, revision=None, **_kwargs):
            calls.append((view, offset, revision))
            rows = records_by_view.get(view, [])
            coverage = (coverage_by_view or {}).get(view, {
                "membership": {"state": "complete"},
                "synchronization": {"state": "ready", "pending_commits": [],
                                    "ambiguous_entities": []},
            })
            return {"revision": "rev-a", **coverage,
                    "records": rows[offset:offset + limit],
                    "next_offset": offset + limit if offset + limit < len(rows) else None}

    monkeypatch.setattr(evidence_scoring, "EvidenceQueryService", FakeQuery)
    return tmp_path / "workspace", calls


def _row(payload, *, attempt=None, pseudonym="Pikachu"):
    return {"pseudonym": pseudonym, "attempt": attempt, "payload": payload}


def test_decision_function_requires_exact_attempt_and_complete_extraction():
    attachment = {"attempt": 2, "attachment_key": "f", "original_digest": "d",
                  "status": "captured"}
    complete = {"attempt": 2, "attachment_key": "f", "original_digest": "d",
                "availability": "complete", "blocks": [{"text": "Extracted."}]}
    result = evidence_scoring.decide_submission_scoring(
        pseudonym="Pikachu", body_text="Typed.", attempt=2,
        attachments=[attachment], extractions=[{**complete, "attempt": "2"}])
    assert result.scorable is False
    assert result.reason == "file_not_read"
    assert result.text == "Typed."

    result = evidence_scoring.decide_submission_scoring(
        pseudonym="Pikachu", body_text="Typed.", attempt=2,
        attachments=[attachment], extractions=[complete])
    assert result.scorable is True
    assert result.reason is None
    assert result.text == "Typed.\n\nExtracted."


def test_decision_rejects_missing_attachment_identity_and_contradictory_completion():
    missing_identity = evidence_scoring.decide_submission_scoring(
        pseudonym="Pikachu", attempt=2,
        attachments=[{"attempt": 2, "status": "captured"}],
        extractions=[{"attempt": 2, "availability": "complete",
                      "blocks": [{"text": "Must not match."}]}])
    assert missing_identity.reason == "file_not_read"
    assert "Must not match." not in missing_identity.text

    contradictory = evidence_scoring.decide_submission_scoring(
        pseudonym="Pikachu", attempt=2,
        attachments=[{"attempt": 2, "attachment_key": "f", "original_digest": "d",
                      "status": "captured"}],
        extractions=[{"attempt": 2, "attachment_key": "f", "original_digest": "d",
                      "availability": "partial", "status": "complete",
                      "blocks": [{"text": "Partial."}]}])
    assert contradictory.reason == "file_not_read"


def test_decision_vocabulary_for_media_speedgrader_and_no_text():
    assert evidence_scoring.decide_submission_scoring(
        pseudonym="Pikachu", media_recording=True).reason == "media_recording"
    assert evidence_scoring.decide_submission_scoring(
        pseudonym="Pikachu", needs_speedgrader=True).reason == "needs_speedgrader"
    assert evidence_scoring.decide_submission_scoring(
        pseudonym="Pikachu").reason == "no_text"


def test_partial_sibling_and_missing_or_stale_extraction_hold_readable_text(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 2}, attempt=2)],
        "attachment_associations": [
            _row({"pseudonym": "Pikachu", "attempt": 2, "attachment_key": "a",
                  "original_digest": "digest-a", "status": "captured"}, attempt=2),
            _row({"pseudonym": "Pikachu", "attempt": 2, "attachment_key": "b",
                  "original_digest": "digest-b", "status": "captured"}, attempt=2),
        ],
        "attachment_extractions": [
            _row({"pseudonym": "Pikachu", "attempt": 2, "attachment_key": "a",
                  "original_digest": "digest-a", "availability": "partial",
                  "partial_reasons": ["ocr_incomplete"], "blocks": [{"text": "Visible sibling text.", "block_id": "b1"}]}, attempt=2),
            _row({"pseudonym": "Pikachu", "attempt": 2, "attachment_key": "b",
                  "original_digest": "old-digest", "availability": "complete",
                  "blocks": [{"text": "Stale text.", "block_id": "old"}]}, attempt=2),
        ],
    })
    evidence = evidence_scoring.read_assignment_evidence(
        course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    student = evidence.students[0]
    assert student.text == ""
    assert student.held is True
    assert student.evidence_complete is False
    assert student.gaps == ("file_not_read",)


def test_attempt_isolation_and_more_than_one_page(tmp_path, monkeypatch):
    current = [_row({"pseudonym": "Pikachu", "attempt": 2}, attempt=2)]
    attachments = []
    extractions = []
    for number in range(101):
        key = f"file-{number}"
        digest = f"digest-{number}"
        attachments.append(_row({"pseudonym": "Pikachu", "attempt": 2,
                                 "attachment_key": key, "original_digest": digest,
                                 "status": "captured" if number < 100 else "pending"}, attempt=2))
        if number < 100:
            extractions.append(_row({"pseudonym": "Pikachu", "attempt": 2,
                                     "attachment_key": key, "original_digest": digest,
                                     "availability": "complete", "blocks": []}, attempt=2))
    # This prior-attempt extraction must never be added to the current attempt's text.
    extractions.append(_row({"pseudonym": "Pikachu", "attempt": 1,
                             "attachment_key": "old", "original_digest": "old",
                             "availability": "complete",
                             "blocks": [{"text": "Prior attempt only.", "block_id": "old"}]}, attempt=1))
    root, calls = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": current,
        "attachment_associations": attachments,
        "attachment_extractions": extractions,
    })
    evidence = evidence_scoring.read_assignment_evidence(
        course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    student = evidence.students[0]
    assert len(evidence.students) == 1
    assert student.attempt == 2
    assert "Prior attempt only." not in student.text
    assert student.held is True
    assert student.reason == "file_not_read"
    assert ("attachment_associations", 100, "rev-a") in calls
    assert ("attachment_extractions", 100, "rev-a") in calls


def test_incomplete_attachment_scope_does_not_hold_current_text(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 1,
                                       "body": "Existing response."}, attempt=1)],
        "attachment_associations": [],
        "attachment_extractions": [],
    }, coverage_by_view={
        "attachment_associations": {
            "membership": {"state": "incomplete"},
            "synchronization": {"state": "sync_pending", "pending_commits": ["opaque"],
                                "ambiguous_entities": []},
        },
    })
    evidence = evidence_scoring.read_assignment_evidence(
        course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    assert len(evidence.students) == 1
    student = evidence.students[0]
    assert student.held is False
    assert student.scorable is True
    assert student.text == "Existing response."
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 1, "response": "Existing response."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    response = bundle["students"][0]["responses"][0]
    assert response["response"] == "Existing response."
    assert response["_held"] is False


def test_complete_empty_attachment_scopes_leave_text_only_submission_unheld(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 1}, attempt=1)],
        "attachment_associations": [],
        "attachment_extractions": [],
    })
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 1, "response": "Text only."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    response = bundle["students"][0]["responses"][0]
    assert response["response"] == "Text only."
    assert response["_held"] is False


def test_text_only_assignment_without_attachment_scope_is_scorable(tmp_path, monkeypatch):
    # Regression: the mirror publishes an attachment scope only for assignments
    # that observed attachments, so a text-entry assignment has none. That must
    # not hold every response (2026-10-07: five SCR packets had zero rows).
    absent = {"membership": {"state": "unknown"},
              "synchronization": {"state": "unknown", "pending_commits": [],
                                  "ambiguous_entities": []}}
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 1}, attempt=1)],
        "attachment_associations": [],
        "attachment_extractions": [],
    }, coverage_by_view={"attachment_associations": absent,
                         "attachment_extractions": absent})
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 1, "response": "Text only."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    response = bundle["students"][0]["responses"][0]
    assert response["response"] == "Text only."
    assert not response.get("_held")


def test_frozen_file_without_published_association_stays_file_not_read(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 2}, attempt=2)],
        "attachment_associations": [], "attachment_extractions": [],
    })
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 2, "response": "Typed text."}]}]}
    summary = evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root,
        canvas_base=ORIGIN,
        submission_attachments={("Pikachu", 2): [{"id": 501, "filename": "essay.docx"}]})
    response = bundle["students"][0]["responses"][0]
    assert summary["held"] == 1
    assert response["response"] == "Typed text."
    assert response["_hold_reason"] == "file_not_read"


def test_unknown_attempt_with_known_file_is_held_when_store_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_scoring, "local_source_root",
                        lambda *_args: tmp_path / "missing-source")
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "response": "Typed text."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=tmp_path,
        canvas_base=ORIGIN,
        submission_attachments={("Pikachu", None): [{"id": 501, "filename": "essay.docx"}]})
    response = bundle["students"][0]["responses"][0]
    assert response["_held"] is True
    assert response["_hold_reason"] == "file_not_read"


def test_malformed_source_attachment_descriptor_fails_closed(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 2}, attempt=2)],
        "attachment_associations": [], "attachment_extractions": [],
    })
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 2, "response": "Typed text."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root,
        canvas_base=ORIGIN,
        submission_attachments={("Pikachu", 2): [{"size": 10}]})
    response = bundle["students"][0]["responses"][0]
    assert response["_hold_reason"] == "file_not_read"


def test_frozen_text_only_ignores_unmatched_index_attachment(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 2}, attempt=2)],
        "attachment_associations": [_row({"pseudonym": "Pikachu", "attempt": 2,
                                           "attachment_key": "extra", "original_digest": "d",
                                           "status": "pending"}, attempt=2)],
        "attachment_extractions": [],
    })
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 2, "response": "Typed text."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root,
        canvas_base=ORIGIN, submission_attachments={("Pikachu", 2): []})
    response = bundle["students"][0]["responses"][0]
    assert response["_scorable"] is True
    assert "_hold_reason" not in response


def test_partial_attachment_is_not_used_and_holds_readable_body(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 2}, attempt=2)],
        "attachment_associations": [_row({"pseudonym": "Pikachu", "attempt": 2,
                                          "attachment_key": "file", "original_digest": "digest",
                                          "status": "captured"}, attempt=2)],
        "attachment_extractions": [_row({"pseudonym": "Pikachu", "attempt": 2,
                                         "attachment_key": "file", "original_digest": "digest",
                                         "availability": "partial", "partial_reasons": ["ocr_incomplete"],
                                         "blocks": [{"text": "Readable file portion.", "block_id": "b1"}]}, attempt=2)],
    })
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 2, "response": "Typed response."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    response = bundle["students"][0]["responses"][0]
    assert response["response"] == "Typed response."
    assert response["_held"] is True
    assert response["_hold_reason"] == "file_not_read"


def test_prior_safe_attempt_is_held_and_receives_no_current_file_text(tmp_path, monkeypatch):
    root, _ = _fake_index_reads(tmp_path, monkeypatch, {
        "current_submissions": [_row({"pseudonym": "Pikachu", "attempt": 2}, attempt=2)],
        "attachment_associations": [_row({"pseudonym": "Pikachu", "attempt": 2,
                                          "attachment_key": "file", "original_digest": "digest",
                                          "status": "captured"}, attempt=2)],
        "attachment_extractions": [_row({"pseudonym": "Pikachu", "attempt": 2,
                                         "attachment_key": "file", "original_digest": "digest",
                                         "availability": "complete",
                                         "blocks": [{"text": "Current file text.", "block_id": "b1"}]}, attempt=2)],
    })
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "10", "attempt": 1, "response": "Earlier response."}]}]}
    evidence_scoring.merge_into_bundle(
        bundle, course_id="1", assignment_id="10", workspace_root=root, canvas_base=ORIGIN)
    response = bundle["students"][0]["responses"][0]
    assert response["response"] == "Earlier response."
    assert response["response"] == "Earlier response."
    assert "Current file text." not in response["response"]


def test_safe_assignment_response_carries_submission_attempt():
    from api.feedback_artifacts import pseudonymize_submissions

    class Vault:
        def get_or_assign(self, *_args):
            return "Pikachu"

    bundle = pseudonymize_submissions([{
        "user_id": "synthetic-user", "attempt": 2, "submitted_at": "2026-01-01T00:00:00Z",
        "body": "Typed response.", "assignment": {"id": 10, "description": "Prompt"},
    }], Vault(), "Synthetic assignment")
    assert bundle["students"][0]["responses"][0]["attempt"] == 2
