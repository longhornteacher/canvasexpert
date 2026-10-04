"""Shared pseudonym rules used by the append-only Identity Vault.

Production storage lives in ``api.shared_vault``. This module contains the
reviewed word registry and identity projection helpers, and never reads or
writes vault files.
"""
import hashlib
import json
import os
import re
from datetime import datetime

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_REGISTRY_PATH = os.path.join(_MODULE_DIR, "data", "pseudonym_words.json")
_REGISTRY_CATEGORIES = ("pokemon",)
_MIN_REGISTRY_WORDS = 256
_WORD_RE = re.compile(r"^[A-Z][a-z]+$")

class PseudonymRegistryError(RuntimeError):
    """The reviewed pseudonym registry is missing or structurally invalid."""


class VaultSchemaError(ValueError):
    """The shared Identity Vault seed or journal is structurally invalid."""


def _load_registry() -> tuple[list[str], dict[str, str]]:
    """Load and validate `api/data/pseudonym_words.json`.

    Fails closed: any structural problem (missing file, wrong categories, a
    word that is not one ASCII title-case token, a duplicate) raises
    `PseudonymRegistryError` at import time rather than falling back to a
    placeholder or numbered word. Returns (words, category_by_lower); the
    category map exists only so this loader can prove each word appears in
    exactly one category once, and is not otherwise used by the shared vault.
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
    currently-held pseudonyms (a shared vault passes `len(self)`), so this stays
    testable without a real vault file and callers who already know their
    count do not need to construct a vault just to ask this question.
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


class IdentityVault:
    """Shared identity operations used by the production append-only vault."""

    def _used_pseudonym_tokens(self) -> set:
        """Case-folded tokens of every pseudonym currently held."""
        return {p.casefold() for p in self._by_pseudo}

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
            if word.casefold() not in banned:
                return word
        raise PseudonymRegistryError(
            "The pseudonym registry is exhausted: every word is already "
            "assigned or collides with a current vault identity."
        )

    def get_or_assign(self, canvas_id, real_name="", sis_id="") -> str:
        """Return the stable pseudonym for this student, assigning one
        available registry word on first sight. Backfills name/sis if they
        were unknown before. Does not auto-save."""
        cid = str(canvas_id)
        entry = self._by_id.get(cid)
        if entry is None:
            banned = self._used_pseudonym_tokens() | self._vault_identity_tokens()
            if real_name:
                banned |= {token.casefold() for token in str(real_name).split()}
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
        return {token.casefold() for token in tokens if token}

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
