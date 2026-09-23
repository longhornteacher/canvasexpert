"""Shared-workspace safety helpers for append-only M365-synced stores."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from api.platform_services import workspace
from api.storage_support import atomic_write_json


class SharedStoreConflictError(RuntimeError):
    """A OneDrive conflict copy blocks writes to the affected shared store."""

    def __init__(self, files: list[dict]):
        self.files = files
        super().__init__("shared_store_conflict")


class LegacyStorageReappearedError(RuntimeError):
    """A retired legacy file returned after the shared migration completed."""

    def __init__(self):
        super().__init__("legacy_storage_reappeared")


def shared_root(root=None) -> Path | None:
    value = workspace.shared_root(root)
    return Path(value) if value else None


def _extended(path: Path) -> str:
    return workspace.extended_path(str(path))


def legacy_storage_reappeared(path) -> bool:
    """Detect a retired legacy file returning, without opening its contents."""
    if not path:
        return False
    legacy = Path(path)
    extended_legacy = _extended(legacy)
    if not os.path.lexists(extended_legacy):
        return False
    try:
        names = os.listdir(_extended(legacy.parent))
    except OSError as exc:
        # Once the old file exists, inability to inspect migration markers
        # cannot be treated as permission to consume or overwrite it.
        raise LegacyStorageReappearedError() from exc
    marker_prefix = legacy.name + ".migrated-"
    if any(name.startswith(marker_prefix) for name in names):
        raise LegacyStorageReappearedError()
    return False


def reappeared_legacy_storage(root=None) -> list[str]:
    """Return safe labels for retired files that have reappeared."""
    result = []
    workspace_root = root if root is not None else workspace.workspace_root()
    if not workspace_root:
        return result
    settings_path = Path(workspace_root) / "settings.json"
    try:
        legacy_storage_reappeared(settings_path)
    except LegacyStorageReappearedError:
        result.append("settings")
    vault_dir = workspace.legacy_identity_vault_dir(root)
    vault_path = Path(vault_dir) / "vault.json" if vault_dir else None
    try:
        legacy_storage_reappeared(vault_path)
    except LegacyStorageReappearedError:
        result.append("vault")
    return result


def scan_conflicts(root=None) -> list[dict]:
    """Find OneDrive ``name-MACHINE.ext`` siblings throughout ``_Shared``.

    A file is considered a conflict copy when removing one or more suffix
    components after a dash yields an existing file with the same extension.
    The canonical path is retained so the teacher can compare or quarantine
    the copy without the runtime ever merging it.
    """
    base = shared_root(root)
    if base is None or not os.path.isdir(_extended(base)):
        return []
    found: list[dict] = []
    for directory, _, names in os.walk(_extended(base)):
        for name in names:
            candidate = Path(directory) / name
            stem, extension = os.path.splitext(name)
            if not extension or "-" not in stem:
                continue
            canonical = None
            split_at = stem.find("-")
            while split_at >= 0:
                possible = Path(directory) / (stem[:split_at] + extension)
                if possible.is_file():
                    canonical = possible
                    break
                split_at = stem.find("-", split_at + 1)
            if canonical is not None:
                found.append({"path": str(candidate), "canonical": str(canonical)})
    return sorted(found, key=lambda item: item["path"].casefold())


def store_conflicts(store_path, *, root=None) -> list[dict]:
    """Return conflicts contained in one store directory."""
    target = Path(store_path).resolve()
    result = []
    for item in scan_conflicts(root):
        conflict = Path(item["path"]).resolve()
        try:
            conflict.relative_to(target)
        except ValueError:
            continue
        result.append(item)
    return result


def assert_store_readable(store_path=None, *, root=None) -> list[dict]:
    """Scan the whole shared tree before a read and return all conflict rows."""
    conflicts = scan_conflicts(root)
    return conflicts


def assert_store_writable(store_path, *, root=None) -> None:
    conflicts = store_conflicts(store_path, root=root)
    if conflicts:
        raise SharedStoreConflictError(conflicts)


def append_jsonl(path: Path, record: dict, *, root=None) -> None:
    """Append and fsync one complete journal line written by this machine."""
    assert_store_readable(path.parent, root=root)
    assert_store_writable(path.parent, root=root)
    payload = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(_extended(path), "ab", buffering=0) as handle:
        handle.write(payload)
        os.fsync(handle.fileno())


def append_jsonl_batch(path: Path, records: list[dict], *, root=None) -> None:
    """Append and fsync a group of complete journal lines in one write."""
    if not records:
        return
    assert_store_readable(path.parent, root=root)
    assert_store_writable(path.parent, root=root)
    payload = b"".join(
        (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        for record in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(_extended(path), "ab", buffering=0) as handle:
        handle.write(payload)
        os.fsync(handle.fileno())


def create_json_exclusive(path: Path, document: dict, *, root=None) -> bool:
    """Atomically publish a new immutable JSON file without replacing it."""
    assert_store_writable(path.parent, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.tmp.{os.getpid()}.", dir=_extended(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(_extended(temporary), _extended(path))
            return True
        except FileExistsError:
            return False
    finally:
        try:
            os.remove(_extended(temporary))
        except FileNotFoundError:
            pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(_extended(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_and_remove(conflict_path: Path, *, root=None) -> dict:
    """Delete a conflict copy only when it is byte-identical to its canonical."""
    path = Path(conflict_path)
    rows = scan_conflicts(root)
    wanted = os.path.normcase(os.path.abspath(str(path)))
    match = next((item for item in rows
                  if os.path.normcase(os.path.abspath(item["path"])) == wanted), None)
    if match is None:
        return {"ok": False, "error": "conflict_not_found"}
    conflict = Path(match["path"])
    canonical = Path(match["canonical"])
    same = file_sha256(conflict) == file_sha256(canonical)
    if same:
        os.remove(_extended(conflict))
    return {"ok": True, "identical": same, "removed": same}


def quarantine_conflict(conflict_path: Path, *, root=None) -> dict:
    """Move one conflict copy to a dated teacher-visible quarantine folder."""
    path = Path(conflict_path)
    rows = scan_conflicts(root)
    wanted = os.path.normcase(os.path.abspath(str(path)))
    match = next((item for item in rows
                  if os.path.normcase(os.path.abspath(item["path"])) == wanted), None)
    if match is None:
        return {"ok": False, "error": "conflict_not_found"}
    base = shared_root(root)
    if base is None:
        return {"ok": False, "error": "workspace_not_configured"}
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    target_dir = base / "_conflicts" / day
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    suffix = 1
    while target.exists():
        target = target_dir / f"{path.stem}-{suffix}{path.suffix}"
        suffix += 1
    shutil.move(_extended(path), _extended(target))
    return {"ok": True, "quarantined": target.name}
