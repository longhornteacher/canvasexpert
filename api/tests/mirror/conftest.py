from __future__ import annotations

import pytest


@pytest.fixture
def evidence_service_workspace(tmp_path, monkeypatch):
    """Safe publication and service maintenance with a synthetic vault."""
    from contextlib import contextmanager
    from api import runtime_paths
    from api.mirror import service, store
    from api.mirror.evidence_acquisition import publish_course_receipt
    from api.mirror.evidence_paths import source_key_for_origin
    from api.mirror.evidence_publish import EvidencePublisher
    from api.tests.mirror.acquisition_samples import SyntheticVault, course_receipt_sample

    root = tmp_path / "workspace"
    source = source_key_for_origin("https://canvas.example.test")
    vault = SyntheticVault()
    import threading
    monkeypatch.setattr(service, "_maintenance_requested", False)
    monkeypatch.setattr(service, "_index_wake", threading.Event())
    monkeypatch.setattr(service, "_work_wake", threading.Event())
    locked = [False]
    monkeypatch.setattr(runtime_paths, "local_cache_dir", lambda: tmp_path / "local")
    monkeypatch.setattr(service.workspace, "workspace_root", lambda: str(root))
    monkeypatch.setattr(service.config, "active_courses", lambda: [{"id": "1"}])
    monkeypatch.setattr(service.config, "get_canvas_base", lambda: "https://canvas.example.test")

    @contextmanager
    def transaction(_root):
        locked[0] = True
        try:
            yield vault
        finally:
            locked[0] = False

    monkeypatch.setattr(store, "_vault_transaction", transaction)

    def publish(variant="full", *, receipt=None):
        receipt = receipt or course_receipt_sample(variant)
        publisher = EvidencePublisher(workspace_root=root, source_key=source,
                                      course_id=receipt.course_id, vault=vault)
        return publish_course_receipt(publisher=publisher, receipt=receipt,
                                      writer_key="writer-a", run_id="run-a")

    return {"root": root, "source": source, "vault": vault,
            "locked": locked, "publish": publish}


@pytest.fixture(autouse=True)
def score_ledger_workspace(tmp_path, monkeypatch):
    """Keep durable score evidence separate from each disposable mirror root."""
    from api.platform_services import workspace
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path / "workspace"))


@pytest.fixture
def submission_history_support():
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

    def row(*, body="First draft", attachments=None, attempt=1):
        return {
            "assignment_id": 82001, "user_id": 991001,
            "attempt": attempt, "submitted_at": "2026-09-01T10:00:00Z",
            "workflow_state": "submitted", "body": body,
            "submission_type": "online_text_entry", "score": None,
            "grade": None, "attachments": list(attachments or []),
        }

    def file_record(file_id="501", payload=b"Original upload"):
        return {"id": file_id, "filename": "student-original.txt",
                "url": f"https://canvas.example.edu/files/{file_id}?download_secret=private",
                "size": len(payload), "content_type": "text/plain"}

    return {"Response": Response, "row": row, "file_record": file_record}


@pytest.fixture
def evidence_factory():
    """Synthetic safe records shared by evidence store and index tests."""
    from api.mirror.evidence_store import EvidenceStore

    source = "a" * 64
    course = "1"

    def store(root, verify_safe=None):
        return EvidenceStore(root, source, course,
                             verify_safe=verify_safe or (lambda record: None),
                             private_diagnostics_root=root.parent / (root.name + "-diagnostics"))

    def fact(kind="submission", entity_key="submission:10:Pikachu", payload=None, **fields):
        if payload is None and kind in {"submission", "attempt_observation"}:
            payload = {
                "assignment_id": "10", "pseudonym": "Pikachu", "attempt": 1,
                "submitted_at": "2026-01-01T00:00:00Z", "body": "Draft",
            }
        return {
            "schema_version": 1, "kind": kind, "source_key": source,
            "course_id": course, "entity_key": entity_key,
            "payload": {**(payload or {}), **fields},
        }

    def commit(refs=(), members=(), scope="assignment.submissions", scope_id="10",
               parents=(), mode="snapshot", complete=True, **changes):
        record = {
            "schema_version": 1, "source_key": source, "course_id": course,
            "scope": scope, "scope_id": scope_id, "writer_key": "writer-a",
            "run_id": "run-1", "parents": list(parents),
            "acquisition_started_at": "2026-01-01T00:00:00Z",
            "acquisition_finished_at": "2026-01-01T00:00:00Z",
            "mode": mode, "membership_complete": complete,
            "record_refs": list(refs), "member_keys": list(members),
            "gaps": [], "watermarks": {},
        }
        record.update(changes)
        return record

    return {"source": source, "course": course, "store": store,
            "fact": fact, "commit": commit}
