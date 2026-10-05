"""Durable, bounded, resumable attachment-capture job laws."""
from __future__ import annotations

import hashlib
import os

import pytest

from api.mirror import evidence_jobs
from api.mirror.evidence_jobs import (
    AttachmentJobStore, JobError, job_id, run_attachment_chunk,
)
from api.mirror.original_archive import archive_original, blob_path, recover_original


ORIGIN = "https://canvas.example.edu"
SOURCE = "a" * 64


class Response:
    def __init__(self, chunks, declared=None):
        self.chunks = list(chunks)
        self.headers = ({"Content-Length": str(declared)} if declared is not None else {})
        self.closed = False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


def _store(tmp_path):
    return AttachmentJobStore(tmp_path / "control.sqlite3")


def _ensure(store, key, *, size=10, attempt=1, pseudonym="Pikachu", media="text/plain"):
    return store.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                        pseudonym=pseudonym, attempt=attempt, attachment_key=key,
                        media_type=media, size=size)


def _harness(tmp_path, payloads, *, origin=ORIGIN):
    """Return (store, run_kwargs) with a real private archive as the sink."""
    store = _store(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    calls = {"downloads": [], "archived": []}

    def resolve_url(job):
        return f"{origin}/files/{job.attachment_key}?download=1"

    def stream_get(url):
        key = url.split("/files/")[1].split("?")[0]
        entry = payloads.get(key)
        if entry is None:
            return None, "HTTP 404"
        if isinstance(entry, BaseException):
            return None, str(entry)
        chunks, declared = entry
        return Response(chunks, declared=declared), None

    def original_exists(digest):
        return blob_path(workspace, digest).exists()

    def store_original(job, digest, temp_path):
        if temp_path:
            archive_original(workspace, temp_path, expected_digest=digest)
        calls["archived"].append((job.attachment_key, digest))

    kwargs = dict(resolve_url=resolve_url, stream_get=stream_get, canvas_origin=origin,
                  original_exists=original_exists, store_original=store_original,
                  staging_dir=tmp_path / "staging")
    return store, workspace, calls, kwargs


def test_more_than_chunk_limit_eventually_all_processed(tmp_path):
    payloads = {}
    store = _store(tmp_path)
    for index in range(25):
        key = f"{index:064x}"
        payloads[key] = ([f"payload-{index}".encode()], len(f"payload-{index}"))
        _ensure(store, key, size=len(f"payload-{index}"))
    _, workspace, calls, kwargs = _harness(tmp_path, payloads)
    first = run_attachment_chunk(store, **kwargs)
    assert first["processed"] == evidence_jobs.MAX_DOWNLOADS_PER_CHUNK
    assert first["remaining"] == 5
    second = run_attachment_chunk(store, **kwargs)
    assert second["processed"] == 5
    assert second["remaining"] == 0
    assert store.summary()["captured"] == 25
    assert len(calls["archived"]) == 25


def test_oversize_input_is_explicit_and_does_not_starve_siblings(tmp_path):
    store = _store(tmp_path)
    big = "b" * 64
    small = "c" * 64
    _ensure(store, big, size=evidence_jobs.MAX_FILE_BYTES + 1)
    _ensure(store, small, size=5)
    payloads = {small: ([b"small"], 5)}
    _, _, _, kwargs = _harness(tmp_path, payloads)
    result = run_attachment_chunk(store, **kwargs)
    assert store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, big)).status == "too_large"
    assert store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, small)).status == "captured"
    assert result["skipped"] == 1 and result["captured"] == 1


def test_partial_stream_is_never_captured_and_retry_succeeds(tmp_path):
    store = _store(tmp_path)
    key = "d" * 64
    _ensure(store, key, size=8)
    broken = {key: ([b"part", OSError("interrupted")], 8)}
    _, workspace, _, kwargs = _harness(tmp_path, broken)
    run_attachment_chunk(store, now="2026-01-01T00:00:00Z", **kwargs)
    job = store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key))
    assert job.status == "failed"
    assert not list((workspace / "_System").rglob("*.zip")) if (workspace / "_System").exists() else True

    good = {key: ([b"complete"], 8)}
    _, _, _, kwargs = _harness(tmp_path, good)
    run_attachment_chunk(store, now="2026-01-01T01:00:00Z", **kwargs)
    job = store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key))
    assert job.status == "captured"
    assert recover_original(workspace, job.digest) == b"complete"


