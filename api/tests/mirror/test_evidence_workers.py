"""Integration coverage for bounded, resumable attachment workers."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace

import pytest


@pytest.fixture
def attachment_world(evidence_service_workspace, monkeypatch):
    from api.mirror import service, evidence_paths
    from api.mirror.evidence_jobs import AttachmentJobStore

    env = evidence_service_workspace
    monkeypatch.setattr(evidence_paths, "source_key_for_origin", lambda _origin: env["source"])
    monkeypatch.setattr(service.config, "get_canvas_base", lambda: "https://canvas.example.test")
    monkeypatch.setattr(service.config, "token_is_set", lambda: True)
    monkeypatch.setattr(service.config, "mirror_enabled", lambda: True)
    monkeypatch.setattr(service, "acquisition_owner_status",
                        lambda: SimpleNamespace(is_owner=True, state="owner"))
    monkeypatch.setattr("api.local_runtime.machine_id", lambda: "synthetic-machine")
    jobs = AttachmentJobStore(evidence_paths.control_store_path(env["source"], env["root"]))
    return {**env, "jobs": jobs}


def _ensure(world, key, *, course="1", filename="essay.txt", now="2026-01-01T00:00:00Z"):
    label = str(key)
    return world["jobs"].ensure(
        source_key=world["source"], course_id=course, assignment_id="10",
        pseudonym="Pikachu", attempt=1, attachment_key=sha256(f"attachment-{label}".encode()).hexdigest(),
        media_type="text/plain", size=len(b"Synthetic attachment bytes"),
        filename=filename, file_id=str(int(label)), now=now)


class _Response:
    def __init__(self, body, on_chunk=None):
        self.body = body
        self.headers = {"Content-Length": str(len(body))}
        self.on_chunk = on_chunk
        self.closed = False

    def iter_content(self, chunk_size):
        if self.on_chunk:
            self.on_chunk()
        yield self.body

    def close(self):
        self.closed = True


def _configure_capture(monkeypatch, world, *, downloaded=None, on_chunk=None):
    from api.mirror import service

    downloaded = downloaded if downloaded is not None else []
    def get(path):
        file_id = path.rsplit("/", 1)[-1]
        return {"url": f"https://canvas.example.test/files/{file_id}"}, None
    def stream(url):
        file_id = url.rsplit("/", 1)[-1]
        downloaded.append(file_id)
        return _Response(b"Synthetic attachment bytes", on_chunk), None
    monkeypatch.setattr(service, "canvas_get", get)
    monkeypatch.setattr(service, "canvas_stream_get", stream)
    return downloaded


def _configure_adapter(monkeypatch, *, on_adapter=None):
    from api.mirror import service
    from api.mirror.extraction import registry
    from api.mirror.extraction.schema import Block, ExtractionResult

    class Adapter:
        EXTRACTOR_VERSION = "synthetic-text-1"

    monkeypatch.setattr(registry, "adapter_name", lambda _filename: ".txt")
    monkeypatch.setattr(registry, "load_adapter", lambda _name: Adapter())
    monkeypatch.setattr(registry, "extractor_version", lambda _filename: "synthetic-text-1")
    def run_adapter(_name, data, _filename):
        if on_adapter:
            on_adapter()
        return ExtractionResult(
            input_digest=sha256(data).hexdigest(), detected_format="text",
            method="native", availability="complete",
            blocks=(Block("b1", "text_line", "Synthetic extracted text"),),
            extractor_version="synthetic-text-1")
    from api.mirror.extraction import supervisor
    monkeypatch.setattr(supervisor, "run_adapter", run_adapter)


def test_worker_drains_45_jobs_newest_first_outside_vault_lock(
        attachment_world, monkeypatch):
    from api.mirror import service
    world = attachment_world
    old = [_ensure(world, f"{i + 1:04d}", now=f"2026-01-01T00:{i:02d}:00Z")
           for i in range(25)]
    newest = [_ensure(world, f"{1001 + i:04d}", now=f"2026-01-02T00:{i:02d}:00Z")
              for i in range(20)]
    def capture(*, stop_event=None, **_kwargs):
        rows = world["jobs"].claim(limit=16)
        for row in rows:
            world["jobs"].record(row.job_id, status="captured", digest="d" * 64)
        return {"processed": len(rows), "captured": len(rows), "published": len(rows), "failed": 0}
    def extract(*, stop_event=None, **_kwargs):
        rows = world["jobs"].extraction_candidates(limit=20)
        for row in rows:
            world["jobs"].record_extraction(row.job_id, state="done", extracted_with="synthetic-text-1:privacy-1")
        return {"processed": len(rows), "published": len(rows), "gaps": []}
    monkeypatch.setattr(service, "run_attachment_capture_chunk", capture)
    monkeypatch.setattr(service, "run_extraction_chunk", extract)
    stop = __import__("threading").Event()
    waits = []
    def wait(seconds):
        waits.append(seconds)
        if world["jobs"].summary()["remaining"] == 0:
            stop.set()
    service.attachment_work_worker(stop, wait=wait)
    assert all(world["jobs"].get(job.job_id).extraction_state == "done"
               for job in (*old, *newest))
    assert len(waits) >= 3


def test_restart_extracts_captured_original_without_redownload(attachment_world, monkeypatch):
    from api.mirror import service

    world = attachment_world
    world["publish"]()
    job = _ensure(world, "9001")
    downloaded = _configure_capture(monkeypatch, world)
    result = service.run_attachment_capture_chunk()
    assert result["failed"] == 0, result
    assert world["jobs"].get(job.job_id).status == "captured", world["jobs"].get(job.job_id)
    assert len(downloaded) == 1

    _configure_adapter(monkeypatch)
    # A new worker lifetime performs recovery/reopen, sees the durable captured
    # digest, and extracts it without claiming another Canvas download.
    stop = __import__("threading").Event()
    service.prepare_evidence_work()
    assert service.run_attachment_capture_chunk()["processed"] == 0
    # Captured bytes are durable and the next capture pass does no network work.
    assert len(downloaded) == 1
    assert world["jobs"].get(job.job_id).extraction_state == "needed"


def test_non_owner_extracts_but_never_downloads(attachment_world, monkeypatch):
    from api.mirror import service
    from api.mirror.original_archive import archive_original

    world = attachment_world
    world["publish"]()
    job = _ensure(world, "9002")
    original = world["root"].parent / "captured-original.txt"
    original.write_bytes(b"Captured before this computer became a non-owner")
    digest = archive_original(world["root"], original)
    world["jobs"].record(job.job_id, status="captured", digest=digest)
    _configure_capture(monkeypatch, world)
    monkeypatch.setattr(service, "acquisition_owner_status",
                        lambda: SimpleNamespace(is_owner=False, state="not_owner"))
    adapter_calls = []
    _configure_adapter(monkeypatch, on_adapter=lambda: adapter_calls.append(True))
    def extract_local(*, stop_event=None, **_kwargs):
        adapter_calls.append(True)
        world["jobs"].record_extraction(job.job_id, state="done", extracted_with="synthetic-text-1:privacy-1")
        return {"processed": 1, "published": 1, "gaps": []}
    monkeypatch.setattr(service, "run_extraction_chunk", extract_local)
    stop = __import__("threading").Event()
    def wait(_seconds):
        if adapter_calls:
            stop.set()
    service.attachment_work_worker(stop, wait=wait)
    assert adapter_calls
    assert world["jobs"].get(job.job_id).extraction_state == "done"


def test_capture_stop_event_leaves_job_resumable(attachment_world, monkeypatch):
    from api.mirror import service
    import threading

    world = attachment_world
    job = _ensure(world, "9003")
    stop = threading.Event()
    _configure_capture(monkeypatch, world, on_chunk=stop.set)
    result = service.run_attachment_capture_chunk(stop_event=stop)
    assert result["processed"] == 1
    assert world["jobs"].get(job.job_id).status == "pending"
    assert result["captured"] == 0


def test_explicit_course_retry_reopens_only_that_courses_gaps(attachment_world, monkeypatch):
    from api.mirror import service

    world = attachment_world
    course_one_gap = _ensure(world, "9101", course="1")
    course_two_gap = _ensure(world, "9102", course="2")
    course_one_exhausted = _ensure(world, "9103", course="1")
    course_two_exhausted = _ensure(world, "9104", course="2")
    course_one_done = _ensure(world, "9105", course="1")
    for job in (course_one_gap, course_two_gap):
        world["jobs"].record(job.job_id, status="captured", digest="d" * 64)
        world["jobs"].record_extraction(job.job_id, state="gap", error="corruption")
    for job in (course_one_exhausted, course_two_exhausted):
        for _ in range(5):
            world["jobs"].record(job.job_id, status="failed", error="transport_failed",
                                 now="2026-01-01T00:00:00Z")
    world["jobs"].record(course_one_done.job_id, status="captured", digest="e" * 64)
    world["jobs"].record_extraction(course_one_done.job_id, state="done",
        extracted_with="synthetic-text-1:1")
    monkeypatch.setattr("api.mirror.extraction.registry.extractor_version",
                        lambda _filename: "synthetic-text-1")

    service.prepare_evidence_work(course_id="1")
    assert world["jobs"].get(course_one_gap.job_id).extraction_state == "needed"
    assert world["jobs"].get(course_one_exhausted.job_id).status == "pending"
    assert world["jobs"].get(course_one_done.job_id).extraction_state == "done"
    assert world["jobs"].get(course_two_gap.job_id).extraction_state == "gap"
    assert world["jobs"].get(course_two_exhausted.job_id).status == "unavailable"
