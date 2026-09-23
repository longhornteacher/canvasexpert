"""Seed import and immutable registry files for the shared Identity Vault."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from api import feedback_vault, local_runtime, pseudonym_secret
from api.platform_services import workspace
from api.shared_storage import (
    LegacyStorageReappearedError, assert_store_writable,
    legacy_storage_reappeared, shared_root,
)


LEDGER_VERSION = 1


class SeedMismatchError(RuntimeError):
    def __init__(self, local_fingerprint: str, shared_fingerprint: str):
        self.local_fingerprint = local_fingerprint
        self.shared_fingerprint = shared_fingerprint
        super().__init__("identity_seed_mismatch")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_path(vault_dir) -> Path:
    return Path(vault_dir) / "seed.v1.json"


def pokemon_path(vault_dir) -> Path:
    return Path(vault_dir) / "pokemon.v1.json"


def _exclusive_json(path: Path, document: dict) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.tmp.{os.getpid()}.",
        dir=workspace.extended_path(str(path.parent)),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Publish without replacement so competing initializers cannot
            # overwrite the immutable seed or registry.
            os.link(workspace.extended_path(str(temporary)), workspace.extended_path(str(path)))
            return True
        except FileExistsError:
            return False
    finally:
        try:
            os.remove(workspace.extended_path(str(temporary)))
        except FileNotFoundError:
            pass


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(workspace.extended_path(str(path)), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_document(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("legacy_vault_unreadable") from exc
    feedback_vault.Vault._validate_document_shape(document)
    return document


def _local_entries(path: Path) -> dict:
    return _local_document(path).get("by_canvas_id", {})


def _subset_matches(local_entries: dict, shared_entries: dict) -> bool:
    for canvas_id, local_entry in local_entries.items():
        shared_entry = shared_entries.get(canvas_id)
        if not isinstance(local_entry, dict) or not isinstance(shared_entry, dict):
            return False
        for key, value in local_entry.items():
            if shared_entry.get(key) != value:
                return False
    return True


def _verify_existing_seed(legacy_path: Path, seed: dict) -> None:
    if not legacy_path.is_file():
        return
    actual = _file_sha256(legacy_path)
    expected = str(seed.get("source_sha256") or "")
    if actual == expected:
        return
    local_entries = _local_entries(legacy_path)
    shared_entries = seed.get("entries")
    if not isinstance(shared_entries, dict) or not _subset_matches(local_entries, shared_entries):
        raise SeedMismatchError(actual[:12], expected[:12])


def _ensure_pokemon_file(vault_dir: Path, *, root=None) -> None:
    path = pokemon_path(vault_dir)
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("pokemon_registry_unreadable") from exc
        if document != {"version": 1, "pokemon": feedback_vault._REGISTRY_WORDS}:
            raise RuntimeError("pokemon_registry_mismatch")
        return
    assert_store_writable(vault_dir, root=root)
    _exclusive_json(path, {"version": 1, "pokemon": feedback_vault._REGISTRY_WORDS})


def ensure_seed(vault_dir=None, legacy_vault_path=None, *, root=None) -> dict:
    """Create the shared seed once, or verify a machine's local source against it."""
    if vault_dir is None:
        vault_dir = workspace.identity_vault_dir(root)
    if legacy_vault_path is None:
        legacy_dir = workspace.legacy_identity_vault_dir(root)
        legacy_vault_path = os.path.join(legacy_dir, "vault.json") if legacy_dir else None
    if not vault_dir:
        raise RuntimeError("workspace_not_configured")
    directory = Path(vault_dir)
    legacy_path = Path(legacy_vault_path) if legacy_vault_path else None
    shared = shared_root(root)
    if shared is None:
        raise RuntimeError("workspace_not_configured")
    # A previous app version may recreate the retired file after migration.
    # Never inspect or retire that copy: the old Vault loader can have already
    # assigned replacement pseudonyms and OneDrive may be syncing the fork.
    legacy_storage_reappeared(legacy_path)
    path = seed_path(directory)
    if path.exists():
        seed = json.loads(path.read_text(encoding="utf-8"))
        if seed.get("version") != LEDGER_VERSION or not isinstance(seed.get("entries"), dict):
            raise RuntimeError("identity_seed_invalid")
        if legacy_path and legacy_path.is_file():
            _verify_existing_seed(legacy_path, seed)
            assert_store_writable(directory, root=root)
            _retire_legacy_vault(legacy_path)
        _ensure_pokemon_file(directory, root=root)
        _ensure_machine_marker(shared, root=root)
        if seed.get("created_by") == local_runtime.machine_id():
            pseudonym_secret.ensure_primary_secret()
        return seed

    assert_store_writable(directory, root=root)
    directory.mkdir(parents=True, exist_ok=True)
    if legacy_path and legacy_path.is_file():
        source_hash = _file_sha256(legacy_path)
        source_document = _local_document(legacy_path)
        entries = source_document.get("by_canvas_id", {})
        source_metadata = {key: value for key, value in source_document.items()
                           if key != "by_canvas_id"}
    else:
        source_hash = hashlib.sha256(b"").hexdigest()
        entries = {}
        source_metadata = {}
    _ensure_pokemon_file(directory, root=root)
    seed = {
        "version": LEDGER_VERSION,
        "source_sha256": source_hash,
        "created_at": _now(),
        "created_by": local_runtime.machine_id(),
        "source_metadata": source_metadata,
        "entries": entries,
    }
    if not _exclusive_json(path, seed):
        seed = json.loads(path.read_text(encoding="utf-8"))
        if legacy_path and legacy_path.is_file():
            _verify_existing_seed(legacy_path, seed)

    if seed.get("created_by") == local_runtime.machine_id():
        pseudonym_secret.ensure_primary_secret()
    if legacy_path and legacy_path.is_file():
        _retire_legacy_vault(legacy_path)

    _ensure_machine_marker(shared, root=root)
    return seed


def _retire_legacy_vault(legacy_path: Path) -> None:
    """Rename a verified legacy file once, without overwriting an older copy."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    migrated = legacy_path.with_name(f"vault.json.migrated-{stamp}")
    if migrated.exists():
        machine = local_runtime.machine_id()
        migrated = legacy_path.with_name(f"vault.json.migrated-{stamp}-{machine}")
    if migrated.exists():
        if _file_sha256(migrated) == _file_sha256(legacy_path):
            return
        raise RuntimeError("legacy_vault_migration_target_conflict")
    os.replace(workspace.extended_path(str(legacy_path)),
               workspace.extended_path(str(migrated)))


def _ensure_machine_marker(shared: Path, *, root=None) -> None:
    machine = local_runtime.machine_id()
    marker = shared / f"migration.{machine}.json"
    if not marker.exists():
        assert_store_writable(shared, root=root)
        _exclusive_json(marker, {"version": LEDGER_VERSION, "started_at": _now(),
                                 "vault_seeded_at": _now()})
