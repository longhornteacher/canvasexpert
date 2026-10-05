"""Activation checkpoint, rollback, and resumable evidence-work recovery laws."""
from __future__ import annotations

import hashlib

import pytest

from api.mirror import evidence_activation
from api.mirror.evidence_activation import (
    activate_read_authority, _write_verified_activation, read_activation, recover_evidence_work,
    rollback_read_authority,
)
from api.mirror.evidence_extraction import ExtractionCache
from api.mirror.evidence_jobs import AttachmentJobStore
from api.mirror.evidence_paths import source_key_for_origin
from api.mirror.evidence_publish import EvidencePublisher
from api.tests.mirror.extraction import document_samples
from api.tests.mirror.test_evidence_publish import SyntheticVault

ORIGIN = "https://canvas.example.edu"
SOURCE = source_key_for_origin(ORIGIN)


def test_activation_defaults_inactive_and_requires_coverage(tmp_path):
    state = read_activation(source_key=SOURCE, workspace_root=tmp_path)
    assert state.state == "inactive"
    with pytest.raises(ValueError):
        activate_read_authority(source_key=SOURCE, workspace_root=tmp_path,
                                report_path=tmp_path / "caller-coverage.json",
                                activated_at="2026-01-01T00:00:00Z")


def test_activation_and_rollback_preserve_coverage(tmp_path):
    coverage = {"courses": {"1": {"verification_state": "verified",
        "verified_import": True, "index_revision": "a" * 64,
        "required_scopes": ["course.context", "course.roster", "course.sections",
                             "course.assignments", "assignment.submissions"]}}}
    _write_verified_activation(source_key=SOURCE, workspace_root=tmp_path,
                               coverage=coverage, activated_at="2026-01-01T00:00:00Z")
    assert read_activation(source_key=SOURCE, workspace_root=tmp_path).state == "active"
    rolled = rollback_read_authority(source_key=SOURCE, workspace_root=tmp_path,
                                     reason="index_rebuild", rolled_back_at="2026-01-02T00:00:00Z")
    assert rolled.state == "rolled_back"
    assert rolled.coverage == coverage
    assert rolled.activated_at == "2026-01-01T00:00:00Z"


def test_corrupt_activation_record_requires_repair(tmp_path):
    from api.mirror.evidence_paths import local_source_root
    path = local_source_root(SOURCE, tmp_path) / "activation.v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    state = read_activation(source_key=SOURCE, workspace_root=tmp_path)
    assert state.state == "repair_required"
    assert state.reason == "invalid_checkpoint"


def test_recovery_resumes_capture_and_extraction(tmp_path, monkeypatch):
    from api import runtime_paths
    monkeypatch.setattr(runtime_paths, "local_cache_dir", lambda: tmp_path / "_System")
    root = tmp_path / "workspace"
    root.mkdir()
    publisher = EvidencePublisher(workspace_root=root, source_key=SOURCE,
                                  course_id="1", vault=SyntheticVault())
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    data = document_samples.build_docx(paragraphs=("Recovered body.",))
    digest = hashlib.sha256(data).hexdigest()
    jobs.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                pseudonym="Pikachu", attempt=1, attachment_key="b" * 64,
                media_type="application/octet-stream", size=len(data),
                status="captured", digest=digest, filename="essay.docx")
    cache = ExtractionCache(tmp_path / "extraction.sqlite3")

    def runner(adapter_name, data, filename):
        from api.mirror.extraction import registry
        return registry.load_adapter(adapter_name)(data, filename=filename)

    result = recover_evidence_work(
        source_key=SOURCE, workspace_root=root, jobs=jobs, cache=cache,
        publisher_for=lambda course: publisher,
        recover_original=lambda d: data, run_adapter=runner,
        writer_key="writer-a", run_id="run-a",
        capture_chunk=lambda: {"processed": 0, "captured": 0, "failed": 0, "skipped": 0})
    assert result["extraction"]["published"] == 1
    assert result["remaining"] == 0


def test_recovery_survives_capture_failure(tmp_path, monkeypatch):
    from api import runtime_paths
    monkeypatch.setattr(runtime_paths, "local_cache_dir", lambda: tmp_path / "_System")
    root = tmp_path / "workspace"
    root.mkdir()
    publisher = EvidencePublisher(workspace_root=root, source_key=SOURCE,
                                  course_id="1", vault=SyntheticVault())
    jobs = AttachmentJobStore(tmp_path / "control.sqlite3")
    cache = ExtractionCache(tmp_path / "extraction.sqlite3")

    def broken_capture():
        raise RuntimeError("synthetic capture failure")

    result = recover_evidence_work(
        source_key=SOURCE, workspace_root=root, jobs=jobs, cache=cache,
        publisher_for=lambda course: publisher,
        recover_original=lambda d: b"", run_adapter=lambda *a: None,
        writer_key="writer-a", run_id="run-a", capture_chunk=broken_capture)
    assert result["capture"]["error"] == "capture_failed"
    assert result["extraction"]["processed"] == 0
