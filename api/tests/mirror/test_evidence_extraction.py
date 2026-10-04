"""Extraction publication, caching, and privacy laws."""
from __future__ import annotations

import hashlib

import pytest

from api.mirror.evidence_extraction import (
    ExtractionCache, extract_captured_attachments, extraction_cache_key,
    extraction_entity_key, publish_extraction,
)
from api.mirror.evidence_jobs import AttachmentJobStore
from api.mirror.evidence_publish import EvidencePublisher
from api.mirror.extraction.docx import extract as extract_docx
from api.mirror.extraction.schema import PRIVACY_POLICY_REVISION
from api.mirror.extraction.text import extract as extract_text
from api.tests.mirror.extraction import document_samples
from api.tests.mirror.test_evidence_publish import SyntheticVault

SOURCE = "a" * 64


def _publisher(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    return EvidencePublisher(workspace_root=root, source_key=SOURCE,
                             course_id="1", vault=SyntheticVault()), root


def test_publish_extraction_scrubs_text_and_omits_filename(tmp_path):
    publisher, root = _publisher(tmp_path)
    result = extract_text(b"Avery Sample wrote this draft.")
    publish_extraction(publisher=publisher, assignment_id="10", pseudonym="Pikachu",
                       attempt=1, attachment_key="b" * 64, original_digest="c" * 64,
                       result=result, writer_key="writer-a", run_id="run-a")
    facts = [f for f in publisher.store.scan().facts.values()
             if f["kind"] == "attachment_extraction"]
    assert len(facts) == 1
    text = "".join(block["text"] for block in facts[0]["payload"]["blocks"])
    assert "Avery" not in text and "Sample" not in text
    assert "filename" not in facts[0]["payload"]
    safe_bytes = b"".join(p.read_bytes() for p in (root / "CanvasMirror").rglob("*.json"))
    assert b"Avery" not in safe_bytes


def test_extraction_entity_key_is_content_and_version_addressed():
    left = extraction_entity_key("10", "c" * 64, "docx-1", 1)
    right = extraction_entity_key("10", "c" * 64, "docx-2", 1)
    assert left != right
    assert extraction_cache_key("c" * 64, "docx-1", 1) != extraction_cache_key("c" * 64, "docx-2", 1)


def _runner(adapter_name, data, filename):
    from api.mirror.extraction import registry
    return registry.load_adapter(adapter_name)(data, filename=filename)


def test_extract_captured_attachments_publishes_and_caches(tmp_path):
    publisher, root = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    data = document_samples.build_docx(paragraphs=("Synthetic body.",))
    digest = hashlib.sha256(data).hexdigest()
    jobs.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                pseudonym="Pikachu", attempt=1, attachment_key="b" * 64,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size=len(data), status="captured", digest=digest, filename="essay.docx")
    cache = ExtractionCache(tmp_path / "extraction.sqlite3")
    outcome = extract_captured_attachments(
        publisher_for=lambda course: publisher, jobs=jobs, cache=cache,
        recover_original=lambda d: data, run_adapter=_runner,
        writer_key="writer-a", run_id="run-a")
    assert outcome.published == 1 and outcome.failed == 0
    # A second pass reuses the cache rather than re-extracting.
    again = extract_captured_attachments(
        publisher_for=lambda course: publisher, jobs=jobs, cache=cache,
        recover_original=lambda d: data, run_adapter=_runner,
        writer_key="writer-a", run_id="run-b")
    assert again.cached == 1 and again.published == 0


def test_one_bad_file_does_not_stop_siblings(tmp_path):
    publisher, root = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    good = document_samples.build_docx(paragraphs=("Good body.",))
    jobs.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                pseudonym="Pikachu", attempt=1, attachment_key="b" * 64,
                media_type="application/octet-stream", size=len(good),
                status="captured", digest=hashlib.sha256(good).hexdigest(),
                filename="good.docx")
    jobs.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                pseudonym="Pikachu", attempt=1, attachment_key="d" * 64,
                media_type="application/octet-stream", size=4,
                status="captured", digest="e" * 64, filename="bad.exe")
    cache = ExtractionCache(tmp_path / "extraction.sqlite3")
    outcome = extract_captured_attachments(
        publisher_for=lambda course: publisher, jobs=jobs, cache=cache,
        recover_original=lambda d: good, run_adapter=_runner,
        writer_key="writer-a", run_id="run-a")
    assert outcome.published == 1
    assert "unsupported_type" in outcome.gaps


def test_stale_extractor_version_reprocesses_only_affected(tmp_path):
    publisher, root = _publisher(tmp_path)
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    data = document_samples.build_docx(paragraphs=("Body.",))
    digest = hashlib.sha256(data).hexdigest()
    jobs.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                pseudonym="Pikachu", attempt=1, attachment_key="b" * 64,
                media_type="application/octet-stream", size=len(data),
                status="captured", digest=digest, filename="essay.docx")
    cache = ExtractionCache(tmp_path / "extraction.sqlite3")
    # Seed a cache entry for a different (older) extractor version.
    cache.record(extraction_cache_key(digest, "docx-0", PRIVACY_POLICY_REVISION),
                 original_digest=digest, extractor_version="docx-0",
                 privacy_revision=PRIVACY_POLICY_REVISION, availability="complete",
                 block_count=1)
    outcome = extract_captured_attachments(
        publisher_for=lambda course: publisher, jobs=jobs, cache=cache,
        recover_original=lambda d: data, run_adapter=_runner,
        writer_key="writer-a", run_id="run-a")
    assert outcome.published == 1 and outcome.cached == 0
