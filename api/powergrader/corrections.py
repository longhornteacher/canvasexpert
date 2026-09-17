"""Teacher-authored AssignmentForge corrections for scoring feedback."""

from __future__ import annotations

import math


CORRECTION_MARKER = "📋 COPY THIS:"


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


def below_met(result: dict, possible: object) -> bool:
    """Use explicit mastery when supplied; otherwise compare numeric points."""
    if isinstance(result.get("met"), bool):
        return not result["met"]
    status = str(result.get("status") or "").strip().casefold()
    if status:
        if status in {"met", "mastered", "proficient", "complete", "completed"}:
            return False
        if status in {"below", "not_met", "not met", "missed", "developing"}:
            return True
    score = result.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return False
    if isinstance(possible, bool) or not isinstance(possible, (int, float)):
        return False
    if not (math.isfinite(float(score)) and math.isfinite(float(possible))):
        return False
    return float(score) < float(possible)


def append_correction(feedback: str, correction: dict | None) -> str:
    """Append one plain-text, student-facing correction block exactly once."""
    if not isinstance(correction, dict) or CORRECTION_MARKER in str(feedback or ""):
        return str(feedback or "")
    answer = str(correction.get("answer") or "").strip()
    why = str(correction.get("why") or "").strip()
    if not answer or not why:
        return str(feedback or "")
    block = f"{CORRECTION_MARKER}\nAnswer: {answer}\nWhy: {why}"
    original = str(feedback or "").strip()
    return f"{original}\n\n{block}".strip() if original else block


def inject(results: list[dict], safe_bundle: dict, *, corrections: dict, tier: str = "") -> list[dict]:
    """Return result rows with bounded corrections added to missed items."""
    if not isinstance(corrections, dict) or not corrections:
        return results
    possible_by_key = {}
    for student in safe_bundle.get("students") or []:
        pseudonym = student.get("pseudonym")
        for response in student.get("responses") or []:
            possible_by_key[(pseudonym, str(response.get("item_id") or ""))] = response.get("possible")
    updated = []
    for result in results or []:
        row = dict(result)
        key = (row.get("pseudonym"), str(row.get("item_id") or ""))
        possible = possible_by_key.get(key)
        correction = correction_for_item(corrections, key[1], tier)
        if correction is not None and below_met(row, possible):
            row["feedback"] = append_correction(row.get("feedback") or "", correction)
        updated.append(row)
    return updated
