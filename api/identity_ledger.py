"""Creation and loading of immutable registry files for the shared Identity Vault."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from api import feedback_vault, local_runtime, pseudonym_secret
from api.platform_services import workspace
from api.shared_storage import (
    assert_store_writable, shared_root,
)


LEDGER_VERSION = 1


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


def ensure_seed(vault_dir=None, *, root=None) -> dict:
    """Create the shared seed once, or load the existing immutable seed."""
    if vault_dir is None:
        vault_dir = workspace.identity_vault_dir(root)
    if not vault_dir:
        raise RuntimeError("workspace_not_configured")
    directory = Path(vault_dir)
    shared = shared_root(root)
    if shared is None:
        raise RuntimeError("workspace_not_configured")
    path = seed_path(directory)
    if path.exists():
        seed = json.loads(path.read_text(encoding="utf-8"))
        if seed.get("version") != LEDGER_VERSION or not isinstance(seed.get("entries"), dict):
            raise RuntimeError("identity_seed_invalid")
        _ensure_pokemon_file(directory, root=root)
        _ensure_machine_marker(shared, root=root)
        if seed.get("created_by") == local_runtime.machine_id():
            pseudonym_secret.ensure_primary_secret()
        return seed

    assert_store_writable(directory, root=root)
    directory.mkdir(parents=True, exist_ok=True)
    _ensure_pokemon_file(directory, root=root)
    seed = {
        "version": LEDGER_VERSION,
        "created_at": _now(),
        "created_by": local_runtime.machine_id(),
        "entries": {},
    }
    if not _exclusive_json(path, seed):
        seed = json.loads(path.read_text(encoding="utf-8"))

    if seed.get("created_by") == local_runtime.machine_id():
        pseudonym_secret.ensure_primary_secret()
    _ensure_machine_marker(shared, root=root)
    return seed


def _ensure_machine_marker(shared: Path, *, root=None) -> None:
    machine = local_runtime.machine_id()
    marker = shared / f"migration.{machine}.json"
    if not marker.exists():
        assert_store_writable(shared, root=root)
        _exclusive_json(marker, {"version": LEDGER_VERSION, "started_at": _now(),
                                 "vault_seeded_at": _now()})
