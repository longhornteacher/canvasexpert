"""Pseudonym vault v3 -- the real<->pseudonym map for feedback tools.

The single most sensitive artifact in the app: it is the only thing that can
re-identify pseudonymized work. It lives in the synced workspace
(`_System/Identity Vault/`), NEVER in the repo, and is NEVER transmitted
anywhere.

Keyed on the Canvas user id (stable, present in the Student Analysis CSV `ID`
column), so a student keeps the same opaque pseudonym forever -- across CSVs,
sources, and years.

v3 pseudonyms are one Pokemon species name drawn from the reviewed registry at
`api/data/pseudonym_words.json`.
See `docs/contracts/pseudonym-contract.md` for the full contract. This is a
pre-launch clean break: an on-disk document that is not schema_version 3, or
that still carries a retired `pseudo_first`/`pseudo_last` component field,
fails closed rather than being migrated or dual-read.

Pure stdlib; offline-testable.
"""
import fnmatch
import hashlib
import json
import os
import re
import socket
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from api.storage_support import atomic_write_json, interprocess_lock

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REGISTRY_PATH = os.path.join(_MODULE_DIR, "data", "pseudonym_words.json")
_REGISTRY_CATEGORIES = ("pokemon",)
_MIN_REGISTRY_WORDS = 256
_WORD_RE = re.compile(r"^[A-Z][a-z]+$")

SCHEMA_VERSION = 3


def machine_id() -> str:
    """Return the local machine label used to annotate private vault writes."""
    override = str(os.environ.get("CANVAS_EXPERT_MACHINE_ID") or "").strip()
    if override:
        return override
    for value in (os.environ.get("COMPUTERNAME"), os.environ.get("HOSTNAME"), socket.gethostname()):
        label = str(value or "").strip()
        if label:
            return label
    return "local-machine"


class PseudonymCollisionError(ValueError):
    """A pseudonym is already held by a different student."""


class InvalidPseudonymError(ValueError):
    """A supplied pseudonym value is not one available registry word."""


class PseudonymRegistryError(RuntimeError):
    """The reviewed pseudonym registry is missing or structurally invalid."""


class VaultSchemaError(ValueError):
    """The on-disk Identity Vault document is not a valid schema-v3 vault."""


