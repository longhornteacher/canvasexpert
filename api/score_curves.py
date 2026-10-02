"""Enumerated score-curve math and immutable local rule lifecycle."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

from api import score_ledger


class ScoreCurveError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _number(value, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ScoreCurveError("invalid_" + field)
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ScoreCurveError("invalid_" + field) from exc
    if not number.is_finite():
        raise ScoreCurveError("invalid_" + field)
    return number


def normalize_formula(formula: dict) -> dict:
    if not isinstance(formula, dict) or formula.get("model") != "gap_close":
        raise ScoreCurveError("unsupported_curve_formula")
    fraction = _number(formula.get("fraction"), "fraction")
    if fraction < 0 or fraction > 1:
        raise ScoreCurveError("invalid_fraction")
    return {"model": "gap_close", "fraction": format(fraction.normalize(), "f"),
            "rounding": "decimal_half_up_whole"}


def apply_formula(raw_score, points_possible, formula: dict) -> dict:
    raw = _number(raw_score, "score")
    possible = _number(points_possible, "points_possible")
    normalized = normalize_formula(formula)
    fraction = Decimal(normalized["fraction"])
    transformed = raw + fraction * (possible - raw)
    capped = min(transformed, possible) if raw <= possible else raw
    # Above-points authored input is preserved exactly. For ordinary input,
    # whole-point half-up rounding may not cross the assignment's exact cap.
    entered = (capped.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
               if raw <= possible else raw)
    if raw <= possible:
        entered = min(entered, possible)
    return {"raw_score": float(raw), "entered_score": float(entered),
            "unrounded_score": float(capped), "points_possible": float(possible),
            "formula": normalized,
            "disclosure": (f"{raw} + {fraction} × ({possible} − {raw}) = {capped} → {entered} "
                "(above-points input preserved)" if raw > possible else
                f"{raw} + {fraction} × ({possible} − {raw}) = {capped} → {entered} "
                "(half-up to whole point, capped at points possible)")}


def _active_state(course_id: str, root=None) -> tuple[list[dict], set[tuple[str, str]]]:
    events = score_ledger.list_rule_events(str(course_id), root=root)
    latest = {}
    exclusions = set()
    for event in sorted(events, key=lambda row: (str(row.get("timestamp") or ""),
                                                 str(row.get("action") or ""))):
        if event.get("action") == "exclude_assignment":
            exclusions.add((str(event.get("rule_id") or ""),
                            str(event.get("assignment_id") or "")))
            continue
        rule_id = str(event.get("rule_id") or "")
        if rule_id:
            latest[rule_id] = event
    return ([row for row in latest.values() if row.get("action") == "create"], exclusions)


def _active_events(course_id: str, root=None) -> list[dict]:
    return _active_state(course_id, root)[0]


def list_active_rules(course_id: str, *, root=None) -> list[dict]:
    return _active_events(course_id, root)


def resolve_rule(course_id: str, assignment_id: str, *, root=None) -> dict | None:
    rules, exclusions = _active_state(course_id, root)
    assignment_rules = [row for row in rules if row.get("assignment_id") == str(assignment_id)]
    course_rules = [row for row in rules if not row.get("assignment_id")
                    and (str(row.get("rule_id")), str(assignment_id)) not in exclusions]
    if len(assignment_rules) > 1 or len(course_rules) > 1:
        raise ScoreCurveError("ambiguous_active_rules")
    return assignment_rules[0] if assignment_rules else (course_rules[0] if course_rules else None)


def create_rule(course_id: str, formula: dict, assignment_id: str = "", *, root=None) -> dict:
    if not str(course_id or "").strip():
        raise ScoreCurveError("course_required")
    normalized = normalize_formula(formula)
    assignment_id = str(assignment_id or "")
    active = list_active_rules(str(course_id), root=root)
    if any(str(row.get("assignment_id") or "") == assignment_id for row in active):
        raise ScoreCurveError("active_rule_exists")
    rule = {"action": "create", "rule_id": uuid.uuid4().hex,
            "scope": "assignment" if assignment_id else "course",
            "assignment_id": assignment_id or None, "created_by": "teacher",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "formula": normalized, "preview": apply_formula(53, 100, normalized)}
    return score_ledger.append_rule_event(str(course_id), rule,
                                          idempotency_key="curve-rule:" + rule["rule_id"], root=root)


def deactivate_rule(course_id: str, rule_id: str, *, root=None) -> dict:
    rule_id = str(rule_id or "")
    rules = list_active_rules(str(course_id), root=root)
    target = next((row for row in rules if row.get("rule_id") == rule_id), None)
    if target is None:
        raise ScoreCurveError("rule_not_active")
    affected = []
    if not target.get("assignment_id"):
        for assignment_id in score_ledger.assignment_ids(str(course_id), root=root):
            rows = score_ledger.list_events(str(course_id), assignment_id, root=root)
            if any(row.get("curve_rule_id") == rule_id for row in rows):
                affected.append(assignment_id)
    deactivation = {"action": "deactivate", "rule_id": rule_id,
                    "scope": target.get("scope"),
                    "assignment_id": target.get("assignment_id"),
                    "affected_assignment_scopes": ([target.get("assignment_id")]
                        if target.get("assignment_id") else sorted(set(affected)))}
    saved = score_ledger.append_rule_event(str(course_id), deactivation,
                                           idempotency_key="curve-deactivate:" + rule_id, root=root)
    return {"ok": True, "rule_id": rule_id,
            "affected_assignment_scopes": saved.get("affected_assignment_scopes") or []}


def exclude_assignment(course_id: str, rule_id: str, assignment_id: str, *, root=None) -> dict:
    return score_ledger.append_rule_event(str(course_id), {
        "action": "exclude_assignment", "rule_id": str(rule_id),
        "scope": "course", "assignment_id": str(assignment_id)},
        idempotency_key=f"curve-exclude:{rule_id}:{assignment_id}", root=root)