def test_same_bytes_under_two_associations_deduplicate(tmp_path):
    store = _store(tmp_path)
    left, right = "e" * 64, "f" * 64
    _ensure(store, left, size=7)
    _ensure(store, right, size=7)
    payloads = {left: ([b"shared!"], 7), right: ([b"shared!"], 7)}
    _, workspace, calls, kwargs = _harness(tmp_path, payloads)
    run_attachment_chunk(store, **kwargs)
    digests = {store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key)).digest
               for key in (left, right)}
    assert len(digests) == 1
    assert len(list((workspace / "_System" / "Archive" / "CanvasMirror Originals" /
                     "blobs").rglob("*.zip"))) == 1


def test_same_filename_different_bytes_remain_distinct(tmp_path):
    store = _store(tmp_path)
    left, right = "1" * 64, "2" * 64
    _ensure(store, left, size=4)
    _ensure(store, right, size=4)
    payloads = {left: ([b"aaaa"], 4), right: ([b"bbbb"], 4)}
    _, _, _, kwargs = _harness(tmp_path, payloads)
    run_attachment_chunk(store, **kwargs)
    assert (store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, left)).digest
            != store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, right)).digest)


def test_expired_url_reacquires_through_resolver(tmp_path):
    store = _store(tmp_path)
    key = "3" * 64
    _ensure(store, key, size=4)
    calls = {"resolved": 0}

    def resolve_url(job):
        calls["resolved"] += 1
        return f"{ORIGIN}/files/{job.attachment_key}?fresh=1"

    def stream_get(url):
        return Response([b"data"], declared=4), None

    _, workspace, _, _ = _harness(tmp_path, {})
    run_attachment_chunk(
        store, resolve_url=resolve_url, stream_get=stream_get, canvas_origin=ORIGIN,
        original_exists=lambda d: blob_path(workspace, d).exists(),
        store_original=lambda job, digest, temp: archive_original(workspace, temp, expected_digest=digest),
        staging_dir=tmp_path / "staging")
    assert calls["resolved"] == 1
    assert store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key)).status == "captured"


def test_foreign_origin_is_refused_without_download(tmp_path):
    store = _store(tmp_path)
    key = "4" * 64
    _ensure(store, key, size=4)
    downloads = []

    def stream_get(url):
        downloads.append(url)
        return Response([b"data"], declared=4), None

    _, workspace, _, _ = _harness(tmp_path, {})
    run_attachment_chunk(
        store, resolve_url=lambda job: "https://evil.example.net/files/x",
        stream_get=stream_get, canvas_origin=ORIGIN,
        original_exists=lambda d: False,
        store_original=lambda *a: None, staging_dir=tmp_path / "staging")
    assert downloads == []
    assert store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key)).status == "foreign_origin"


def test_restart_reuses_completed_original_without_redownload(tmp_path):
    store = _store(tmp_path)
    key = "5" * 64
    _ensure(store, key, size=6)
    payloads = {key: ([b"stable"], 6)}
    _, workspace, _, kwargs = _harness(tmp_path, payloads)
    run_attachment_chunk(store, **kwargs)
    job = store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key))
    assert job.status == "captured"

    # A fresh queue row (as another machine would rebuild) with the same digest
    # must reuse the archived original rather than download again.
    store2 = _store(tmp_path / "second")
    store2.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                  pseudonym="Pikachu", attempt=1, attachment_key=key,
                  media_type="text/plain", size=6, status="pending", digest=job.digest)
    downloads = []

    def stream_get(url):
        downloads.append(url)
        return Response([b"stable"], declared=6), None

    run_attachment_chunk(
        store2, resolve_url=lambda job: f"{ORIGIN}/files/{key}",
        stream_get=stream_get, canvas_origin=ORIGIN,
        original_exists=lambda d: blob_path(workspace, d).exists(),
        store_original=lambda job, digest, temp: None,
        staging_dir=tmp_path / "staging2")
    assert downloads == []
    assert store2.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key)).status == "captured"


