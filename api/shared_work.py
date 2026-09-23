"""Append-only, leased work items in the private shared workspace.

The work item stores complete state snapshots as immutable content-addressed
blobs. Its journal contains only sequence numbers and blob digests, so a second
machine can resume the same teacher-reviewed state after the shared files sync.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import copy
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api import local_runtime, runtime_paths
from api.platform_services import workspace
from api.shared_storage import (
    SharedStoreConflictError, append_jsonl, assert_store_writable,
    create_json_exclusive, scan_conflicts, store_conflicts,
)
from api.storage_support import atomic_write_json, interprocess_lock


_WORK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
LEASE_STALE_AFTER = timedelta(minutes=5)
HEARTBEAT_INTERVAL_SECONDS = 30


class WorkItemError(RuntimeError):
    code = "work_item_error"


class WorkItemNotFound(WorkItemError):
    code = "work_item_not_found"


class WorkItemHeldElsewhere(WorkItemError):
    code = "work_item_held_elsewhere"

    def __init__(self, holder: str, heartbeat_at: str):
        self.holder = holder
        self.heartbeat_at = heartbeat_at
        super().__init__(self.code)


class WorkItemStaleLease(WorkItemError):
    code = "work_item_stale_lease_confirmation_required"

    def __init__(self, holder: str, heartbeat_at: str, present=0, expected=0):
        self.holder = holder
        self.heartbeat_at = heartbeat_at
        self.present = int(present)
        self.expected = int(expected)
        super().__init__(self.code)


class WorkItemSyncPending(WorkItemError):
    code = "work_item_sync_pending"

    def __init__(self, present: int, expected: int):
        self.present = present
        self.expected = expected
        super().__init__(self.code)


def _canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class SharedWorkStore:
    """Read, lease, hand off, and append snapshots for private work items."""

    def __init__(self, *, root=None):
        shared = workspace.shared_work_root(root)
        if not shared:
            raise WorkItemError("workspace_not_configured")
        self.workspace_root = root
        self.root = Path(shared)
        self._is_production = root is None

    def _item_dir(self, work_id: str) -> Path:
        value = str(work_id or "")
        if not _WORK_ID_RE.fullmatch(value):
            raise WorkItemNotFound("work_item_not_found")
        return self.root / value

    def _lock_path(self, work_id: str) -> Path:
        digest = hashlib.sha256(str(self._item_dir(work_id).resolve()).encode("utf-8")).hexdigest()[:20]
        return runtime_paths.local_app_dir() / "locks" / f"work-{digest}.lock"

    @contextmanager
    def _lock(self, work_id: str):
        with interprocess_lock(self._lock_path(work_id)):
            yield

    def _manifest(self, work_id: str) -> dict:
        directory = self._item_dir(work_id)
        conflicts = store_conflicts(directory, root=self.workspace_root)
        if conflicts:
            raise SharedStoreConflictError(conflicts)
        path = directory / "manifest.json"
        try:
            with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkItemNotFound("work_item_not_found") from exc
        if (not isinstance(value, dict) or value.get("version") != 1
                or str(value.get("work_id") or "") != str(work_id)):
            raise WorkItemError("work_item_manifest_invalid")
        return value

    def _lease_paths(self, work_id: str) -> list[Path]:
        return sorted(self._item_dir(work_id).glob("lease.*.json"), key=lambda path: path.name)

    def _leases(self, work_id: str) -> list[dict]:
        values = []
        for path in self._lease_paths(work_id):
            try:
                with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                    value = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("epoch"), int):
                values.append(value)
        return values

    def _effective_lease(self, work_id: str) -> dict | None:
        leases = self._leases(work_id)
        if not leases:
            return None
        leases.sort(key=lambda item: (int(item.get("epoch") or 0),
                                      str(item.get("heartbeat_at") or ""),
                                      str(item.get("machine") or "")))
        highest = int(leases[-1].get("epoch") or 0)
        same_epoch = [item for item in leases if int(item.get("epoch") or 0) == highest]
        owners = {str(item.get("machine") or "") for item in same_epoch}
        if len(owners) > 1:
            raise WorkItemError("work_item_lease_conflict")
        return same_epoch[-1]

    def _write_lease(self, work_id: str, lease: dict) -> None:
        directory = self._item_dir(work_id)
        assert_store_writable(directory, root=self.workspace_root)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"lease.{lease['machine']}.json"
        atomic_write_json(path, lease)

    def _raw_events(self, work_id: str) -> list[dict]:
        directory = self._item_dir(work_id)
        events = []
        if not directory.is_dir():
            raise WorkItemNotFound("work_item_not_found")
        scan_conflicts(self.workspace_root)
        for path in sorted(directory.glob("events.*.jsonl"), key=lambda item: item.name):
            if path.name.endswith(".orphan.jsonl"):
                continue
            try:
                with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if (not isinstance(event, dict) or event.get("v") != 1
                                or not isinstance(event.get("seq"), int)
                                or not isinstance(event.get("epoch"), int)
                                or not isinstance(event.get("blob_sha256"), str)):
                            raise WorkItemError("work_item_event_invalid")
                        events.append(event)
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkItemError("work_item_events_unreadable") from exc
        return events

    def _quarantine_late_events(self, work_id: str, events: list[dict]) -> list[dict]:
        leases = sorted(self._leases(work_id), key=lambda item: int(item.get("epoch") or 0))
        cutoffs = []
        for lease in leases:
            source = str(lease.get("takeover_from_machine") or "")
            if source:
                cutoffs.append((int(lease.get("epoch") or 0), source,
                                int(lease.get("takeover_after_seq") or 0)))
        late: dict[str, list[dict]] = {}
        for event in events:
            for epoch, source, cutoff in cutoffs:
                if (str(event.get("machine") or "") == source
                        and int(event.get("epoch") or 0) < epoch
                        and int(event.get("seq") or 0) > cutoff):
                    late.setdefault(source, []).append(event)
                    break
        if late:
            late_fingerprints = set()
            for machine, machine_events in late.items():
                orphan_path = self._item_dir(work_id) / f"events.{machine}.orphan.jsonl"
                known = set()
                if orphan_path.is_file():
                    try:
                        with open(workspace.extended_path(str(orphan_path)), encoding="utf-8") as handle:
                            known = {hashlib.sha256(_canonical_json(json.loads(line))).hexdigest()
                                     for line in handle if line.strip()}
                    except (OSError, json.JSONDecodeError):
                        raise WorkItemError("work_item_orphan_journal_unreadable")
                additions = []
                for event in machine_events:
                    fingerprint = hashlib.sha256(_canonical_json(event)).hexdigest()
                    late_fingerprints.add(fingerprint)
                    if fingerprint not in known:
                        additions.append(event)
                        known.add(fingerprint)
                if additions:
                    assert_store_writable(self._item_dir(work_id), root=self.workspace_root)
                    payload = b"".join((_canonical_json(event) + b"\n") for event in additions)
                    orphan_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(workspace.extended_path(str(orphan_path)), "ab", buffering=0) as handle:
                        handle.write(payload)
                        os.fsync(handle.fileno())
            events = [event for event in events
                      if hashlib.sha256(_canonical_json(event)).hexdigest() not in late_fingerprints]
        return events

    def _events(self, work_id: str) -> list[dict]:
        events = self._quarantine_late_events(work_id, self._raw_events(work_id))
        # A higher fencing epoch wins any duplicate sequence left by a stale
        # writer. The lower-epoch copy is handled as late/orphaned above.
        by_seq = {}
        for event in events:
            seq = int(event["seq"])
            previous = by_seq.get(seq)
            if previous is None or int(event["epoch"]) > int(previous["epoch"]):
                by_seq[seq] = event
            elif int(event["epoch"]) == int(previous["epoch"]):
                if event != previous:
                    raise WorkItemError("work_item_sequence_conflict")
        return [by_seq[seq] for seq in sorted(by_seq)]

    def _write_blob(self, work_id: str, state: dict) -> str:
        payload = _canonical_json(state)
        digest = hashlib.sha256(payload).hexdigest()
        path = self._item_dir(work_id) / "blobs" / digest
        create_json_exclusive(path, state, root=self.workspace_root)
        return digest

    def _read_blob(self, work_id: str, digest: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise WorkItemError("work_item_blob_invalid")
        path = self._item_dir(work_id) / "blobs" / digest
        try:
            with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkItemError("work_item_blob_missing") from exc
        if not isinstance(state, dict) or hashlib.sha256(_canonical_json(state)).hexdigest() != digest:
            raise WorkItemError("work_item_blob_digest_mismatch")
        return state

    def _portable_snapshot(self, work_id: str, state: dict) -> dict:
        """Move SAFE bundle bytes into blobs and remove machine-cache pointers."""
        snapshot = copy.deepcopy(state)
        artifacts = snapshot.get("privacy_artifacts")
        bundle_path = artifacts.get("safe_bundle") if isinstance(artifacts, dict) else None
        if bundle_path:
            try:
                with open(workspace.extended_path(str(bundle_path)), encoding="utf-8") as handle:
                    bundle = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkItemError("work_item_safe_bundle_unreadable") from exc
            if not isinstance(bundle, dict):
                raise WorkItemError("work_item_safe_bundle_invalid")
            snapshot.setdefault("work_artifacts", {})["safe_bundle_sha256"] = self._write_blob(work_id, bundle)
            artifacts["safe_bundle"] = ""
            artifacts["safe_folder"] = ""
        # A shared work snapshot is self-contained. Local mirror revisions,
        # cache paths and cached submission digests are not continuation inputs.
        for key in ("mirror_revision", "mirror_snapshot_id", "submission_snapshot",
                    "submission_snapshot_count", "evidence_manifest"):
            snapshot.pop(key, None)
        return snapshot

    def _materialize_snapshot(self, work_id: str, state: dict) -> dict:
        digest = (state.get("work_artifacts") or {}).get("safe_bundle_sha256")
        if digest:
            bundle = self._read_blob(work_id, str(digest))
            directory = runtime_paths.local_cache_dir() / "work-items" / work_id
            path = directory / f"{digest}.json"
            if not path.is_file():
                atomic_write_json(path, bundle)
            artifacts = state.get("privacy_artifacts")
            if not isinstance(artifacts, dict):
                artifacts = {}
                state["privacy_artifacts"] = artifacts
            artifacts["safe_bundle"] = str(path)
            artifacts["safe_folder"] = str(directory)
        return state

    def _next_seq(self, work_id: str, events: list[dict]) -> int:
        return max((int(event["seq"]) for event in events), default=0) + 1

    @staticmethod
    def _contiguous_event_count(events: list[dict]) -> int:
        available = {int(event["seq"]) for event in events}
        count = 0
        while count + 1 in available:
            count += 1
        return count

    def _append_snapshot(self, work_id: str, state: dict) -> dict:
        if not isinstance(state, dict):
            raise WorkItemError("work_item_state_invalid")
        lease = self._effective_lease(work_id)
        machine = local_runtime.machine_id()
        if (not lease or lease.get("state") != "held"
                or lease.get("machine") != machine
                or int(lease.get("epoch") or 0) <= 0):
            if lease and lease.get("state") == "held":
                raise WorkItemHeldElsewhere(str(lease.get("machine") or ""),
                                            str(lease.get("heartbeat_at") or ""))
            raise WorkItemError("work_item_lease_required")
        self._heartbeat_unlocked(work_id, lease)
        events = self._events(work_id)
        portable = self._portable_snapshot(work_id, state)
        digest = self._write_blob(work_id, portable)
        seq = self._next_seq(work_id, events)
        event = {"v": 1, "ts": _now(), "machine": machine,
                 "epoch": int(lease["epoch"]), "seq": seq,
                 "op": "snapshot", "blob_sha256": digest}
        append_jsonl(self._item_dir(work_id) / f"events.{machine}.jsonl", event,
                     root=self.workspace_root)
        current = self._effective_lease(work_id)
        if (not current or current.get("machine") != machine
                or int(current.get("epoch") or 0) != int(lease.get("epoch") or 0)
                or current.get("state") != "held"):
            raise WorkItemHeldElsewhere(str((current or {}).get("machine") or ""),
                                        str((current or {}).get("heartbeat_at") or ""))
        current["final_event_count"] = seq
        current["heartbeat_at"] = _now()
        self._write_lease(work_id, current)
        return {"seq": seq, "blob_sha256": digest}

    def _heartbeat_unlocked(self, work_id: str, lease: dict) -> None:
        current = self._effective_lease(work_id)
        if (not current or current.get("machine") != lease.get("machine")
                or int(current.get("epoch") or 0) != int(lease.get("epoch") or 0)
                or current.get("state") != "held"):
            raise WorkItemHeldElsewhere(
                str((current or {}).get("machine") or ""),
                str((current or {}).get("heartbeat_at") or ""),
            )
        updated = dict(current)
        updated["heartbeat_at"] = _now()
        self._write_lease(work_id, updated)

    def save_snapshot(self, work_id: str, state: dict, *, kind="scoring_session",
                      course_id="", assignment_id="", label="") -> dict:
        directory = self._item_dir(work_id)
        with self._lock(work_id):
            if not (directory / "manifest.json").exists():
                directory.mkdir(parents=True, exist_ok=True)
                machine = local_runtime.machine_id()
                manifest = {
                    "version": 1, "work_id": str(work_id), "kind": str(kind),
                    "course_id": str(course_id or ""),
                    "assignment_id": str(assignment_id or ""),
                    "label": str(label or ""), "created_by": machine,
                    "created_at": _now(),
                }
                created = create_json_exclusive(directory / "manifest.json", manifest,
                                                root=self.workspace_root)
                if not created:
                    self._manifest(work_id)
                self.acquire(work_id)
            elif not any(self._leases(work_id)):
                self.acquire(work_id)
            return self._append_snapshot(work_id, state)

    def load_snapshot(self, work_id: str) -> dict | None:
        self._manifest(work_id)
        events = self._events(work_id)
        if not events:
            return None
        latest = events[-1]
        state = self._materialize_snapshot(
            work_id, self._read_blob(work_id, str(latest["blob_sha256"]))
        )
        lease = self._effective_lease(work_id)
        machine = local_runtime.machine_id()
        if lease and lease.get("state") == "held" and lease.get("machine") == machine:
            try:
                self._heartbeat_unlocked(work_id, lease)
            except WorkItemError:
                pass
        return state

    def require_owner(self, work_id: str) -> dict:
        """Ensure this machine holds the active lease before mutating work."""
        with self._lock(work_id):
            self._manifest(work_id)
            current = self._effective_lease(work_id)
            machine = local_runtime.machine_id()
            if current and current.get("state") == "held":
                if current.get("machine") == machine:
                    self._heartbeat_unlocked(work_id, current)
                    return self.summary(work_id)
                heartbeat = _parse_time(str(current.get("heartbeat_at") or ""))
                if heartbeat is None or datetime.now(timezone.utc) - heartbeat > LEASE_STALE_AFTER:
                    raise WorkItemStaleLease(str(current.get("machine") or ""),
                                             str(current.get("heartbeat_at") or ""))
                raise WorkItemHeldElsewhere(str(current.get("machine") or ""),
                                            str(current.get("heartbeat_at") or ""))
            if current and current.get("machine") not in {"", machine}:
                raise WorkItemError("work_item_takeover_required")
            return self.acquire(work_id)

    def acquire(self, work_id: str, *, confirm_stale=False) -> dict:
        with self._lock(work_id):
            self._manifest(work_id)
            events = self._events(work_id)
            machine = local_runtime.machine_id()
            current = self._effective_lease(work_id)
            now = datetime.now(timezone.utc)
            if current and current.get("state") == "held":
                if current.get("machine") == machine:
                    self._heartbeat_unlocked(work_id, current)
                    return self.summary(work_id)
                heartbeat = _parse_time(str(current.get("heartbeat_at") or ""))
                stale = heartbeat is None or now - heartbeat > LEASE_STALE_AFTER
                if not stale:
                    raise WorkItemHeldElsewhere(str(current.get("machine") or ""),
                                                str(current.get("heartbeat_at") or ""))
                if not confirm_stale:
                    expected = int(current.get("final_event_count") or 0)
                    raise WorkItemStaleLease(str(current.get("machine") or ""),
                                             str(current.get("heartbeat_at") or ""),
                                             self._contiguous_event_count(events), expected)
            expected = int((current or {}).get("final_event_count") or 0)
            if current and current.get("state") == "released":
                available = {int(event["seq"]) for event in events}
                present = sum(1 for seq in range(1, expected + 1) if seq in available)
                if present < expected:
                    raise WorkItemSyncPending(present, expected)
            # On stale takeover only the contiguous prefix is safe. Any later
            # old-holder event that syncs afterward is orphaned beyond this
            # fencing watermark instead of being silently merged.
            latest_seq = self._contiguous_event_count(events)
            epoch = int((current or {}).get("epoch") or 0) + 1
            lease = {
                "version": 1, "machine": machine, "pid": os.getpid(),
                "epoch": epoch, "lease_at": _now(), "heartbeat_at": _now(),
                "state": "held", "final_event_count": latest_seq,
            }
            if current and current.get("machine") != machine:
                lease["takeover_from_machine"] = str(current.get("machine") or "")
                lease["takeover_after_seq"] = latest_seq
                if current.get("state") == "held":
                    lease["takeover_expected_event_count"] = expected
            self._write_lease(work_id, lease)
            if self._is_production:
                _heartbeat_service.register(self, str(work_id))
            return self.summary(work_id)

    def release(self, work_id: str) -> dict:
        with self._lock(work_id):
            current = self._effective_lease(work_id)
            machine = local_runtime.machine_id()
            if not current or current.get("machine") != machine or current.get("state") != "held":
                return self.summary(work_id)
            events = self._events(work_id)
            released = dict(current)
            released.update(state="released", heartbeat_at=_now(),
                            final_event_count=max((int(event["seq"]) for event in events), default=0))
            self._write_lease(work_id, released)
            _heartbeat_service.unregister(self, str(work_id))
            return self.summary(work_id)

    def _orphan_count(self, work_id: str) -> int:
        count = 0
        for path in self._item_dir(work_id).glob("events.*.orphan.jsonl"):
            try:
                with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                    count += sum(1 for line in handle if line.strip())
            except OSError:
                continue
        return count

    def summary(self, work_id: str) -> dict:
        manifest = self._manifest(work_id)
        events = self._events(work_id)
        lease = self._effective_lease(work_id)
        expected = max(
            int((lease or {}).get("final_event_count") or 0),
            int((lease or {}).get("takeover_expected_event_count") or 0),
            int((lease or {}).get("takeover_after_seq") or 0),
        )
        seen = {int(event["seq"]) for event in events}
        present = sum(1 for seq in range(1, expected + 1) if seq in seen)
        gap = expected - present
        return {
            "work_id": str(work_id), "kind": str(manifest.get("kind") or ""),
            "course_id": str(manifest.get("course_id") or ""),
            "assignment_id": str(manifest.get("assignment_id") or ""),
            "created_at": str(manifest.get("created_at") or ""),
            "holder": str((lease or {}).get("machine") or ""),
            "heartbeat_at": str((lease or {}).get("heartbeat_at") or ""),
            "lease_state": str((lease or {}).get("state") or "released"),
            "event_count": len(events), "final_event_count": expected,
            "sync_progress": {"present": present, "expected": expected,
                              "complete": gap == 0},
            "takeover_warning": bool((lease or {}).get("takeover_expected_event_count")),
            "takeover_from": str((lease or {}).get("takeover_from_machine") or ""),
            "orphan_event_count": self._orphan_count(work_id),
        }

    def list_items(self, *, kind: str | None = None) -> list[dict]:
        scan_conflicts(self.workspace_root)
        if not self.root.is_dir():
            return []
        summaries = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not _WORK_ID_RE.fullmatch(directory.name):
                continue
            try:
                summary = self.summary(directory.name)
            except WorkItemError:
                continue
            if kind and summary["kind"] != kind:
                continue
            summaries.append(summary)
        summaries.sort(key=lambda item: (item["created_at"], item["work_id"]), reverse=True)
        return summaries


class _HeartbeatService:
    def __init__(self):
        self._guard = threading.RLock()
        self._items: dict[tuple[int, str], tuple[SharedWorkStore, str]] = {}
        self._thread = None

    def register(self, store: SharedWorkStore, work_id: str) -> None:
        key = (id(store), work_id)
        with self._guard:
            self._items[key] = (store, work_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="ce-work-heartbeat", daemon=True)
                self._thread.start()

    def unregister(self, store: SharedWorkStore, work_id: str) -> None:
        with self._guard:
            self._items.pop((id(store), work_id), None)

    def release_all(self) -> None:
        with self._guard:
            items = list(self._items.values())
        for store, work_id in items:
            try:
                store.release(work_id)
            except Exception:
                pass

    def _run(self) -> None:
        while True:
            time.sleep(HEARTBEAT_INTERVAL_SECONDS)
            with self._guard:
                items = list(self._items.values())
            if not items:
                return
            for store, work_id in items:
                try:
                    with store._lock(work_id):
                        lease = store._effective_lease(work_id)
                        if lease and lease.get("machine") == local_runtime.machine_id() and lease.get("state") == "held":
                            store._heartbeat_unlocked(work_id, lease)
                        else:
                            self.unregister(store, work_id)
                except Exception:
                    # A shared-store conflict pauses heartbeat writes. Keep the
                    # registration so the owner can resume after review.
                    continue


_heartbeat_service = _HeartbeatService()


def heartbeat_service() -> _HeartbeatService:
    return _heartbeat_service
