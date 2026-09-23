import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api import local_runtime
from api.shared_work import (
    SharedWorkStore,
    WorkItemHeldElsewhere,
    WorkItemStaleLease,
    WorkItemSyncPending,
)
from api.shared_storage import SharedStoreConflictError


def _state(status="ready"):
    return {"session_id": "session-1", "status": status,
            "students": [{"user_id": "synthetic-id", "real_name": "Synthetic Student"}]}


def test_work_item_snapshot_sync_handoff_and_takeover(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    laptop = SharedWorkStore(root=tmp_path)
    laptop.save_snapshot("session-1", _state(), course_id="course-test",
                         assignment_id="assignment-test")
    laptop.save_snapshot("session-1", _state("staged"))

    with pytest.raises(WorkItemHeldElsewhere) as held:
        monkeypatch.setattr(local_runtime, "machine_id", lambda: "DESKTOP-TEST")
        SharedWorkStore(root=tmp_path).acquire("session-1")
    assert held.value.holder == "LAPTOP-TEST"

    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    laptop.release("session-1")
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "DESKTOP-TEST")
    desktop = SharedWorkStore(root=tmp_path)
    summary = desktop.acquire("session-1")
    assert summary["sync_progress"] == {"present": 2, "expected": 2, "complete": True}
    assert desktop.load_snapshot("session-1")["status"] == "staged"


def test_released_work_waits_until_final_events_are_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    laptop = SharedWorkStore(root=tmp_path)
    laptop.save_snapshot("session-1", _state())
    laptop.release("session-1")
    event_path = tmp_path / "_Shared" / "work" / "session-1" / "events.LAPTOP-TEST.jsonl"
    event_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(local_runtime, "machine_id", lambda: "DESKTOP-TEST")
    desktop = SharedWorkStore(root=tmp_path)
    with pytest.raises(WorkItemSyncPending) as pending:
        desktop.acquire("session-1")
    assert (pending.value.present, pending.value.expected) == (0, 1)