def test_reconstruct_from_facts_rebuilds_queue_without_resetting_local(tmp_path):
    from api.mirror.evidence_store import EvidenceStore

    store = _store(tmp_path)
    key = "6" * 64
    _ensure(store, key, size=4)
    store.record(job_id(SOURCE, "1", "10", "Pikachu", 1, key), status="captured",
                 digest="7" * 64)

    class Snapshot:
        facts = {
            "ref": {"kind": "attachment", "source_key": SOURCE, "course_id": "1",
                    "payload": {"assignment_id": "10", "pseudonym": "Pikachu",
                                "attempt": 1, "attachment_key": key,
                                "media_type": "text/plain", "size": 4,
                                "status": "pending", "original_digest": None}},
        }

    created = store.reconstruct_from_facts(Snapshot())
    assert created == 0  # existing row preserved, not reset to pending
    assert store.get(job_id(SOURCE, "1", "10", "Pikachu", 1, key)).status == "captured"


def test_backoff_defers_retry_until_due(tmp_path):
    store = _store(tmp_path)
    key = "8" * 64
    _ensure(store, key, size=4)
    identifier = job_id(SOURCE, "1", "10", "Pikachu", 1, key)
    store.record(identifier, status="failed", error="transport_failed",
                 now="2026-01-01T00:00:00Z")
    job = store.get(identifier)
    assert job.attempts == 1 and job.next_attempt_at is not None
    assert store.claim(now="2026-01-01T00:00:10Z") == ()
    assert len(store.claim(now="2026-01-01T00:01:00Z")) == 1


def test_terminal_status_never_regresses(tmp_path):
    store = _store(tmp_path)
    key = "9" * 64
    _ensure(store, key, size=4)
    identifier = job_id(SOURCE, "1", "10", "Pikachu", 1, key)
    store.record(identifier, status="too_large", error="over_file_limit")
    store.record(identifier, status="pending")
    assert store.get(identifier).status == "too_large"


