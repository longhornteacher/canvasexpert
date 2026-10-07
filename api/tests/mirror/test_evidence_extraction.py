"""Association-keyed extraction publication, retries, and privacy laws."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import threading

import pytest

from api.mirror.evidence_extraction import (
    extract_captured_attachments, extraction_entity_key, publish_extraction,
)
from api.mirror.evidence_jobs import AttachmentJobStore
from api.mirror.evidence_publish import EvidencePublisher, PublicationRefused
from api.mirror.extraction import registry
from api.mirror.extraction.ocr_runtime import OcrBlock, OcrResult
from api.mirror.extraction.schema import ExtractionError, ExtractionResult
from api.mirror.extraction.text import extract as extract_text
from api.tests.mirror.acquisition_samples import SyntheticVault
from api.tests.mirror.extraction import document_samples

SOURCE = "a" * 64


def _publisher(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    return EvidencePublisher(workspace_root=root, source_key=SOURCE,
                             course_id="1", vault=SyntheticVault()), root


@contextmanager
def _scope(publisher):
    yield publisher


def _job(jobs, key, *, pseudonym="Pikachu", attempt=1, filename="essay.docx",
         digest="c" * 64, course_id="1", assignment_id="10", now=None):
    return jobs.ensure(source_key=SOURCE, course_id=course_id,
                       assignment_id=assignment_id, pseudonym=pseudonym,
                       attempt=attempt, attachment_key=key,
                       media_type="application/octet-stream", size=1,
                       status="captured", digest=digest, filename=filename,
                       file_id=key, now=now)


def _runner(adapter_name, data, filename):
    from api.mirror.extraction import registry
    return registry.load_adapter(adapter_name)(data, filename=filename)


def test_publish_extraction_scrubs_text_and_uses_association_identity(tmp_path):
    publisher, root = _publisher(tmp_path)
    result = extract_text(b"Avery Sample wrote this draft.")
    commit = publish_extraction(publisher=publisher, assignment_id="10", pseudonym="Pikachu",
                                attempt=1, attachment_key="b" * 64, original_digest="c" * 64,
                                result=result, writer_key="writer-a", run_id="run-a")
    facts = [f for f in publisher.store.scan().facts.values()
             if f["kind"] == "attachment_extraction"]
    assert len(facts) == 1
    assert facts[0]["entity_key"] == extraction_entity_key("10", "Pikachu", 1, "b" * 64)
    assert facts[0]["entity_key"] == "extraction:10:Pikachu:1:" + "b" * 64
    text = "".join(block["text"] for block in facts[0]["payload"]["blocks"])
    assert "Avery" not in text and "Sample" not in text
    assert "filename" not in facts[0]["payload"]
    safe_bytes = b"".join(p.read_bytes() for p in (root / "CanvasMirror").rglob("*.json"))
    assert b"Avery" not in safe_bytes
    first_commit_count = len(publisher.store.scan().commits)
    same = publish_extraction(publisher=publisher, assignment_id="10", pseudonym="Pikachu",
                              attempt=1, attachment_key="b" * 64, original_digest="c" * 64,
                              result=result, writer_key="writer-a", run_id="run-b")
    assert same == commit
    assert len(publisher.store.scan().commits) == first_commit_count


def _synthetic_ocr(path, *, timeout):
    return OcrResult(blocks=(OcrBlock("Recognized text", ((0, 0), (9, 0), (9, 4), (0, 4)), 0.9),),
                     model_version="synthetic")


_IMAGE = (document_samples.build_text_png("Recognized text"), {"ocr": _synthetic_ocr})
# One formatted, located sample per required format; each must publish as a fact.
_ADAPTER_SAMPLES = {
    ".docx": (document_samples.build_docx(paragraphs=("Alpha words.", "Beta words."),
                                          bold_word="Beta"), {}),
    ".pptx": (document_samples.build_pptx(), {}),
    ".xlsx": (document_samples.build_xlsx(), {}),
    ".pdf": (document_samples.build_native_pdf(), {}),
    ".jpg": _IMAGE, ".jpeg": _IMAGE, ".png": _IMAGE,
}


def test_every_required_format_has_a_publication_sample():
    assert set(_ADAPTER_SAMPLES) >= registry.REQUIRED_FORMATS


@pytest.mark.parametrize("extension", sorted(_ADAPTER_SAMPLES))
def test_every_adapter_result_publishes_as_an_evidence_fact(tmp_path, extension):
    data, kwargs = _ADAPTER_SAMPLES[extension]
    result = registry.load_adapter(extension)(data, filename=f"sample{extension}", **kwargs)
    publisher, _ = _publisher(tmp_path)
    publish_extraction(publisher=publisher, assignment_id="10", pseudonym="Pikachu",
                       attempt=1, attachment_key="b" * 64, original_digest="c" * 64,
                       result=result, writer_key="writer-a", run_id="run-a")
    facts = [f for f in publisher.store.scan().facts.values()
             if f["kind"] == "attachment_extraction"]
    assert len(facts) == 1 and facts[0]["payload"]["blocks"]


def test_extraction_identity_is_association_not_content_or_version():
    key = extraction_entity_key("10", "Pikachu", 1, "b" * 64)
    assert key == "extraction:10:Pikachu:1:" + "b" * 64
    assert extraction_entity_key("10", "Pikachu", 1, "b" * 64) != \
        extraction_entity_key("10", "Eevee", 1, "b" * 64)
    assert extraction_entity_key("10", "Pikachu", 1, "b" * 64) != \
        extraction_entity_key("10", "Pikachu", 2, "b" * 64)
    assert extraction_entity_key("10", "Pikachu", 1, "b" * 64) != \
        extraction_entity_key("10", "Pikachu", 1, "d" * 64)
    assert extraction_entity_key("10", "Pikachu", None, "b" * 64).endswith(":none:" + "b" * 64)


def test_new_extraction_supersedes_legacy_content_key_for_same_association(tmp_path):
    from api.mirror.evidence_publish import _utc_now

    publisher, _ = _publisher(tmp_path)
    old = extract_text(b"Older extracted text.")
    publish_extraction(publisher=publisher, assignment_id="10", pseudonym="Pikachu",
                       attempt=1, attachment_key="b" * 64, original_digest="c" * 64,
                       result=old, writer_key="writer-a", run_id="run-a")
    current = publisher.store.scan().scopes[(SOURCE, "1", "assignment.extractions", "10")]
    old_fact = next(publisher.store.scan().facts[ref] for ref in current.current_refs)
    payload = old_fact["payload"]
    legacy_key = (f"extraction:10:{payload['original_digest']}:"
                  f"{payload['extractor_version']}:{payload['privacy_policy_revision']}")
    _, legacy_ref = publisher._fact("attachment_extraction", legacy_key, payload)
    publisher.store.publish_commit({
        "schema_version": 1, "source_key": SOURCE, "course_id": "1",
        "scope": "assignment.extractions", "scope_id": "10",
        "writer_key": "writer-a", "run_id": "legacy", "parents": list(current.heads),
        "acquisition_started_at": _utc_now(), "acquisition_finished_at": _utc_now(),
        "mode": "snapshot", "membership_complete": False,
        "record_refs": [legacy_ref], "member_keys": [legacy_key],
        "gaps": [], "watermarks": {},
    })
    newer = extract_text(b"Updated extracted text.")
    newer = ExtractionResult(
        input_digest=newer.input_digest, detected_format=newer.detected_format,
        method=newer.method, availability=newer.availability, blocks=newer.blocks,
        extractor_version="text-2", schema_version=newer.schema_version,
        privacy_policy_revision=newer.privacy_policy_revision,
        partial_reasons=newer.partial_reasons, warnings=newer.warnings,
        processed_units=newer.processed_units, total_units=newer.total_units)
    publish_extraction(publisher=publisher, assignment_id="10", pseudonym="Pikachu",
                       attempt=1, attachment_key="b" * 64, original_digest="c" * 64,
                       result=newer, writer_key="writer-a", run_id="run-b")
    snapshot = publisher.store.scan()
    state = snapshot.scopes[(SOURCE, "1", "assignment.extractions", "10")]
    facts = [snapshot.facts[ref] for ref in state.current_refs]
    assert len(facts) == 1 and facts[0]["payload"]["extractor_version"] == "text-2"
    assert facts[0]["entity_key"] == extraction_entity_key("10", "Pikachu", 1, "b" * 64)


def test_identical_files_for_two_students_publish_two_extractions(tmp_path):
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    data = document_samples.build_docx(paragraphs=("Shared bytes.",))
    digest = hashlib.sha256(data).hexdigest()
    _job(jobs, "b" * 64, pseudonym="Pikachu", digest=digest)
    _job(jobs, "d" * 64, pseudonym="Eevee", digest=digest)
    outcome = extract_captured_attachments(
        publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
        recover_original=lambda _: data, run_adapter=_runner,
        writer_key="writer-a", run_id="run-a")
    facts = [f for f in publisher.store.scan().facts.values()
             if f["kind"] == "attachment_extraction"]
    assert len(facts) == 2 and len({fact["entity_key"] for fact in facts}) == 2
    assert outcome.processed == 2 and outcome.published == 2 and outcome.gaps == ()
    assert jobs.summary()["extraction_done"] == 2
    assert {job.extracted_with for job in jobs.extraction_candidates(limit=10)} == set()
    assert {job.extracted_with for job in jobs.extraction_candidates(limit=10)} == set()


def test_extraction_chunks_drain_all_jobs_newest_first(tmp_path):
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    data = document_samples.build_docx(paragraphs=("Synthetic body.",))
    digest = hashlib.sha256(data).hexdigest()
    for index in range(25):
        _job(jobs, f"{index + 1:064x}", pseudonym="Pikachu", attempt=index + 1,
             digest=digest)
    first = extract_captured_attachments(
        publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
        recover_original=lambda _: data, run_adapter=_runner,
        writer_key="writer-a", run_id="run-a", limit=20)
    second = extract_captured_attachments(
        publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
        recover_original=lambda _: data, run_adapter=_runner,
        writer_key="writer-a", run_id="run-b", limit=20)
    assert first.processed == 20 and second.processed == 5
    assert jobs.summary()["extraction_done"] == 25
    assert jobs.extraction_candidates(limit=20) == ()


def test_failure_outcomes_publish_gaps_and_reopen_for_next_refresh(tmp_path, monkeypatch):
    from api.mirror.extraction import registry

    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    _job(jobs, "b" * 64, filename="unsupported.exe", digest="b" * 64,
         now="2026-01-01T00:00:00Z")
    _job(jobs, "d" * 64, filename="missing.docx", attempt=2, digest="d" * 64,
         now="2026-01-02T00:00:00Z")
    _job(jobs, "e" * 64, filename="timeout.docx", attempt=3, digest="e" * 64,
         now="2026-01-03T00:00:00Z")
    _job(jobs, "f" * 64, filename="adapter.docx", attempt=4, digest="f" * 64,
         now="2026-01-04T00:00:00Z")
    original_load = registry.load_adapter

    def load(name):
        if name == ".docx" and not hasattr(load, "failed_once"):
            load.failed_once = True
            raise ExtractionError("missing_dependency")
        return original_load(name)

    monkeypatch.setattr(registry, "load_adapter", load)
    def recover(digest):
        if digest == "e" * 64:
            raise FileNotFoundError
        return b"ignored"

    outcome = extract_captured_attachments(
        publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
        recover_original=recover,
        run_adapter=lambda *args: (_ for _ in ()).throw(ExtractionError("timeout")),
        writer_key="writer-a", run_id="run-a")
    assert outcome.processed == 4 and outcome.published == 4
    assert set(outcome.gaps) == {"unsupported_type", "missing_dependency", "original_missing", "timeout"}
    assert jobs.summary()["extraction_gaps"] == 4
    snapshot = publisher.store.scan()
    unavailable = [f["payload"] for f in snapshot.facts.values()
                   if f["kind"] == "attachment_extraction" and f["payload"]["availability"] == "unavailable"]
    assert {reason for fact in unavailable for reason in fact["partial_reasons"]} >= {
        "unsupported_type", "missing_dependency", "timeout"}
    associations = [f["payload"] for f in snapshot.facts.values()
                    if f["kind"] == "attachment" and f["payload"]["status"] == "unavailable"]
    assert len(associations) == 1 and associations[0]["original_digest"] is None
    assert jobs.reopen_extractions(lambda filename: "current:1") == 4
    assert jobs.summary()["extraction_needed"] == 4


def test_partial_adapter_result_is_preserved_as_gap(tmp_path):
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    _job(jobs, "b" * 64, filename="partial.txt")
    partial = ExtractionResult(input_digest="c" * 64, detected_format="text", method="native",
                              availability="partial", extractor_version="text-1",
                              partial_reasons=("truncated",))
    outcome = extract_captured_attachments(
        publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
        recover_original=lambda _: b"text", run_adapter=lambda *args: partial,
        writer_key="writer-a", run_id="run-a")
    fact = next(f for f in publisher.store.scan().facts.values()
                if f["kind"] == "attachment_extraction")
    assert fact["payload"]["availability"] == "partial"
    assert fact["payload"]["partial_reasons"] == ["truncated"]
    assert outcome.gaps == ("truncated",)
    assert jobs.summary()["extraction_gaps"] == 1


@pytest.mark.parametrize(("error", "reason"), [
    (RuntimeError("opaque failure"), "corruption"),
    (ExtractionError("recognition_failed"), "recognition_gap"),
])
def test_adapter_failures_publish_sanitized_reasons(tmp_path, error, reason):
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    _job(jobs, "b" * 64, filename="essay.txt")

    def fail(*_args):
        raise error

    outcome = extract_captured_attachments(
        publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
        recover_original=lambda _: b"essay", run_adapter=fail,
        writer_key="writer-a", run_id="run-a")
    fact = next(f for f in publisher.store.scan().facts.values()
                if f["kind"] == "attachment_extraction")
    assert fact["payload"]["availability"] == "unavailable"
    assert fact["payload"]["partial_reasons"] == [reason]
    assert outcome.gaps == (reason,)


def test_adapter_runs_outside_publisher_scope_and_chunk_stops_between_jobs(tmp_path):
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    _job(jobs, "b" * 64, filename="one.txt", attempt=1)
    _job(jobs, "d" * 64, filename="two.txt", attempt=2)
    active = False
    stop = threading.Event()

    @contextmanager
    def scoped(_course_id):
        nonlocal active
        active = True
        try:
            yield publisher
        finally:
            active = False

    def run(_name, _data, _filename):
        assert not active
        stop.set()
        return extract_text(b"ready")

    outcome = extract_captured_attachments(
        publisher_scope=scoped, jobs=jobs, recover_original=lambda _: b"x",
        run_adapter=run, writer_key="writer-a", run_id="run-a", stop_event=stop)
    assert outcome.processed == 1 and outcome.published == 1
    assert jobs.summary()["extraction_done"] == 1
    assert jobs.summary()["extraction_needed"] == 1


def test_publication_failure_never_marks_extraction_done_and_retry_is_idempotent(tmp_path, monkeypatch):
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    _job(jobs, "b" * 64, filename="essay.txt")
    original_commit = publisher.store.publish_commit
    failures = {"left": True}

    def flaky_commit(record):
        if failures["left"]:
            failures["left"] = False
            raise OSError("disk unavailable")
        return original_commit(record)

    monkeypatch.setattr(publisher.store, "publish_commit", flaky_commit)
    args = dict(publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
                recover_original=lambda _: b"essay", run_adapter=_runner,
                writer_key="writer-a", run_id="run-a")
    first = extract_captured_attachments(**args)
    assert first.published == 0 and first.gaps == ("publication_failed",)
    assert jobs.summary()["extraction_needed"] == 1
    second = extract_captured_attachments(**args)
    assert second.published == 1 and jobs.summary()["extraction_done"] == 1


def test_refused_publication_settles_as_a_text_free_gap(tmp_path, monkeypatch):
    # A refusal repeats identically, so the job must settle, not retry every chunk.
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    _job(jobs, "b" * 64, filename="essay.txt")
    verify = publisher.verify_safe

    def refuse_text(record):
        if any(block.get("text") for block in record.get("payload", {}).get("blocks", [])):
            raise PublicationRefused("privacy_refused")
        return verify(record)

    events = []
    monkeypatch.setattr(publisher, "verify_safe", refuse_text)
    monkeypatch.setattr("api.mirror.evidence_extraction.operational_log.emit",
                        lambda event, outcome, **fields: events.append((event, outcome, fields.get("scope"))))
    args = dict(publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
                recover_original=lambda _: b"essay", run_adapter=_runner,
                writer_key="writer-a", run_id="run-a")

    first = extract_captured_attachments(**args)

    assert first.published == 1 and first.gaps == ("publication_refused",)
    assert ("mirror.extraction_publication", "refused", "privacy_refused") in events
    assert jobs.summary()["extraction_gaps"] == 1 and jobs.extraction_candidates(limit=20) == ()
    [fact] = [f for f in publisher.store.scan().facts.values()
              if f["kind"] == "attachment_extraction"]
    assert fact["payload"]["availability"] == "unavailable" and fact["payload"]["blocks"] == []
    assert extract_captured_attachments(**args).processed == 0


def test_completion_record_failure_retries_without_duplicate_commit(tmp_path, monkeypatch):
    publisher, _ = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    _job(jobs, "b" * 64, filename="essay.txt")
    original_record = jobs.record_extraction
    fail = {"once": True}

    def flaky_record(*args, **kwargs):
        if fail["once"]:
            fail["once"] = False
            raise OSError("local completion write failed")
        return original_record(*args, **kwargs)

    monkeypatch.setattr(jobs, "record_extraction", flaky_record)
    args = dict(publisher_scope=lambda course_id: _scope(publisher), jobs=jobs,
                recover_original=lambda _: b"essay", run_adapter=_runner,
                writer_key="writer-a", run_id="run-a")
    first = extract_captured_attachments(**args)
    assert first.gaps == ("publication_failed",)
    assert first.published == 1
    assert first.published == 1
    assert jobs.summary()["extraction_needed"] == 1
    commits = len(publisher.store.scan().commits)
    second = extract_captured_attachments(**args)
    assert second.published == 1 and jobs.summary()["extraction_done"] == 1
    assert len(publisher.store.scan().commits) == commits
