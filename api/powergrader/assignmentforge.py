"""Private AssignmentForge metadata associated with pushed assignments.

AssignmentForge supports and corrections never travel through Canvas. The
Operation Ledger retains the authored envelope privately; this module resolves
that record by the exact assignment ID created by the ledger so ScoringSession
can use teacher-authored corrections without inventing a second source of truth.
"""

from __future__ import annotations

import copy

from api.operation_ledger import operations


ASSIGNMENT_KIND = "content.assignment"


def _created_ids(target: dict) -> list[str]:
    ids = []
    target_id = target.get("returned_object_id")
    if target_id not in (None, ""):
        ids.append(str(target_id))
    for step in target.get("steps") or []:
        key = str(step.get("step_key") or "")
        if not (key == "create_assignment" or key.startswith("create_tier_assignment:")):
            continue
        value = step.get("returned_object_id")
        if value not in (None, ""):
            ids.append(str(value))
    return list(dict.fromkeys(ids))


def _tier_for_id(payload: dict, assignment_id: str, target: dict) -> str:
    tiers = payload.get("tiers") or []
    if not isinstance(tiers, list):
        return ""
    for index, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            continue
        step = next(
            (row for row in target.get("steps") or []
             if row.get("step_key") == f"create_tier_assignment:{index}"),
            None,
        )
        if step and str(step.get("returned_object_id") or "") == str(assignment_id):
            return str(tier.get("tag") or tier.get("tier") or tier.get("label") or "").strip()
    return ""


def for_assignment(course_id: str, assignment_id: str) -> dict:
    """Return private corrections/tier metadata for one exact Canvas assignment."""
    wanted_course = str(course_id or "").strip()
    wanted_assignment = str(assignment_id or "").strip()
    if not wanted_course or not wanted_assignment:
        return {}
    try:
        records = operations.list_operations()
    except Exception:
        return {}

    # Newer operations win if a teacher deliberately rebuilt the same object;
    # exact IDs still prevent title-based or guessed association.
    try:
        for operation in reversed(records):
            if not isinstance(operation, dict) or operation.get("kind") != ASSIGNMENT_KIND:
                continue
            payload = operation.get("normalized_payload") or {}
            if not isinstance(payload, dict):
                continue
            for target in operation.get("targets") or []:
                if not isinstance(target, dict):
                    continue
                if str(target.get("course_id") or "") != wanted_course:
                    continue
                if wanted_assignment not in _created_ids(target):
                    continue
                corrections = payload.get("corrections")
                if not isinstance(corrections, dict) or not corrections:
                    return {}
                return {
                    "corrections": copy.deepcopy(corrections),
                    "tier": _tier_for_id(payload, wanted_assignment, target),
                }
    except Exception:
        return {}
    return {}