def test_invalid_inputs_are_refused(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(JobError):
        _ensure(store, "a" * 64, size=-1)
    with pytest.raises(JobError):
        _ensure(store, "a" * 64, attempt=0)
    with pytest.raises(JobError):
        store.claim(limit=0)
    with pytest.raises(JobError):
        store.record("missing", status="captured")


def test_enqueue_from_receipt_creates_one_job_per_attachment(tmp_path):
    from api.mirror.evidence_acquisition import (
        CourseAcquisitionReceipt, ScopeReceipt,
    )
    from api.mirror.evidence_jobs import enqueue_from_receipt
    store = _store(tmp_path)
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z", (
        ScopeReceipt("assignment.submissions", "10", (
            {"user_id": "991001", "assignment_id": "10", "attempt": 2,
             "submitted_at": "2026-01-02T00:00:00Z",
             "attachments": [{"id": 501, "filename": "draft.docx", "size": 12,
                              "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}],
             "submission_history": [
                 {"attempt": 1, "submitted_at": "2026-01-01T00:00:00Z",
                  "attachments": [{"id": 500, "filename": "old.docx", "size": 8}]},
             ]},
        ), True),
    ))
    created = enqueue_from_receipt(store, receipt, source_key=SOURCE,
                                   pseudonym_for=lambda raw: "Pikachu" if raw else None)
    assert created == 2
    assert store.summary()["total"] == 2
    # Re-enqueue is idempotent.
    assert enqueue_from_receipt(store, receipt, source_key=SOURCE,
                                pseudonym_for=lambda raw: "Pikachu") == 0


def test_captured_attachment_republishes_digest_without_filename(tmp_path):
    from api.mirror.evidence_acquisition import (
        CourseAcquisitionReceipt, ScopeReceipt, publish_captured_attachment,
        publish_course_receipt,
    )
    from api.mirror.evidence_jobs import enqueue_from_receipt
    from api.mirror.evidence_publish import EvidencePublisher
    from api.tests.mirror.acquisition_samples import SyntheticVault

    publisher = EvidencePublisher(workspace_root=tmp_path, source_key=SOURCE,
                                  course_id="1", vault=SyntheticVault())
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z", (
        ScopeReceipt("assignment.submissions", "10", (
            {"user_id": "991001", "assignment_id": "10", "attempt": 1,
             "submitted_at": "2026-01-01T00:00:00Z", "body": "Draft",
             "attachments": [{"id": 501, "filename": "private-name.docx", "size": 12}]},
        ), True),
    ))
    publish_course_receipt(publisher=publisher, receipt=receipt,
                           writer_key="writer-a", run_id="run-a")
    pending = [f for f in publisher.store.scan().facts.values() if f["kind"] == "attachment"]
    assert len(pending) == 1
    assert pending[0]["payload"]["status"] == "pending"
    assert "filename" not in pending[0]["payload"]

    store = _store(tmp_path)
    enqueue_from_receipt(store, receipt, source_key=SOURCE,
                         pseudonym_for=lambda raw: "Pikachu")
    job = store.claim()[0]
    store.record(job.job_id, status="captured", digest="a" * 64)
    job = store.get(job.job_id)
    publish_captured_attachment(publisher=publisher, job=job, digest="a" * 64,
                                writer_key="writer-a", run_id="run-b")
    captured = [f for f in publisher.store.scan().facts.values()
                if f["kind"] == "attachment" and f["payload"]["status"] == "captured"]
    assert len(captured) == 1
    assert captured[0]["payload"]["original_digest"] == "a" * 64
    assert "filename" not in captured[0]["payload"]
    safe_bytes = b"".join(p.read_bytes() for p in (tmp_path / "CanvasMirror").rglob("*.json"))
    assert b"private-name.docx" not in safe_bytes


def test_resolver_reacquires_fresh_url_from_stable_file_id(tmp_path):
    from api.mirror.evidence_jobs import resolve_canvas_file_url
    store = _store(tmp_path)
    job = store.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                       pseudonym="Pikachu", attempt=1, attachment_key="a" * 64,
                       media_type="text/plain", size=4, file_id="501")
    calls = []

    def canvas_get(path):
        calls.append(path)
        return {"url": "https://canvas.example.edu/files/501/download?verifier=fresh"}, None

    assert resolve_canvas_file_url(job, canvas_get=canvas_get) == \
        "https://canvas.example.edu/files/501/download?verifier=fresh"
    assert calls == ["/api/v1/files/501"]
    # A job without a stable file id cannot be reacquired.
    bare = store.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                        pseudonym="Pikachu", attempt=1, attachment_key="b" * 64,
                        media_type="text/plain", size=4)
    assert resolve_canvas_file_url(bare, canvas_get=canvas_get) is None


def test_original_sink_archives_associates_and_publishes(tmp_path):
    from api.mirror.evidence_acquisition import (
        CourseAcquisitionReceipt, ScopeReceipt, publish_course_receipt,
    )
    from api.mirror.evidence_jobs import enqueue_from_receipt, make_original_sink
    from api.mirror.evidence_publish import EvidencePublisher
    from api.mirror.original_archive import recover_original
    from api.tests.mirror.acquisition_samples import SyntheticVault

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    publisher = EvidencePublisher(workspace_root=workspace, source_key=SOURCE,
                                  course_id="1", vault=SyntheticVault())
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z", (
        ScopeReceipt("assignment.submissions", "10", (
            {"user_id": "991001", "assignment_id": "10", "attempt": 1,
             "submitted_at": "2026-01-01T00:00:00Z", "body": "Draft",
             "attachments": [{"id": 501, "filename": "essay.docx", "size": 6}]},
        ), True),
    ))
    publish_course_receipt(publisher=publisher, receipt=receipt,
                           writer_key="writer-a", run_id="run-a")
    store = _store(tmp_path)
    enqueue_from_receipt(store, receipt, source_key=SOURCE,
                         pseudonym_for=lambda raw: "Pikachu")
    job = store.claim()[0]
    source = tmp_path / "essay.docx"
    source.write_bytes(b"essay!")
    digest = hashlib.sha256(b"essay!").hexdigest()
    sink = make_original_sink(workspace_root=workspace, publisher=publisher,
                              writer_key="writer-a", run_id="run-b")
    sink(job, digest, str(source))
    assert recover_original(workspace, digest) == b"essay!"
    captured = [f for f in publisher.store.scan().facts.values()
                if f["kind"] == "attachment" and f["payload"]["status"] == "captured"]
    assert captured and captured[0]["payload"]["original_digest"] == digest
    associations = list((workspace / "_System" / "Archive" / "CanvasMirror Originals" /
                         "associations").rglob("*.json"))
    assert len(associations) == 1
    assert b"essay.docx" in associations[0].read_bytes()