def test_stale_takeover_fences_and_quarantines_late_events(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    laptop = SharedWorkStore(root=tmp_path)
    laptop.save_snapshot("session-1", _state())
    laptop.release("session-1")
    lease_path = tmp_path / "_Shared" / "work" / "session-1" / "lease.LAPTOP-TEST.json"
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    lease.update(state="held", heartbeat_at=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat())
    lease_path.write_text(json.dumps(lease), encoding="utf-8")

    monkeypatch.setattr(local_runtime, "machine_id", lambda: "DESKTOP-TEST")
    desktop = SharedWorkStore(root=tmp_path)
    with pytest.raises(WorkItemStaleLease):
        desktop.acquire("session-1")
    desktop.acquire("session-1", confirm_stale=True)
    desktop.save_snapshot("session-1", _state("finished"))

    late = {"v": 1, "ts": "2026-01-01T00:00:00Z", "machine": "LAPTOP-TEST",
            "epoch": 1, "seq": 3, "op": "snapshot", "blob_sha256": "0" * 64}
    with open(tmp_path / "_Shared" / "work" / "session-1" / "events.LAPTOP-TEST.jsonl",
              "a", encoding="utf-8") as handle:
        handle.write(json.dumps(late) + "\n")

    summary = desktop.summary("session-1")
    orphan_path = tmp_path / "_Shared" / "work" / "session-1" / "events.LAPTOP-TEST.orphan.jsonl"
    assert summary["event_count"] == 2
    assert summary["orphan_event_count"] == 1
    assert json.loads(orphan_path.read_text(encoding="utf-8")) == late
    assert desktop.load_snapshot("session-1")["status"] == "finished"


def test_stale_takeover_uses_contiguous_watermark_and_surfaces_sync_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    laptop = SharedWorkStore(root=tmp_path)
    laptop.save_snapshot("session-1", _state("ready"))
    laptop.save_snapshot("session-1", _state("partial"))
    lease_path = tmp_path / "_Shared" / "work" / "session-1" / "lease.LAPTOP-TEST.json"
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    lease.update(state="held", heartbeat_at=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat())
    lease_path.write_text(json.dumps(lease), encoding="utf-8")

    event_path = tmp_path / "_Shared" / "work" / "session-1" / "events.LAPTOP-TEST.jsonl"
    events = event_path.read_text(encoding="utf-8").splitlines()
    assert len(events) == 2
    event_path.write_text(events[0] + "\n", encoding="utf-8")

    monkeypatch.setattr(local_runtime, "machine_id", lambda: "DESKTOP-TEST")
    desktop = SharedWorkStore(root=tmp_path)
    with pytest.raises(WorkItemStaleLease) as stale:
        desktop.acquire("session-1")
    assert (stale.value.present, stale.value.expected) == (1, 2)

    warning = desktop.acquire("session-1", confirm_stale=True)
    assert warning["takeover_warning"] is True
    assert warning["sync_progress"] == {"present": 1, "expected": 2, "complete": False}
    desktop.save_snapshot("session-1", _state("finished"))

    # The missing old event arrives after the new holder wrote sequence 2.
    with open(event_path, "a", encoding="utf-8") as handle:
        handle.write(events[1] + "\n")

    summary = desktop.summary("session-1")
    orphan_path = tmp_path / "_Shared" / "work" / "session-1" / "events.LAPTOP-TEST.orphan.jsonl"
    assert summary["event_count"] == 2
    assert summary["orphan_event_count"] == 1
    assert summary["takeover_warning"] is True
    assert json.loads(orphan_path.read_text(encoding="utf-8")) == json.loads(events[1])
    assert desktop.load_snapshot("session-1")["status"] == "finished"


def test_summary_exposes_progress_without_work_item_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    store = SharedWorkStore(root=tmp_path)
    store.save_snapshot("session-1", _state(), course_id="course-test",
                        assignment_id="assignment-test", label="Synthetic Assignment")

    summary = store.list_items(kind="scoring_session")[0]

    assert summary["holder"] == "LAPTOP-TEST"
    assert "students" not in summary
    assert "label" not in summary
    assert summary["sync_progress"]["complete"] is True


def test_shared_session_snapshot_carries_safe_bundle_and_drops_cache_references(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    bundle_path = tmp_path / "For AI" / "synthetic-bundle.json"
    bundle_path.parent.mkdir(parents=True)
    bundle = {"students": [{"pseudonym": "Bulbasaur", "responses": [
        {"item_id": "item-1", "response": "Synthetic writing."},
    ]}]}
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    store = SharedWorkStore(root=tmp_path)
    session = {
        **_state(),
        "storage_model": "shared_work.v1",
        "mirror_revision": "machine-cache-revision",
        "mirror_snapshot_id": "cache-snapshot",
        "submission_snapshot": "cache-digest",
        "evidence_manifest": "C:/private/cache/manifest.json",
        "privacy_artifacts": {
            "safe_folder": str(bundle_path.parent),
            "safe_bundle": str(bundle_path),
            "safe_students": 1,
        },
    }

    store.save_snapshot("session-1", session)
    event_path = tmp_path / "_Shared" / "work" / "session-1" / "events.LAPTOP-TEST.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    persisted = store._read_blob("session-1", event["blob_sha256"])
    serialized = json.dumps(persisted)
    assert "machine-cache-revision" not in serialized
    assert "cache-snapshot" not in serialized
    assert "C:/private/cache" not in serialized
    assert str(bundle_path) not in serialized
    assert persisted["privacy_artifacts"]["safe_bundle"] == ""

    monkeypatch.setattr(local_runtime, "machine_id", lambda: "DESKTOP-TEST")
    resumed = store.load_snapshot("session-1")
    materialized = resumed["privacy_artifacts"]["safe_bundle"]
    assert json.loads(Path(materialized).read_text(encoding="utf-8")) == bundle


def test_conflicted_work_item_cannot_be_read_or_written(tmp_path, monkeypatch):
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    store = SharedWorkStore(root=tmp_path)
    store.save_snapshot("session-1", _state())
    directory = tmp_path / "_Shared" / "work" / "session-1"
    conflict = directory / "events.LAPTOP-TEST-ONEDRIVE.jsonl"
    conflict.write_text("{}\n", encoding="utf-8")

    with pytest.raises(SharedStoreConflictError):
        store.load_snapshot("session-1")
    with pytest.raises(SharedStoreConflictError):
        store.save_snapshot("session-1", _state("staged"))
