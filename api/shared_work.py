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
from collections import OrderedDict
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


# --- Exact read memo for the advisory summary path ---------------------------
# Under CPU load every file open costs tens of milliseconds, and discovery's
# resume read repeats byte-identical reads on every call. The memo reuses the
# *validated result* of a read for a file whose ``(st_size, st_mtime_ns)`` is
# unchanged, so every check the read helpers perform still ran once on those
# exact bytes. Only the summary read uses it; mutating owners always read the
# files themselves.

# A file modified this close to the read's start can still be rewritten without
# a visible change (git's "racy clean"), so its result is never reused later.
_MEMO_RACY_NS = 2_000_000_000
_MEMO_MAX_ENTRIES = 8192
_MEMO_MAX_BYTES = 64 * 1024 * 1024


class _SummaryReadMemo:
    """Thread-safe LRU of validated per-file results, one entry per file path.

    An entry holds the file signature and the result serialized as JSON text,
    so a hit returns a fresh object graph that no caller can use to corrupt
    the memo. A changed signature simply misses; ``remember`` then replaces it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[tuple[int, int], str]] = OrderedDict()
        self._bytes = 0

    def get(self, key: str, signature: tuple[int, int]) -> str | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] != signature:
                return None
            self._entries.move_to_end(key)
            return entry[1]

    def remember(self, key: str, signature: tuple[int, int], text: str) -> None:
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous[1])
            if len(text) > _MEMO_MAX_BYTES // 4:
                return
            self._entries[key] = (signature, text)
            self._bytes += len(text)
            while len(self._entries) > _MEMO_MAX_ENTRIES or self._bytes > _MEMO_MAX_BYTES:
                _, (_, dropped) = self._entries.popitem(last=False)
                self._bytes -= len(dropped)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0


_SUMMARY_MEMO = _SummaryReadMemo()


def _entry_signature(entry) -> tuple[int, int] | None:
    try:
        stat = entry.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def clear_summary_read_memo() -> None:
    """Forget every memoized summary read (tests; a new process starts empty)."""
    _SUMMARY_MEMO.clear()


def _memoized_read(path, signature, started_ns: int, read):
    """Return ``read()``, reusing its result for an unchanged, settled file.

    ``signature`` is the file's ``(st_size, st_mtime_ns)`` taken before the
    read (``None`` when unknown: always read). ``read`` returns the validated
    result, or ``None`` for an outcome that must not be remembered; an
    exception (unreadable, invalid, refused) propagates and is never stored.
    A file whose mtime is not at least ``_MEMO_RACY_NS`` older than
    ``started_ns`` is read again next time.
    """
    if signature is None:
        return read()
    key = str(path)
    cached = _SUMMARY_MEMO.get(key, signature)
    if cached is not None:
        return json.loads(cached)
    value = read()
    if value is not None and signature[1] <= started_ns - _MEMO_RACY_NS:
        _SUMMARY_MEMO.remember(key, signature, json.dumps(value))
    return value


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

    @staticmethod
    def _read_lease_files(paths) -> list[dict]:
        values = []
        for path in paths:
            try:
                with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                    value = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("epoch"), int):
                values.append(value)
        return values

    def _leases(self, work_id: str) -> list[dict]:
        return self._read_lease_files(self._lease_paths(work_id))

    @staticmethod
    def _select_effective_lease(leases: list[dict]) -> dict | None:
        """Pure effective-lease rule shared by mutating owners and summary reads."""
        leases = list(leases)
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

    def _effective_lease(self, work_id: str) -> dict | None:
        return self._select_effective_lease(self._leases(work_id))

    def _write_lease(self, work_id: str, lease: dict) -> None:
        directory = self._item_dir(work_id)
        assert_store_writable(directory, root=self.workspace_root)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"lease.{lease['machine']}.json"
        atomic_write_json(path, lease)

    @staticmethod
    def _read_event_files(paths) -> list[dict]:
        events = []
        for path in paths:
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

    def _raw_events(self, work_id: str) -> list[dict]:
        directory = self._item_dir(work_id)
        if not directory.is_dir():
            raise WorkItemNotFound("work_item_not_found")
        paths = [path for path in sorted(directory.glob("events.*.jsonl"),
                                         key=lambda item: item.name)
                 if not path.name.endswith(".orphan.jsonl")]
        return self._read_event_files(paths)

    @staticmethod
    def _classify_late_events(events: list[dict], leases: list[dict]):
        """Pure fencing rule: return ``(kept, late_by_machine)``.

        A late event was written by a taken-over machine, under an older epoch,
        beyond the takeover cutoff. Persisting late events is the caller's
        decision; only mutating owners quarantine them.
        """
        leases = sorted(leases, key=lambda item: int(item.get("epoch") or 0))
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
        if not late:
            return events, late
        late_fingerprints = {
            hashlib.sha256(_canonical_json(event)).hexdigest()
            for machine_events in late.values() for event in machine_events
        }
        kept = [event for event in events
                if hashlib.sha256(_canonical_json(event)).hexdigest() not in late_fingerprints]
        return kept, late

    @staticmethod
    def _select_events(events: list[dict]) -> list[dict]:
        """Pure sequence rule: a higher fencing epoch wins a duplicate sequence."""
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

    def _quarantine_late_events(self, work_id: str, events: list[dict]) -> list[dict]:
        events, late = self._classify_late_events(events, self._leases(work_id))
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
        return events

    def _events(self, work_id: str) -> list[dict]:
        events = self._quarantine_late_events(work_id, self._raw_events(work_id))
        # The lower-epoch copy of a duplicate sequence was classified late above.
        return self._select_events(events)

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

    @staticmethod
    def _sync_counts(events: list[dict], lease: dict | None) -> tuple[int, int]:
        """Pure ``(expected, present)`` event counts for an effective lease."""
        expected = max(
            int((lease or {}).get("final_event_count") or 0),
            int((lease or {}).get("takeover_expected_event_count") or 0),
            int((lease or {}).get("takeover_after_seq") or 0),
        )
        seen = {int(event["seq"]) for event in events}
        present = sum(1 for seq in range(1, expected + 1) if seq in seen)
        return expected, present

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
        expected, present = self._sync_counts(events, lease)
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


    # ------------------------------------------------------------------
    # Advisory summary read (discovery). Side-effect free: it never takes a
    # lock, heartbeats, acquires/releases/renews a lease, quarantines events,
    # materializes a bundle, or writes any file. It reuses the pure
    # event/lease selection rules above, so ownership and fencing semantics
    # have one implementation.
    # ------------------------------------------------------------------

    _SUMMARY_ERROR_CODES = {
        "work_item_event_invalid": "events_invalid",
        "work_item_events_unreadable": "events_unreadable",
        "work_item_sequence_conflict": "sequence_conflict",
        "work_item_lease_conflict": "lease_conflict",
        "work_item_blob_missing": "snapshot_unavailable",
        "work_item_blob_digest_mismatch": "snapshot_unavailable",
        "work_item_blob_invalid": "snapshot_unavailable",
    }

    def _conflicts_by_item(self, conflicts: list[dict]) -> dict[str, list[dict]]:
        """Group one whole-tree conflict inventory by item directory name."""
        if not conflicts:
            return {}
        try:
            root = self.root.resolve()
        except OSError:
            return {}
        grouped: dict[str, list[dict]] = {}
        for conflict in conflicts:
            try:
                relative = Path(conflict["path"]).resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            if len(relative.parts) >= 2:
                grouped.setdefault(relative.parts[0].casefold(), []).append(conflict)
        return grouped

    @staticmethod
    def _list_item_dir(directory: Path) -> dict | None:
        """One directory listing: manifest presence, journals, leases, signatures.

        ``stats`` maps each manifest/journal/lease file name to its
        ``(st_size, st_mtime_ns)`` from the listing's own ``DirEntry`` (free on
        Windows), or ``None`` when it could not be read. They key the read memo.
        """
        try:
            with os.scandir(directory) as scanner:
                entries = list(scanner)
        except OSError:
            return None
        has_manifest = False
        events, leases, signature, stats = [], [], [], {}
        for entry in entries:
            name = entry.name
            if name == "manifest.json":
                has_manifest = True
                stats[name] = _entry_signature(entry)
            elif (name.startswith("events.") and name.endswith(".jsonl")
                  and len(name) >= 13 and not name.endswith(".orphan.jsonl")):
                events.append(directory / name)
                stats[name] = _entry_signature(entry)
                size, mtime_ns = stats[name] or (-1, -1)
                signature.append((name, size, mtime_ns))
            elif name.startswith("lease.") and name.endswith(".json") and len(name) >= 10:
                leases.append(directory / name)
                stats[name] = _entry_signature(entry)
        return {"total": len(entries), "has_manifest": has_manifest,
                "events": sorted(events, key=lambda path: path.name),
                "leases": sorted(leases, key=lambda path: path.name),
                "signature": tuple(sorted(signature)), "stats": stats}

    @staticmethod
    def _read_manifest_file(path: Path, work_id: str) -> dict | None:
        try:
            with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if (not isinstance(value, dict) or value.get("version") != 1
                or str(value.get("work_id") or "") != str(work_id)):
            return None
        return value

    def _read_manifest_memoized(self, directory: Path, work_id: str, listing: dict,
                                started_ns: int) -> dict | None:
        path = directory / "manifest.json"
        return _memoized_read(path, listing["stats"].get("manifest.json"), started_ns,
                              lambda: self._read_manifest_file(path, work_id))

    def _read_blob_memoized(self, work_id: str, digest: str, started_ns: int) -> dict:
        """``_read_blob`` reusing its verified result while the blob file is unchanged.

        The content-addressed name is not trusted on its own: the read verifies
        integrity, so the memo is keyed on one ``os.stat`` of the blob instead.
        """
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return self._read_blob(work_id, digest)
        path = self._item_dir(work_id) / "blobs" / digest
        try:
            stat = os.stat(workspace.extended_path(str(path)))
        except OSError:
            return self._read_blob(work_id, digest)
        return _memoized_read(path, (stat.st_size, stat.st_mtime_ns), started_ns,
                              lambda: self._read_blob(work_id, digest))

    def _read_item_state(self, work_id: str, listing: dict, project,
                         started_ns: int | None = None) -> dict:
        """Read one item's leases, events and latest snapshot blob exactly once.

        Unchanged, settled files are served from the read memo instead of being
        opened (see ``_memoized_read``); results are identical either way.
        """
        if started_ns is None:
            started_ns = time.time_ns()
        stats = listing.get("stats") or {}
        try:
            leases = []
            for path in listing["leases"]:
                leases.extend(_memoized_read(
                    path, stats.get(path.name), started_ns,
                    lambda path=path: self._read_lease_files([path]) or None) or [])
            lease = self._select_effective_lease(leases)
            events = []
            for path in listing["events"]:
                events.extend(_memoized_read(
                    path, stats.get(path.name), started_ns,
                    lambda path=path: self._read_event_files([path])))
            events, late = self._classify_late_events(events, leases)
            selected = self._select_events(events)
        except WorkItemError as exc:
            return {"error": self._SUMMARY_ERROR_CODES.get(str(exc), "item_unreadable")}
        except OSError:
            return {"error": "item_unreadable"}
        expected, present = self._sync_counts(selected, lease)
        late_count = sum(len(rows) for rows in late.values())
        outcome = {
            "state": None,
            "work_item": {
                "holder": str((lease or {}).get("machine") or ""),
                "heartbeat_at": str((lease or {}).get("heartbeat_at") or ""),
                "lease_state": str((lease or {}).get("state") or "released"),
                "event_count": len(selected),
                "sync_progress": {"present": present, "expected": expected,
                                  "complete": present == expected},
                "orphan_event_count": late_count,
            },
            "notices": ["late_events_ignored"] if late_count else [],
        }
        if present < expected:
            outcome["error"] = "sync_incomplete"
            return outcome
        if not selected:
            return outcome
        try:
            blob = self._read_blob_memoized(work_id, str(selected[-1]["blob_sha256"]),
                                            started_ns)
        except WorkItemError as exc:
            outcome["error"] = self._SUMMARY_ERROR_CODES.get(str(exc), "snapshot_unavailable")
            return outcome
        if blob.get("storage_model") == "shared_work.v1":
            outcome["state"] = project(blob)
        return outcome

    def read_item_summaries(self, *, kind: str, course_ids=None, project) -> dict:
        """Summarize work items of one kind/course set without side effects.

        ``project`` maps the latest validated state dict to the allowlisted
        summary fields; the full state never leaves this call. Returns::

            {"items": [{"work_id", "course_id", "assignment_id", "created_at",
                         "state", "work_item", "attention", "notices"}],
             "unclassified": [code, ...]}

        ``state`` is ``None`` for an item with no usable latest snapshot.
        ``attention`` holds blocking per-item codes (the item's latest state is
        unknown: ``sync_incomplete``, ``events_unreadable``, ``events_invalid``,
        ``sequence_conflict``, ``lease_conflict``, ``snapshot_unavailable``,
        ``item_unreadable``); ``unclassified`` lists directories whose manifest
        could not prove them out of scope (``manifest_unreadable``,
        ``manifest_missing``, ``item_unreadable``, ``work_root_unreadable``).
        A conflict copy inside a relevant item directory raises
        ``SharedStoreConflictError``. ``course_ids=None`` means every course;
        an empty collection means none.

        Unchanged, settled files (same size and mtime, at least 2 s old) are
        served from a process-lifetime read memo instead of being reopened; the
        result is identical to an unmemoized read, and failures are never kept.
        """
        allowed = None if course_ids is None else {str(c or "") for c in course_ids}
        result = {"items": [], "unclassified": []}
        # Files settled for 2 s before this instant may be served from the read memo.
        started_ns = time.time_ns()
        # One whole-tree inventory per pass; refusals are scoped to items.
        conflicts = scan_conflicts(self.workspace_root)
        if not self.root.is_dir():
            return result
        try:
            with os.scandir(self.root) as scanner:
                names = sorted(entry.name for entry in scanner
                               if entry.is_dir() and _WORK_ID_RE.fullmatch(entry.name))
        except OSError:
            result["unclassified"].append("work_root_unreadable")
            return result
        by_item = self._conflicts_by_item(conflicts)
        for name in names:
            directory = self.root / name
            listing = self._list_item_dir(directory)
            if listing is None:
                result["unclassified"].append("item_unreadable")
                continue
            if not listing["has_manifest"]:
                if listing["total"]:
                    result["unclassified"].append("manifest_missing")
                continue
            manifest = self._read_manifest_memoized(directory, name, listing, started_ns)
            if manifest is None:
                result["unclassified"].append("manifest_unreadable")
                continue
            if str(manifest.get("kind") or "") != kind:
                continue
            course_id = str(manifest.get("course_id") or "")
            # An empty manifest course cannot prove the item is out of scope.
            if allowed is not None and course_id and course_id not in allowed:
                continue
            if by_item.get(name.casefold()):
                raise SharedStoreConflictError(by_item[name.casefold()])
            outcome = self._read_item_state(name, listing, project, started_ns)
            if outcome.get("error"):
                # At most one bounded reread, only when the journals visibly
                # changed (sync in progress) since the first listing.
                relisted = self._list_item_dir(directory)
                if relisted is not None and relisted["signature"] != listing["signature"]:
                    outcome = self._read_item_state(name, relisted, project, started_ns)
            error = outcome.get("error")
            result["items"].append({
                "work_id": name, "course_id": course_id,
                "assignment_id": str(manifest.get("assignment_id") or ""),
                "created_at": str(manifest.get("created_at") or ""),
                "state": None if error else outcome.get("state"),
                "work_item": outcome.get("work_item"),
                "attention": [error] if error else [],
                "notices": list(outcome.get("notices") or []),
            })
        return result


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