def _load_registry() -> tuple[list[str], dict[str, str]]:
    """Load and validate `api/data/pseudonym_words.json`.

    Fails closed: any structural problem (missing file, wrong categories, a
    word that is not one ASCII title-case token, a duplicate) raises
    `PseudonymRegistryError` at import time rather than falling back to a
    placeholder or numbered word. Returns (words, category_by_lower); the
    category map exists only so this loader can prove each word appears in
    exactly one category once, and is not otherwise used by `Vault`.
    """
    try:
        with open(_REGISTRY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise PseudonymRegistryError(
            f"Pseudonym registry at {_REGISTRY_PATH} could not be read: {exc}"
        ) from exc

    if not isinstance(data, dict) or set(data) != set(_REGISTRY_CATEGORIES):
        raise PseudonymRegistryError(
            "Pseudonym registry must have exactly the categories "
            f"{sorted(_REGISTRY_CATEGORIES)}."
        )

    words: list[str] = []
    category_by_lower: dict[str, str] = {}
    for category in _REGISTRY_CATEGORIES:
        entries = data[category]
        if not isinstance(entries, list) or not entries:
            raise PseudonymRegistryError(
                f"Registry category '{category}' must be a non-empty list."
            )
        for word in entries:
            if not isinstance(word, str) or not _WORD_RE.fullmatch(word) or not word.isascii():
                raise PseudonymRegistryError(
                    f"Registry word {word!r} in '{category}' must be one ASCII "
                    "title-case alphabetic token."
                )
            folded = word.lower()
            if folded in category_by_lower:
                raise PseudonymRegistryError(
                    f"Registry word {word!r} is duplicated (case-insensitively)."
                )
            category_by_lower[folded] = category
            words.append(word)

    if len(words) < _MIN_REGISTRY_WORDS:
        raise PseudonymRegistryError(
            f"Pseudonym registry has {len(words)} words; at least "
            f"{_MIN_REGISTRY_WORDS} are required."
        )
    return words, category_by_lower


_REGISTRY_WORDS, _REGISTRY_CATEGORY_BY_LOWER = _load_registry()
_REGISTRY_CANONICAL_BY_LOWER = {w.lower(): w for w in _REGISTRY_WORDS}

# The vault never releases a word (see the module docstring), so the registry
# only ever drains as a teacher's roster grows year over year. Growing it back
# means writing and reviewing a bigger `pseudonym_words.json` -- a real amount
# of lead time, not a quick fix -- so the warning below has to fire long
# before `_select_available_word` would actually raise `PseudonymRegistryError`.
# A fifth of the registry left unassigned is the sensible line: at a rough
# 170-student-a-year pace it still leaves more than a year to notice and act.
_LOW_RUNWAY_FRACTION = 0.2


def _canonical_registry_word(value: object) -> str | None:
    """The exact registry-cased word for `value`, or None if `value` is not
    a single word present in the registry (case-insensitively)."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate.split()) != 1:
        return None
    return _REGISTRY_CANONICAL_BY_LOWER.get(candidate.lower())


def registry_runway(words_assigned: int) -> dict:
    """Words total/assigned/remaining and whether the registry is running low.

    Pure and I/O-free: `words_assigned` is the caller's own count of
    currently-held pseudonyms (a `Vault` passes `len(self)`), so this stays
    testable without a real vault file and callers who already know their
    count do not need to construct a `Vault` just to ask this question.
    `low_runway` flips to True once `words_remaining` drops to or below
    `_LOW_RUNWAY_FRACTION` of `words_total`. This is a forecast, not a
    failure: `PseudonymRegistryError` from `_select_available_word` remains
    the only terminal behavior on true exhaustion. The point of this function
    is that nobody should ever actually reach that error unwarned.
    """
    words_total = len(_REGISTRY_WORDS)
    words_assigned = max(int(words_assigned), 0)
    words_remaining = max(words_total - words_assigned, 0)
    low_runway = words_remaining <= round(words_total * _LOW_RUNWAY_FRACTION)
    return {
        "words_total": words_total,
        "words_assigned": words_assigned,
        "words_remaining": words_remaining,
        "low_runway": low_runway,
    }


class Vault:
    def __init__(self, path: str):
        self.path = path
        self._by_id = {}          # canvas_id(str) -> {pseudonym, real_name, sis_id,
                                  #                    nicknames, first_seen}
        self._by_pseudo = {}      # pseudonym -> canvas_id(str)
        self.conflict_files: list[str] = []
        self._load()

    def _load(self):
        data = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self._validate_document_shape(data)
        self._apply_document(data)
        self.conflict_files = self._scan_conflicts()

    @staticmethod
    def _validate_document_shape(data: dict) -> None:
        """Fail closed on any vault document that predates schema v3.

        This is the pre-launch clean break: no migration, no dual-read, no
        silent rewrite. A present document must declare `schema_version: 3`
        and must not carry a retired `pseudo_first`/`pseudo_last` field on
        any entry. Moving the offending file aside is a deliberate,
        recoverable operator action -- never something this code does.
        """
        if not isinstance(data, dict):
            raise VaultSchemaError("Identity Vault file is not a JSON object.")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise VaultSchemaError(
                "Identity Vault schema_version is missing or unsupported "
                f"(expected {SCHEMA_VERSION}). This is a pre-launch clean "
                "break: move the existing vault file aside to start a fresh one."
            )
        raw_entries = data.get("by_canvas_id", {})
        if not isinstance(raw_entries, dict):
            raise VaultSchemaError("Identity Vault by_canvas_id must be an object.")
        for entry in raw_entries.values():
            if isinstance(entry, dict) and ("pseudo_first" in entry or "pseudo_last" in entry):
                raise VaultSchemaError(
                    "Identity Vault entry still carries a retired pseudo_first/"
                    "pseudo_last field from the two-part pseudonym scheme."
                )

    def _scan_conflicts(self) -> list[str]:
        """OneDrive can fork this file across machines, naming copies like
        ``vault-DESKTOP123.json`` or ``vault (1).json``. Find any such
        artifact beside the canonical file (never the file itself or its
        ``.lock`` companion) without touching or merging them."""
        directory = os.path.dirname(self.path) or "."
        if not os.path.isdir(directory):
            return []
        canonical = os.path.basename(self.path)
        lock_name = canonical + ".lock"
        found = []
        for entry in sorted(os.listdir(directory)):
            if entry in (canonical, lock_name):
                continue
            if fnmatch.fnmatch(entry, "vault*.json"):
                found.append(entry)
        return found

    def conflicts(self) -> list[str]:
        """Basenames of OneDrive conflict-copy artifacts found beside this
        vault at last load. Empty means no fork detected."""
        return list(self.conflict_files)

    def _apply_document(self, data: dict):
        """Replace in-memory maps with one freshly loaded document."""
        raw_entries = data.get("by_canvas_id", {}) if isinstance(data, dict) else {}
        self._by_id = raw_entries if isinstance(raw_entries, dict) else {}
        self._by_pseudo = {
            v["pseudonym"]: cid
            for cid, v in self._by_id.items()
            if isinstance(v, dict) and v.get("pseudonym")
        }

    def _lock_path(self) -> Path:
        path = Path(self.path)
        return path.with_name(path.name + ".lock")

    def _save_unlocked(self):
        atomic_write_json(Path(self.path), {
            "schema_version": SCHEMA_VERSION,
            "by_canvas_id": self._by_id,
            "written_by": machine_id(),
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "entry_count": len(self._by_id),
        })

    @contextmanager
    def transaction(self):
        """Reload, mutate, and atomically save this vault under one lock."""
        with interprocess_lock(self._lock_path()):
            self._load()
            try:
                yield self
            except Exception:
                raise
            else:
                self._save_unlocked()

    def save(self):
        with interprocess_lock(self._lock_path()):
            self._save_unlocked()

    def _used_pseudonym_tokens(self, exclude: str = "") -> set:
        """Case-folded tokens of every pseudonym currently held, optionally
        excluding one pseudonym (so `regenerate_pseudonym` can replace a
        student's own word without treating it as "already taken")."""
        excluded = exclude.lower()
        return {p.lower() for p in self._by_pseudo if p.lower() != excluded}

    def _select_available_word(self, banned: set, stable_key: str = "") -> str:
        """Select a registry word by deterministic hash-and-probe.

        The starting position is derived from the stable Canvas user key and
        collisions probe in registry order.  The vault, rather than a caller's
        partial roster, supplies the banned set so every machine makes the
        same decision from the same shared state.
        """
        if not _REGISTRY_WORDS:
            raise PseudonymRegistryError(
                "The pseudonym registry is empty."
            )
        digest = hashlib.sha256(str(stable_key).encode("utf-8")).digest()
        start = int.from_bytes(digest[:8], "big") % len(_REGISTRY_WORDS)
        for offset in range(len(_REGISTRY_WORDS)):
            word = _REGISTRY_WORDS[(start + offset) % len(_REGISTRY_WORDS)]
            if word.lower() not in banned:
                return word
        raise PseudonymRegistryError(
            "The pseudonym registry is exhausted: every word is already "
            "assigned or collides with a current vault identity."
        )

    @staticmethod
    def _roster_tokens(roster_names: set | None) -> set:
        tokens: set = set()
        if roster_names:
            for name in roster_names:
                tokens.update(t.lower() for t in str(name).split())
        return tokens

    def get_or_assign(self, canvas_id, real_name="", sis_id="",
                      roster_names: set | None = None) -> str:
        """Return the stable pseudonym for this student, assigning one
        available registry word on first sight. Backfills name/sis if they
        were unknown before. If `roster_names` is provided, the assigned
        word will avoid colliding with any real roster token. Does not
        auto-save."""
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            # `roster_names` remains accepted for source compatibility, but
            # intentionally does not participate in assignment.  A caller
            # may only have a partial roster; the shared vault is the one
            # authoritative source for identity-token collisions.
            banned = self._used_pseudonym_tokens() | self._vault_identity_tokens()
            if real_name:
                banned |= {token.lower() for token in str(real_name).split()}
            pseudonym = self._select_available_word(banned, cid)
            entry = {
                "pseudonym": pseudonym,
                "real_name": real_name,
                "sis_id": sis_id,
                "nicknames": [],
                "first_seen": datetime.now().isoformat(timespec="seconds"),
            }
            self._by_id[cid] = entry
            self._by_pseudo[pseudonym] = cid
        else:
            if real_name and not entry.get("real_name"):
                entry["real_name"] = real_name
            if sis_id and not entry.get("sis_id"):
                entry["sis_id"] = sis_id
            if not entry.get("pseudonym"):
                banned = self._used_pseudonym_tokens() | self._vault_identity_tokens()
                pseudonym = self._select_available_word(banned, cid)
                entry["pseudonym"] = pseudonym
                self._by_pseudo[pseudonym] = cid
        return entry["pseudonym"]

    def _vault_identity_tokens(self) -> set:
        """Case-folded name tokens already recorded in this vault."""
        tokens: set = set()
        for entry in self._by_id.values():
            if not isinstance(entry, dict):
                continue
            for field in ("real_name", "sis_id"):
                tokens.update(str(entry.get(field) or "").split())
            for nickname in entry.get("nicknames", []) or []:
                tokens.update(str(nickname).split())
        return {token.lower() for token in tokens if token}

    def remember_identity(self, canvas_id, real_name="", sis_id="") -> None:
        """Record identity metadata before assignment without choosing a word."""
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            self._by_id[cid] = {
                "pseudonym": "",
                "real_name": str(real_name or ""),
                "sis_id": str(sis_id or ""),
                "nicknames": [],
                "first_seen": datetime.now().isoformat(timespec="seconds"),
            }
            return
        if real_name and not entry.get("real_name"):
            entry["real_name"] = str(real_name)
        if sis_id and not entry.get("sis_id"):
            entry["sis_id"] = str(sis_id)

    def set_nicknames(self, canvas_id, nicknames: list[str]):
        """Set the nicknames for a student (dedup case-insensitively, strip, no empty strings).
        Caller must call save()."""
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            return
        seen: set = set()
        clean = []
        for n in nicknames:
            n_stripped = n.strip()
            if n_stripped and n_stripped.lower() not in seen:
                seen.add(n_stripped.lower())
                clean.append(n_stripped)
        entry["nicknames"] = sorted(clean)

    def add_nicknames(self, canvas_id, nicknames: list[str]):
        """Merge nicknames into the existing set (dedup case-insensitively, strip,
        no empty strings) -- does NOT clobber teacher-entered ones. Caller saves."""
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            return
        existing = entry.get("nicknames", [])
        seen = {n.lower() for n in existing}
        merged = list(existing)
        for n in nicknames:
            ns = n.strip()
            if ns and ns.lower() not in seen:
                seen.add(ns.lower())
                merged.append(ns)
        entry["nicknames"] = sorted(merged)

    def set_pseudonym(self, canvas_id, value: str):
        """Manual override from the UI/MCP. Caller must call save().

        `value` must be exactly one word already present in the registry
        (matched case-insensitively; the canonical registry casing is what
        gets stored). Raises `InvalidPseudonymError` for a non-string,
        blank, multiword, or out-of-registry value, and
        `PseudonymCollisionError` if a different student already holds it.
        Both checks run before any mutation, so a refused rename leaves the
        vault untouched without depending on the transaction to roll back.
        """
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            return
        canonical = _canonical_registry_word(value)
        if canonical is None:
            raise InvalidPseudonymError(
                "pseudonym must be exactly one word from the reviewed registry."
            )
        holder = self._canvas_id_holding(canonical)
        if holder is not None and holder != cid:
            raise PseudonymCollisionError(
                f"The pseudonym '{canonical}' already belongs to another "
                "student. Choose a different one."
            )
        old_pseudo = entry.get("pseudonym", "")
        entry["pseudonym"] = canonical
        self._by_pseudo.pop(old_pseudo, None)
        self._by_pseudo[canonical] = cid

    def _canvas_id_holding(self, pseudonym: str):
        """The canvas_id already using this pseudonym, or None.

        Case-insensitive, so a rename that only changes capitalization is
        recognized as the same student rather than read as a collision.
        """
        wanted = str(pseudonym or "").strip().lower()
        if not wanted:
            return None
        for existing, cid in self._by_pseudo.items():
            if str(existing).strip().lower() == wanted:
                return cid
        return None

    def regenerate_pseudonym(self, canvas_id, roster_names: set | None = None):
        """Assign a new available registry word, collision-checked, and
        guaranteed different from the current one. Caller must call save()."""
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            return
        old_pseudo = entry.get("pseudonym", "")
        banned = self._used_pseudonym_tokens(exclude=old_pseudo) | self._roster_tokens(roster_names)
        # Also ban the student's own current word so regenerate always hands
        # back something different.
        banned.add(old_pseudo.lower())
        new_pseudonym = self._select_available_word(banned, f"{cid}:regenerate:{old_pseudo}")
        entry["pseudonym"] = new_pseudonym
        self._by_pseudo.pop(old_pseudo, None)
        self._by_pseudo[new_pseudonym] = cid

    def reverse(self, pseudonym: str):
        """Pseudonym -> {canvas_id, real_name, sis_id} or None."""
        cid = self._by_pseudo.get(pseudonym)
        if cid is None:
            return None
        e = self._by_id[cid]
        return {"canvas_id": cid, "real_name": e.get("real_name", ""),
                "sis_id": e.get("sis_id", "")}

    def entries(self) -> list[dict]:
        """Return all entries as a list for the UI table."""
        result = []
        for cid, e in self._by_id.items():
            result.append({
                "canvas_id": cid,
                "real_name": e.get("real_name", ""),
                "sis_id": e.get("sis_id", ""),
                "pseudonym": e.get("pseudonym", ""),
                "nicknames": e.get("nicknames", []),
                "first_seen": e.get("first_seen", ""),
            })
        # Sort by real_name for the UI
        result.sort(key=lambda x: x["real_name"].lower())
        return result

    def all_real_identifiers(self):
        """(names, ids) sets of every real identifier the vault knows -- used by the
        outbound safety scan to detect any leak before transmission.
        Now includes nicknames."""
        names, ids = set(), set()
        for cid, e in self._by_id.items():
            ids.add(str(cid))
            if e.get("sis_id"):
                ids.add(str(e["sis_id"]))
            if e.get("real_name"):
                names.add(e["real_name"])
            for nn in e.get("nicknames", []):
                if nn:
                    names.add(nn)
        return names, ids

    def __len__(self):
        return len(self._by_id)

    def registry_runway(self) -> dict:
        """This vault's view of `registry_runway`: how much of the shared
        pseudonym registry is left, using this vault's own assigned count."""
        return registry_runway(len(self))
