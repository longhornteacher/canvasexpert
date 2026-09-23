"""Shared append-only Identity Vault backed by an immutable seed and journals."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from api import feedback_vault, identity_ledger, local_runtime, pseudonym_secret
from api.platform_services import workspace
from api.shared_storage import append_jsonl, legacy_storage_reappeared, scan_conflicts
from api.storage_support import interprocess_lock


class PseudonymSecretRequired(RuntimeError):
    """This machine has not received the teacher's shared pseudonym secret."""


class PseudonymProvisionalError(RuntimeError):
    """A cross-machine collision must be resolved before scoring this student."""


class PermanentPseudonymError(ValueError):
    """An existing student-to-Pokémon assignment is immutable."""


class SharedVault(feedback_vault.Vault):
    """API-compatible vault facade whose shared writes are append-only."""

    def __init__(self, directory, *, legacy_vault_path=None, workspace_root=None,
                 secret_provider=None):
        self.directory = Path(directory)
        self.workspace_root = workspace_root
        self.legacy_vault_path = legacy_vault_path
        self.secret_provider = secret_provider or pseudonym_secret.get_secret
        self._provisional_ids: set[str] = set()
        self._assignment_events: list[dict] = []
        self._max_k: dict[str, int] = {}
        self._pending_k: dict[str, int] = {}
        self._events: list[dict] = []
        self._baseline_by_id: dict = {}
        super().__init__(str(self.directory / "vault.json"))

    def _ensure_seed(self) -> dict:
        if self.legacy_vault_path is None and self.workspace_root is None:
            production_dir = workspace.identity_vault_dir()
            if production_dir and Path(production_dir).resolve() == self.directory.resolve():
                legacy_dir = workspace.legacy_identity_vault_dir()
                self.legacy_vault_path = os.path.join(legacy_dir, "vault.json") if legacy_dir else None
        return identity_ledger.ensure_seed(
            self.directory,
            self.legacy_vault_path,
            root=self.workspace_root,
        )

    def _load(self):
        # Every shared-store read checks the whole tree so the console can
        # surface a conflict anywhere in _Shared, while writes remain blocked
        # only for the affected store.
        conflicts = scan_conflicts(self.workspace_root)
        self.conflict_files = [Path(item["path"]).name for item in conflicts]
        seed = self._ensure_seed()
        seed_entries = seed.get("entries") or {}
        if not isinstance(seed_entries, dict):
            raise feedback_vault.VaultSchemaError("Identity Vault seed entries are invalid.")

        metadata = copy.deepcopy(seed_entries)
        assignments = []
        for canvas_id, entry in seed_entries.items():
            if not isinstance(entry, dict):
                raise feedback_vault.VaultSchemaError("Identity Vault seed entry is invalid.")
            pokemon = str(entry.get("pseudonym") or "")
            if pokemon:
                canonical = feedback_vault._canonical_registry_word(pokemon)
                if canonical is None:
                    raise feedback_vault.VaultSchemaError("Identity Vault seed pseudonym is invalid.")
                assignments.append({
                    "canvas_user_id": str(canvas_id), "pokemon": canonical,
                    "k": 0, "ts": "0000-01-01T00:00:00Z", "machine": "SEED",
                })

        self._events = []
        self._max_k = {str(event["canvas_user_id"]): 0 for event in assignments}
        journal_paths = sorted(self.directory.glob("journal.*.jsonl"), key=lambda path: path.name)
        for path in journal_paths:
            try:
                with open(workspace.extended_path(str(path)), encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise feedback_vault.VaultSchemaError(
                                f"Identity Vault journal is invalid at line {line_number}."
                            ) from exc
                        self._events.append(event)
            except OSError as exc:
                raise feedback_vault.VaultSchemaError("Identity Vault journal could not be read.") from exc

        self._events.sort(key=self._event_order)
        self._assignment_events = list(assignments)
        for event in self._events:
            self._apply_event(event, metadata, assignments)
        assignments.sort(key=lambda event: (str(event.get("ts") or ""),
                                            str(event.get("machine") or ""),
                                            int(event.get("seq") or 0)))
        winners: dict[str, dict] = {}
        owners: dict[str, str] = {}
        provisional: dict[str, dict] = {}
        for event in assignments:
            canvas_id = str(event["canvas_user_id"])
            pokemon = str(event["pokemon"])
            folded = pokemon.casefold()
            if canvas_id in winners:
                continue
            if canvas_id in provisional:
                if folded not in owners:
                    winners[canvas_id] = event
                    owners[folded] = canvas_id
                    provisional.pop(canvas_id, None)
                continue
            owner = owners.get(folded)
            if owner is None:
                winners[canvas_id] = event
                owners[folded] = canvas_id
            elif owner != canvas_id:
                provisional[canvas_id] = event

        self._by_id = metadata
        self._by_pseudo = {}
        self._provisional_ids = set(provisional)
        for canvas_id, entry in self._by_id.items():
            stable = winners.get(str(canvas_id))
            pending = provisional.get(str(canvas_id))
            assignment = stable or pending
            if assignment:
                entry["pseudonym"] = assignment["pokemon"]
            if pending:
                entry["provisional"] = True
            else:
                entry.pop("provisional", None)
            if stable:
                self._by_pseudo[stable["pokemon"]] = str(canvas_id)
        self._baseline_by_id = copy.deepcopy(self._by_id)

    @staticmethod
    def _event_order(event):
        if not isinstance(event, dict) or event.get("v") != 1:
            raise feedback_vault.VaultSchemaError("Identity Vault journal event is invalid.")
        seq = event.get("seq", 0)
        if not isinstance(seq, int):
            raise feedback_vault.VaultSchemaError("Identity Vault journal sequence is invalid.")
        return (str(event.get("ts") or ""), str(event.get("machine") or ""), seq)

    def _apply_event(self, event, metadata, assignments):
        if not isinstance(event, dict) or event.get("v") != 1:
            raise feedback_vault.VaultSchemaError("Identity Vault journal event is invalid.")
        operation = event.get("op")
        canvas_id = str(event.get("canvas_user_id") or "")
        if not canvas_id:
            raise feedback_vault.VaultSchemaError("Identity Vault journal identity is invalid.")
        if operation == "assign":
            pokemon = feedback_vault._canonical_registry_word(event.get("pokemon"))
            if pokemon is None or not isinstance(event.get("k"), int) or event["k"] < 0:
                raise feedback_vault.VaultSchemaError("Identity Vault assignment is invalid.")
            assignments.append({
                "canvas_user_id": canvas_id, "pokemon": pokemon, "k": event["k"],
                "ts": str(event.get("ts") or ""), "machine": str(event.get("machine") or ""),
                "seq": int(event.get("seq") or 0),
            })
            cid_events = str(canvas_id)
            # Keep the next deterministic HMAC probe above every probe this
            # identity has already recorded, including collisions.
            self._max_k[cid_events] = max(self._max_k.get(cid_events, -1), event["k"])
            self._assignment_events.append(assignments[-1])
        elif operation == "identity":
            entry = event.get("entry")
            if not isinstance(entry, dict):
                raise feedback_vault.VaultSchemaError("Identity Vault metadata is invalid.")
            existing = metadata.setdefault(canvas_id, {})
            for key, value in entry.items():
                if key != "pseudonym":
                    existing[key] = copy.deepcopy(value)
        else:
            raise feedback_vault.VaultSchemaError("Identity Vault journal operation is invalid.")

    def _lock_path(self) -> Path:
        digest = hashlib.sha256(str(self.directory.resolve()).encode("utf-8")).hexdigest()[:16]
        return runtime_local_lock_root() / f"identity-{digest}.lock"

    @contextmanager
    def transaction(self):
        with interprocess_lock(self._lock_path()):
            self._load()
            try:
                yield self
            except Exception:
                raise
            else:
                self._save_changes()

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _journal_path(self) -> Path:
        return self.directory / f"journal.{local_runtime.machine_id()}.jsonl"

    def _append(self, event: dict) -> None:
        legacy_storage_reappeared(self.legacy_vault_path)
        event = {"v": 1, "ts": self._timestamp(), "machine": local_runtime.machine_id(), **event}
        append_jsonl(self._journal_path(), event, root=self.workspace_root)

    def _save_changes(self) -> None:
        legacy_storage_reappeared(self.legacy_vault_path)
        current_ids = set(self._by_id)
        all_ids = current_ids | set(self._baseline_by_id)
        for canvas_id in sorted(all_ids):
            current = self._by_id.get(canvas_id)
            previous = self._baseline_by_id.get(canvas_id)
            if not isinstance(current, dict):
                continue
            previous = previous if isinstance(previous, dict) else {}
            old_pokemon = str(previous.get("pseudonym") or "")
            new_pokemon = str(current.get("pseudonym") or "")
            was_provisional = bool(previous.get("provisional"))
            is_provisional = bool(current.get("provisional"))
            if new_pokemon and new_pokemon != old_pokemon:
                if old_pokemon and not was_provisional:
                    raise PermanentPseudonymError("An existing student pseudonym cannot be changed.")
                if is_provisional:
                    continue
                k = self._pending_k.get(str(canvas_id), self._max_k.get(str(canvas_id), -1) + 1)
                self._append({"op": "assign", "canvas_user_id": str(canvas_id),
                              "pokemon": new_pokemon, "k": k, "course_ids": []})
                self._max_k[str(canvas_id)] = k
            identity = {key: copy.deepcopy(value) for key, value in current.items()
                        if key not in {"pseudonym", "provisional"}}
            previous_identity = {key: value for key, value in previous.items()
                                 if key not in {"pseudonym", "provisional"}}
            if identity != previous_identity:
                self._append({"op": "identity", "canvas_user_id": str(canvas_id),
                              "entry": identity})
        self._pending_k.clear()
        self._baseline_by_id = copy.deepcopy(self._by_id)

    def save(self):
        with interprocess_lock(self._lock_path()):
            self._save_changes()

    def get_or_assign(self, canvas_id, real_name="", sis_id="", roster_names=None) -> str:
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            entry = {
                "pseudonym": "", "real_name": str(real_name or ""),
                "sis_id": str(sis_id or ""), "nicknames": [],
                "first_seen": self._timestamp(),
            }
            self._by_id[cid] = entry
        else:
            if real_name and not entry.get("real_name"):
                entry["real_name"] = str(real_name)
            if sis_id and not entry.get("sis_id"):
                entry["sis_id"] = str(sis_id)
        if not entry.get("pseudonym") or cid in self._provisional_ids:
            if cid in self._provisional_ids:
                return str(entry.get("pseudonym") or "")
            self._assign_hmac(cid, entry)
        return str(entry.get("pseudonym") or "")

    def _assign_hmac(self, canvas_id: str, entry: dict) -> None:
        secret = self.secret_provider()
        if not isinstance(secret, bytes) or len(secret) != 32:
            raise PseudonymSecretRequired("pseudonym_secret_not_configured")
        used = {str(event["pokemon"]).casefold() for event in self._assignment_events}
        used.update(str(pseudo).casefold() for pseudo in self._by_pseudo)
        k = max(self._max_k.get(canvas_id, -1) + 1, 0)
        for candidate_k in range(k, k + len(feedback_vault._REGISTRY_WORDS) * 4):
            payload = f"{canvas_id}:{candidate_k}".encode("utf-8")
            digest = hmac.new(secret, payload, hashlib.sha256).digest()
            index = int.from_bytes(digest[:8], "big") % len(feedback_vault._REGISTRY_WORDS)
            candidate = feedback_vault._REGISTRY_WORDS[index]
            if candidate.casefold() not in used:
                entry["pseudonym"] = candidate
                entry.pop("provisional", None)
                self._by_pseudo[candidate] = canvas_id
                self._pending_k[canvas_id] = candidate_k
                return
        raise feedback_vault.PseudonymRegistryError("The pseudonym registry is exhausted.")

    def resolve_provisional(self, canvas_id) -> str:
        cid = str(canvas_id)
        if cid not in self._provisional_ids:
            return str((self._by_id.get(cid) or {}).get("pseudonym") or "")
        entry = self._by_id[cid]
        old = str(entry.get("pseudonym") or "")
        if old:
            entry["provisional"] = False
            self._provisional_ids.remove(cid)
            self._assign_hmac(cid, entry)
        return str(entry.get("pseudonym") or "")

    def is_provisional(self, canvas_id) -> bool:
        return str(canvas_id) in self._provisional_ids

    def require_stable(self, canvas_id) -> None:
        if self.is_provisional(canvas_id):
            raise PseudonymProvisionalError("pseudonym_provisional")

    def set_pseudonym(self, canvas_id, value: str):
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is not None and str(entry.get("pseudonym") or "").casefold() == str(value or "").casefold():
            return
        raise PermanentPseudonymError("Existing student-to-Pokémon mappings are permanent.")

    def regenerate_pseudonym(self, canvas_id, roster_names=None):
        raise PermanentPseudonymError("Existing student-to-Pokémon mappings are permanent.")

    def conflicts(self) -> list[str]:
        return list(self.conflict_files)

    def entries(self) -> list[dict]:
        rows = super().entries()
        for row in rows:
            if self.is_provisional(row["canvas_id"]):
                row["provisional"] = True
        return rows


def runtime_local_lock_root() -> Path:
    from api import runtime_paths
    return runtime_paths.local_app_dir() / "locks"