def test_missing_private_original_does_not_erase_published_safe_text(tmp_path):
    from api.mirror.evidence_acquisition import (
        CourseAcquisitionReceipt, ScopeReceipt, publish_course_receipt,
    )
    from api.mirror.evidence_publish import EvidencePublisher
    from api.tests.mirror.acquisition_samples import SyntheticVault

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    publisher = EvidencePublisher(workspace_root=workspace, source_key=SOURCE,
                                  course_id="1", vault=SyntheticVault())
    receipt = CourseAcquisitionReceipt("1", "2026-01-04T00:00:00Z", "2026-01-04T00:01:00Z", (
        ScopeReceipt("assignment.submissions", "10", (
            {"user_id": "991001", "assignment_id": "10", "attempt": 1,
             "submitted_at": "2026-01-01T00:00:00Z", "body": "Readable draft",
             "attachments": [{"id": 501, "filename": "essay.docx", "size": 6}]},
        ), True),
    ))
    publish_course_receipt(publisher=publisher, receipt=receipt,
                           writer_key="writer-a", run_id="run-a")
    snapshot = publisher.store.scan()
    # The submission text is readable even though no original bytes exist yet.
    submissions = snapshot.scopes[(SOURCE, "1", "assignment.submissions", "10")]
    assert submissions.membership_complete
    assert any(snapshot.facts[ref]["payload"].get("body") == "Readable draft"
               for ref in submissions.current_refs)
    attachments = snapshot.scopes[(SOURCE, "1", "assignment.attachments", "10")]
    assert attachments.membership_complete
    assert all(snapshot.facts[ref]["payload"]["status"] == "pending"
               for ref in attachments.current_refs)


def test_service_chunk_drains_queue_through_coordinated_transport(tmp_path, monkeypatch):
    from api.mirror import service
    from api.mirror.evidence_jobs import AttachmentJobStore
    from api.mirror.evidence_paths import control_store_path, source_key_for_origin
    from api.platform_services import config, workspace

    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    monkeypatch.setattr(config, "get_canvas_base", lambda: ORIGIN)
    source_key = source_key_for_origin(ORIGIN)
    from api.mirror import store as mirror_store
    with mirror_store._vault_transaction(root) as vault:
        pseudo = vault.get_or_assign("991001", real_name="Synthetic Learner")
        vault.save()
    store = AttachmentJobStore(control_store_path(source_key, root))
    store.ensure(source_key=source_key, course_id="1", assignment_id="10",
                 pseudonym=pseudo, attempt=1, attachment_key="a" * 64,
                 media_type="text/plain", size=5, file_id="501")

    monkeypatch.setattr(service, "canvas_get",
                        lambda path: ({"url": f"{ORIGIN}/files/501/download?fresh=1"}, None))
    monkeypatch.setattr(service, "canvas_stream_get",
                        lambda url: (Response([b"hello"], declared=5), None))
    result = service.run_attachment_capture_chunk()
    assert result["captured"] == 1
    assert store.summary()["captured"] == 1
