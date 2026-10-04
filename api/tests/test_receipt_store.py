"""Receipt-store schema, atomicity, redaction, and quarantine tests."""

import copy
import json
import threading

import pytest
from starlette.requests import Request

from api.operation_ledger import receipts, storage
from api.operation_ledger import paths
from api.webui.routes.receipts import list_receipts as list_route, receipt_page


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(paths, "private_root", lambda: root)
    return root


def _receipt(receipt_id="r-1"):
    return {
        "version": 1,
        "receipt_id": receipt_id,
        "subject_type": "operation",
        "subject_id": "opaque-operation",
        "kind": "grade.powergrader",
        "attempted_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:00:01Z",
        "status": "applied",
        "targets": [{"target_key": "opaque-target", "object_type": "submission", "private": {"real_name": "Private Learner"}}],
        "private_detail": {"comment": "Private detail"},
    }


def test_empty_round_trip_and_immutable_duplicate(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    assert receipts.list_receipts() == []
    created = receipts.create_receipt(_receipt())
    assert created["receipt_id"] == "r-1"
    assert receipts.get_receipt("r-1")["private_detail"]["comment"] == "Private detail"
    with pytest.raises(storage.ReceiptConflictError):
        receipts.create_receipt(_receipt())


def test_concurrent_creates_are_serialized(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    failures = []
    def create(index):
        try:
            receipts.create_receipt(_receipt(f"r-{index}"))
        except Exception as exc:  # pragma: no cover - assertion below reports it
            failures.append(exc)
    threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert failures == []
    assert len(receipts.list_receipts()) == 8


def test_atomic_failure_preserves_previous_document(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    receipts.create_receipt(_receipt("before"))
    before = paths.receipts_file().read_text(encoding="utf-8")
    monkeypatch.setattr(storage.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        receipts.create_receipt(_receipt("after"))
    assert paths.receipts_file().read_text(encoding="utf-8") == before


def test_corrupt_and_unknown_version_are_quarantined(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    path = paths.receipts_file()
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert receipts.list_receipts() == []
    assert list((root / "quarantine").glob("*.corrupt"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 99, "receipts": []}), encoding="utf-8")
    assert receipts.list_receipts() == []
    assert len(list((root / "quarantine").glob("*.corrupt"))) == 2


def test_list_is_pii_minimized_detail_hydrates_private_data(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    item = _receipt("safe-id")
    receipts.create_receipt(item)
    response = list_route()
    body = json.loads(response.body)
    text = json.dumps(body)
    assert body["ok"] is True
    assert "Private Learner" not in text and "Private detail" not in text
    assert body["receipts"][0]["detail_url"] == "/api/receipts/safe-id"
    assert receipts.get_receipt("safe-id")["targets"][0]["private"]["real_name"] == "Private Learner"


def test_historical_routine_receipt_renders(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    receipt = _receipt("routine-receipt")
    receipt["subject_type"] = "routine"
    receipt["kind"] = "sis.grade.bridge"
    receipts.create_receipt(receipt)

    response = receipt_page(
        Request({"type": "http", "method": "GET", "path": "/receipts/routine-receipt",
                 "headers": []}),
        "routine-receipt",
    )

    assert response.status_code == 200
