"""Integrated evidence read-path scenarios across the real pipeline.

These examples compose the real publisher, index, attachment queue, extraction,
query service, and MCP read tools. Only Canvas transport and the supervised
adapter subprocess are stubbed, so the tests exercise production behavior
without live Canvas, teacher files, or a spawned worker process.

They cover the brief's acceptance criteria together rather than one seam each:
an old shared descriptor beside a fresh workspace, a delayed second course and
a corrupt index recovering locally, honest partial/future-version reads,
partition switching and restart without activation, and failure-after-success
with no calendar backlog.
"""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from api.mcp_server import tools
from api.mirror import service
from api.mirror.evidence_acquisition import publish_course_receipt
from api.mirror.evidence_jobs import AttachmentJobStore, enqueue_from_receipt
from api.mirror.evidence_paths import (
    control_store_path, local_source_root, source_key_for_origin,
)
from api.mirror.evidence_publish import EvidencePublisher
from api.platform_services import workspace
from api import runtime_paths
from api.tests.mirror.acquisition_samples import (
    SyntheticVault, course_receipt_sample, synthetic_documents,
)

ORIGIN = "https://canvas.example.test"
SOURCE = source_key_for_origin(ORIGIN)


class _Response:
    """Minimal streaming response for the stubbed Canvas transport."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.payload

    def close(self):
        self.closed = True


@pytest.fixture
def read_path_world(tmp_path, monkeypatch):
    """One workspace, one machine-local partition, and a stubbed Canvas.

    Returns helpers that publish a named receipt, enqueue and drain the
    attachment queue, and rebuild the local index, all through the real
    production owners.
    """
    root = tmp_path / "workspace"
    local = tmp_path / "local"
    vault = SyntheticVault()
    documents = synthetic_documents()
    downloads: list[str] = []
    gets: list[str] = []

    monkeypatch.setattr(service, "_maintenance_requested", False)
    monkeypatch.setattr(service, "_index_wake", threading.Event())
    monkeypatch.setattr(service, "_work_wake", threading.Event())
    monkeypatch.setattr(runtime_paths, "local_cache_dir", lambda: local)
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))

    @contextmanager
    def transaction(_root):
        yield vault

    monkeypatch.setattr(service.store, "_vault_transaction", transaction)
    monkeypatch.setattr(tools, "_open_vault", lambda: (vault, None))
    monkeypatch.setattr(tools.config, "get_canvas_base", lambda: ORIGIN)
    monkeypatch.setattr(tools.config, "active_courses",
                        lambda: [{"id": "1", "name": "Synthetic ELA"}])
    monkeypatch.setattr(tools.config, "saved_courses",
                        lambda: [{"id": "1", "name": "Synthetic ELA"}])
    monkeypatch.setattr(tools.config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(service.config, "mirror_enabled", lambda: True)
    monkeypatch.setattr(service.config, "token_is_set", lambda: True)

    def canvas_get(path):
        gets.append(str(path))
        file_id = str(path).rsplit("/", 1)[-1]
        return {"url": f"{ORIGIN}/files/{file_id}"}, None

    def canvas_stream_get(url):
        file_id = str(url).rsplit("/", 1)[-1]
        downloads.append(file_id)
        return _Response(documents.get(file_id, b"synthetic bytes")), None

    monkeypatch.setattr(service, "canvas_get", canvas_get)
    monkeypatch.setattr(service, "canvas_stream_get", canvas_stream_get)

    def in_process_adapter(adapter_name, path, **_kwargs):
        from api.mirror.extraction import registry
        data = Path(path).read_bytes()
        return registry.load_adapter(adapter_name)(data, filename=str(path))

    monkeypatch.setattr("api.mirror.extraction.supervisor.run_adapter",
                        in_process_adapter)

    def publish(variant="read_path", *, receipt=None, run_id="run-a"):
        receipt = receipt or course_receipt_sample(variant)
        publisher = EvidencePublisher(workspace_root=root, source_key=SOURCE,
                                      course_id=receipt.course_id, vault=vault)
        publish_course_receipt(publisher=publisher, receipt=receipt,
                               writer_key="writer-a", run_id=run_id)
        return receipt

    def enqueue(receipt):
        jobs = AttachmentJobStore(control_store_path(SOURCE, root))
        return enqueue_from_receipt(jobs, receipt, source_key=SOURCE,
                                    pseudonym_for=lambda raw: vault.get_or_assign(str(raw)))

    def drain():
        """Run bounded capture and extraction chunks until the queue settles."""
        for _ in range(20):
            captured = service.run_attachment_capture_chunk()
            extracted = service.run_extraction_chunk()
            if not (captured.get("processed") or extracted.get("processed")):
                break

    def index():
        return service.run_index_maintenance(root=root, source_key=SOURCE)

    return {"root": root, "local": local, "vault": vault, "documents": documents,
            "downloads": downloads, "gets": gets, "publish": publish,
            "enqueue": enqueue, "drain": drain, "index": index}


def _set_courses(monkeypatch, course_ids):
    courses = [{"id": cid, "name": f"Course {cid}"} for cid in course_ids]
    monkeypatch.setattr(tools.config, "active_courses", lambda: courses)
    monkeypatch.setattr(tools.config, "saved_courses", lambda: courses)


def test_old_descriptor_fresh_workspace_text_and_attachments_reach_mcp(
        read_path_world, monkeypatch):
    """An old shared reader descriptor beside a fresh workspace does not block
    acquisition; text and supported attachment blocks reach MCP, and an
    unreadable sibling stays an explicit gap."""
    world = read_path_world
    # A stale shared descriptor from the retired publication path is ignored.
    legacy = world["root"] / "CanvasMirror" / "reader.v1.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"schema_version": 1, "views": {}}), encoding="utf-8")

    receipt = world["publish"]("read_path")
    world["enqueue"](receipt)
    world["drain"]()
    world["index"]()

    submissions = tools.get_submissions("1", "10")
    assert submissions["ok"] is True, submissions
    rows = [dict(zip(submissions["submissions"]["columns"], row))
            for row in submissions["submissions"]["rows"]]
    assert any("Latest synthetic draft." in (row.get("text") or "") for row in rows)

    evidence = tools.get_assignment_evidence("1", "10", view="attachments")
    assert evidence["ok"] is True, evidence
    extractions = [record["payload"] for record in evidence["records"]
                   if record["index_view"] == "attachment_extractions"]
    assert extractions, evidence
    docx = next(payload for payload in extractions
                if payload["availability"] == "complete")
    assert any("Synthetic thesis sentence." in block["text"] for block in docx["blocks"])
    # The unreadable sibling is an explicit gap, never an empty success.
    assert any(payload["availability"] == "unavailable" for payload in extractions)
    # No private filename or URL leaked into the safe read.
    serialized = json.dumps(evidence)
    assert "essay.docx" not in serialized and "broken.pdf" not in serialized


def test_second_course_delay_and_corrupt_index_recover_locally(
        read_path_world, monkeypatch):
    """A delayed second course and a corrupt index both recover from local safe
    files with zero Canvas calls; independent courses keep honest coverage."""
    world = read_path_world
    _set_courses(monkeypatch, ["1", "2"])
    world["publish"]("read_path")
    world["index"]()

    # Course 2 has not arrived yet: its read refuses with a refresh hint, while
    # course 1 stays readable.
    missing = tools.get_roster("2")
    assert missing["ok"] is False
    assert missing["code"] == "evidence_not_acquired"
    assert tools.get_roster("1")["ok"] is True

    # Course 2's safe files arrive later; local maintenance makes them visible
    # without any acquisition.
    world["publish"]("second_course")
    gets_before = len(world["gets"])
    world["index"]()
    assert len(world["gets"]) == gets_before
    assert tools.get_roster("2")["ok"] is True

    # Corrupt the disposable index; maintenance rebuilds it from safe files with
    # zero Canvas calls and both courses remain readable.
    index_path = local_source_root(SOURCE, world["root"]) / "query.sqlite3"
    index_path.write_bytes(b"not a sqlite database")
    gets_before = len(world["gets"])
    world["index"]()
    assert len(world["gets"]) == gets_before
    assert tools.get_roster("1")["ok"] is True
    assert tools.get_roster("2")["ok"] is True


def test_partial_and_future_version_evidence_reads_honestly(
        read_path_world, monkeypatch):
    """Partial submissions, a future-version record, empty-complete vs never
    acquired, and paginated history all read honestly."""
    world = read_path_world
    world["publish"]("partial_submissions")
    world["index"]()

    partial = tools.get_submissions("1", "10")
    assert partial["ok"] is True
    assert partial["coverage"]["state"] == "incomplete"
    assert "membership_incomplete" in partial["warnings"]

    # A future-version commit is diagnosed as update-required, not corruption,
    # and the resolver refuses with actionable guidance.
    commit_dir = (world["root"] / "CanvasMirror" / "sources" / SOURCE
                  / "courses" / "1" / "commits")
    future = json.loads(next(commit_dir.rglob("*.json")).read_text(encoding="utf-8"))
    future["schema_version"] = 99
    future_dir = commit_dir / "ff"
    future_dir.mkdir(parents=True, exist_ok=True)
    (future_dir / ("f" * 64 + ".json")).write_text(
        json.dumps(future), encoding="utf-8")
    world["index"]()
    refusal = tools.get_roster("1")
    assert refusal["ok"] is False
    assert refusal["code"] == "evidence_update_required"

    # Remove the future record; an empty-complete scope differs from never
    # acquired, and history pages within one pinned revision.
    (future_dir / ("f" * 64 + ".json")).unlink()
    world["publish"]("empty_submissions", run_id="run-empty")
    world["index"]()
    empty = tools.get_submissions("1", "10")
    assert empty["ok"] is True
    assert empty["coverage"]["state"] == "complete"
    assert empty["submissions"]["rows"] == []

    world["publish"]("read_path", run_id="run-history")
    world["index"]()
    first = tools.get_submissions("1", "10", history=True, limit=1)
    assert first["ok"] is True
    assert first["revision"]
    assert len(first["attempts"]) == 1


def test_switching_partitions_and_restart_need_no_activation(
        read_path_world, monkeypatch, tmp_path):
    """Two machine-local partitions over one shared safe root both index the
    same evidence; a restart needs no activation, and no seeded identity
    reaches the safe files, index, or agent output."""
    world = read_path_world
    world["publish"]("read_path")
    world["index"]()

    # Partition B is a second machine-local cache over the same safe root.
    partition_b = tmp_path / "local-b"
    monkeypatch.setattr(runtime_paths, "local_cache_dir", lambda: partition_b)
    assert not (partition_b / "CanvasMirror").exists()
    world["index"]()
    assert tools.get_roster("1")["ok"] is True

    # A restart (fresh resolver) still needs no activation checkpoint.
    service, source_key, refusal = tools._evidence_reader()
    assert service is not None and refusal is None

    # Seeded real identifiers never reach the safe root, index, or output.
    safe_bytes = b"".join(path.read_bytes()
                          for path in (world["root"] / "CanvasMirror").rglob("*.json"))
    for secret in (b"Avery Sample", b"Morgan Sample", b"synthetic-user-01",
                   b"synthetic-user-02"):
        assert secret not in safe_bytes
    output = json.dumps(tools.get_roster("1"))
    assert "Avery Sample" not in output and "synthetic-user-01" not in output


def test_failure_after_success_and_no_calendar_backlog(
        read_path_world, monkeypatch):
    """A failed refresh keeps last success and reports the stage; a requested
    assignment progresses ahead of older backlog; gradebook totals exclude an
    incomplete assignment and say so; older evidence stays readable."""
    world = read_path_world
    from api.mirror import store as mirror_store

    _set_courses(monkeypatch, ["1"])
    monkeypatch.setattr(service.config, "course_display_name", lambda _cid: "Course 1")
    world["publish"]("read_path")
    world["index"]()

    # A failed refresh keeps last success and reports the failed stage.
    mirror_store.begin_refresh("1", operation_id="op-1", root=str(world["root"]))
    mirror_store.finish_refresh("1", operation_id="op-1", ok=True,
                                finished_at="2026-10-05T10:00:00Z",
                                root=str(world["root"]))
    mirror_store.begin_refresh("1", operation_id="op-2", root=str(world["root"]))
    mirror_store.finish_refresh("1", operation_id="op-2", ok=False,
                                finished_at="2026-10-05T11:00:00Z",
                                error_code="publication_incomplete",
                                failure_stage="publication", root=str(world["root"]))
    status = service.status()
    refresh = status["courses"][0]["refresh"]
    assert refresh["state"] == "failed"
    assert refresh["failure_stage"] == "publication"
    assert refresh["last_success_at"] == "2026-10-05T10:00:00Z"

    # A requested assignment's newly created jobs progress ahead of older
    # backlog jobs (newest-first), with no calendar classifier.
    jobs = AttachmentJobStore(control_store_path(SOURCE, world["root"]))
    for ordinal in range(30):
        jobs.ensure(source_key=SOURCE, course_id="1", assignment_id="99",
                    pseudonym="Pikachu", attempt=1,
                    attachment_key=hashlib.sha256(f"old-{ordinal}".encode()).hexdigest(),
                    media_type="text/plain", size=4, filename="old.txt",
                    file_id=str(9000 + ordinal),
                    now=f"2026-01-01T00:{ordinal:02d}:00Z")
    requested = jobs.ensure(source_key=SOURCE, course_id="1", assignment_id="10",
                            pseudonym="Pikachu", attempt=1,
                            attachment_key=hashlib.sha256(b"requested").hexdigest(),
                            media_type="text/plain", size=4, filename="new.txt",
                            file_id="9500", now="2026-02-01T00:00:00Z")
    claimed = jobs.claim(limit=1)
    assert claimed and claimed[0].job_id == requested.job_id

    # Gradebook totals exclude an incomplete assignment and say so.
    world["publish"]("partial_submissions", run_id="run-partial")
    world["index"]()
    snapshot = tools.get_gradebook_snapshot("1")
    assert snapshot["ok"] is True
    assert "assignments_excluded_from_totals" in snapshot["warnings"]
    assert snapshot["coverage"]["excluded_assignment_ids"]

    # Older evidence remains readable; no date-based refusal.
    assert tools.get_submissions("1", "10")["ok"] is True
