"""Append-only, per-machine journals for M365-synced key-value state."""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from api import local_runtime
from api.platform_services import workspace
from api.shared_storage import (
    append_jsonl_batch, create_json_exclusive, legacy_storage_reappeared,
    scan_conflicts,
)


class SharedKVError(RuntimeError):
    """A shared key-value store could not be read or safely initialized."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _escape(segment: str) -> str:
    return str(segment).replace("\\", "\\\\").replace(".", "\\.")


def _split_key(value: str) -> list[str]:
    parts = []
    current = []
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    parts.append("".join(current))
    return parts


def flatten(document: dict) -> dict[str, object]:
    """Flatten nested mappings to dotted keys; arrays remain atomic values."""
    result: dict[str, object] = {}

    def visit(value, prefix):
        if isinstance(value, dict) and value:
            for key in sorted(value, key=lambda item: str(item)):
                part = _escape(str(key))
                visit(value[key], f"{prefix}.{part}" if prefix else part)
        else:
            result[prefix] = copy.deepcopy(value)

    for key in sorted(document, key=lambda item: str(item)):
        visit(document[key], _escape(str(key)))
    return result


def unflatten(values: dict[str, object]) -> dict:
    result: dict = {}
    for key, value in values.items():
        parts = _split_key(key)
        current = result
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[parts[-1]] = copy.deepcopy(value)
    return result


def _contains_fixture(value) -> bool:
    if isinstance(value, str):
        return value == "Practice - Red/Gold"
    if isinstance(value, dict):
        return any(_contains_fixture(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_fixture(item) for item in value)
    return False


def drop_test_course(document: dict) -> dict:
    """Remove the one documented pre-launch key-42 practice fixture."""
    result = copy.deepcopy(document)
    if _contains_fixture(result.get("42")):
        result.pop("42", None)
    for key in ("sis_grade_bridges", "extra_time", "roster_student_settings",
                "roster_tier_schemes", "roster_group_schemes",
                "roster_score_matrices", "roster_relationships", "roster_baselines"):
        mapping = result.get(key)
        if isinstance(mapping, dict) and _contains_fixture(mapping.get("42")):
            mapping.pop("42", None)
    if isinstance(result.get("saved_courses"), list):
        result["saved_courses"] = [course for course in result["saved_courses"]
                                   if not (isinstance(course, dict)
                                           and str(course.get("id")) == "42"
                                           and _contains_fixture(course))]
    return result


class SharedKVStore:
    """One shared snapshot plus append-only journals, merged by last event."""

    def __init__(self, name: str, *, root=None, legacy_path=None):
        self.name = str(name)
        self.workspace_root = root
        shared = workspace.shared_root(root)
        if not shared:
            raise SharedKVError("workspace_not_configured")
        self.root = Path(shared) / "kv" / self.name
        self.legacy_path = Path(legacy_path) if legacy_path else None

    def _snapshots(self) -> list[tuple[Path, dict]]:
        rows = []
        if not self.root.is_dir():
            return rows
        for path in self.root.glob("snapshot.*.json"):
            try:
                with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                    document = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise SharedKVError("shared_kv_snapshot_unreadable") from exc
            if (not isinstance(document, dict) or document.get("version") != 1
                    or not isinstance(document.get("values"), dict)):
                raise SharedKVError("shared_kv_snapshot_invalid")
            rows.append((path, document))
        return sorted(rows, key=lambda row: (str(row[1].get("snapshot_at") or ""), row[0].name))

    def _legacy_values(self, fallback: dict | None = None) -> dict:
        legacy_storage_reappeared(self.legacy_path)
        if self.legacy_path and self.legacy_path.is_file():
            try:
                with open(workspace.extended_path(str(self.legacy_path)), encoding="utf-8") as handle:
                    document = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise SharedKVError("legacy_settings_unreadable") from exc
            if not isinstance(document, dict):
                raise SharedKVError("legacy_settings_invalid")
            return drop_test_course(document)
        return copy.deepcopy(fallback or {})

    def ensure_initial_snapshot(self, fallback: dict | None = None) -> None:
        """Import legacy settings once using an immutable, no-replace publish."""
        legacy_storage_reappeared(self.legacy_path)
        snapshots = self._snapshots()
        if snapshots:
            if self.legacy_path and self.legacy_path.is_file():
                legacy_values = flatten(self._legacy_values())
                initial_values = snapshots[0][1]["values"]
                if any(initial_values.get(key) != value for key, value in legacy_values.items()):
                    raise SharedKVError("legacy_settings_mismatch")
                self._retire_legacy()
            return
        document = self._legacy_values(fallback)
        snapshots = self._snapshots()
        if snapshots:
            self._retire_legacy()
            return
        snapshot_at = _now()
        snapshot = {
            "version": 1,
            "created_at": snapshot_at,
            "snapshot_at": snapshot_at,
            "created_by": local_runtime.machine_id(),
            "values": flatten(document),
        }
        initial = self.root / "snapshot.initial.json"
        created = create_json_exclusive(initial, snapshot, root=self.workspace_root)
        if not created:
            # A concurrent first run owns the immutable initial snapshot.
            self._snapshots()
        else:
            self._retire_legacy()

    def _retire_legacy(self) -> None:
        legacy_storage_reappeared(self.legacy_path)
        if not self.legacy_path or not self.legacy_path.is_file():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        migrated = self.legacy_path.with_name(f"{self.legacy_path.name}.migrated-{stamp}")
        if not migrated.exists():
            os.replace(workspace.extended_path(str(self.legacy_path)),
                       workspace.extended_path(str(migrated)))

    def read_values(self, fallback: dict | None = None) -> dict[str, object]:
        # Conflict discovery is whole-tree, even though only this store's
        # writes are blocked by a sibling copy.
        legacy_storage_reappeared(self.legacy_path)
        scan_conflicts(self.workspace_root)
        self.ensure_initial_snapshot(fallback)
        snapshots = self._snapshots()
        if not snapshots:
            raise SharedKVError("shared_kv_snapshot_missing")
        _path, snapshot = snapshots[-1]
        values = copy.deepcopy(snapshot["values"])
        watermark = str(snapshot.get("snapshot_at") or "")
        events = []
        for path in self.root.glob("journal.*.jsonl"):
            try:
                with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if (not isinstance(event, dict) or event.get("v") != 1
                                or not isinstance(event.get("key"), str)
                                or event.get("op") not in {"set", "del"}):
                            raise SharedKVError("shared_kv_event_invalid")
                        if str(event.get("ts") or "") > watermark:
                            events.append((event, line_number))
            except (OSError, json.JSONDecodeError) as exc:
                raise SharedKVError("shared_kv_journal_unreadable") from exc
        events.sort(key=lambda item: (str(item[0].get("ts") or ""),
                                     str(item[0].get("machine") or ""),
                                     int(item[0].get("seq") or item[1])))
        for event, _line_number in events:
            if event["op"] == "set":
                values[event["key"]] = copy.deepcopy(event.get("value"))
            else:
                values.pop(event["key"], None)
        return values

    def read(self, fallback: dict | None = None) -> dict:
        return unflatten(self.read_values(fallback))

    def append_changes(self, before: dict, after: dict) -> None:
        legacy_storage_reappeared(self.legacy_path)
        old_values = flatten(before)
        new_values = flatten(after)
        changed = []
        timestamp = _now()
        machine = local_runtime.machine_id()
        for key in sorted(set(old_values) | set(new_values)):
            old_present = key in old_values
            new_present = key in new_values
            if old_present == new_present and old_values.get(key) == new_values.get(key):
                continue
            if new_present:
                changed.append({"v": 1, "ts": timestamp, "machine": machine,
                                "key": key, "op": "set", "value": new_values[key]})
            else:
                changed.append({"v": 1, "ts": timestamp, "machine": machine,
                                "key": key, "op": "del"})
        if changed:
            append_jsonl_batch(self.root / f"journal.{machine}.jsonl", changed,
                               root=self.workspace_root)
