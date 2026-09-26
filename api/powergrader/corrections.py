"""Teacher-authored AssignmentForge corrections for scoring feedback."""

from __future__ import annotations


def _lookup(mapping: dict, key: object):
    if not isinstance(mapping, dict):
        return None
    text = str(key or "")
    if text in mapping:
        return mapping[text]
    folded = text.casefold()
    for candidate, value in mapping.items():
        if str(candidate).casefold() == folded:
            return value
    return None


def correction_for_item(corrections: dict, item_id: object, tier: str = "") -> dict | None:
    """Resolve one exact packet item, preferring shared over tier selection."""
    entry = _lookup(corrections, item_id)
    if not isinstance(entry, dict):
        return None
    shared = entry.get("shared")
    if isinstance(shared, dict):
        return shared
    by_tier = entry.get("by_tier")
    if not isinstance(by_tier, dict):
        return None
    selected = _lookup(by_tier, tier)
    return selected if isinstance(selected, dict) else None
