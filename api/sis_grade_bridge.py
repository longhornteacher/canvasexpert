"""Assistant-facing SIS grade-bridge use case.

This is the shared non-HTTP boundary used by MCP.  Live Canvas behavior stays
inside the ledger adapter; this module creates one frozen operation and shapes
only aggregate, student-free results.
"""

from __future__ import annotations

import copy

from api.operation_ledger import batches, executor, models, operations, receipts, registry
from api.operation_ledger.adapters.sis_grade_bridge import KIND
from api.operation_ledger.adapters import differentiated_bridge
from api.platform_services import config
from api.platform_services import canvas_client


def _current_course(course_id: str) -> bool:
    wanted = str(course_id or "").strip()
    return bool(wanted) and wanted in {
        str(course.get("id") or "").strip()
        for course in config.active_courses()
    }


def list_sis_grade_bridges(course_id: str) -> dict:
    course_key = str(course_id or "").strip()
    if not course_key:
        return {"ok": False, "error": "course_id is required"}
    saved_ids = {
        str(course.get("id") or "").strip()
        for course in [*(config.saved_courses() or []), *(config.active_courses() or [])]
        if str(course.get("id") or "").strip()
    }
    if course_key not in saved_ids:
        return {
            "ok": False,
            "error": (
                f"Unknown course_id '{course_key}'; call list_courses and use a "
                "returned course_id."
            ),
        }
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}
    try:
        records = config.list_sis_grade_bridges(course_key)
    except Exception:
        return {"ok": False, "error": "bridge registrations could not be read"}
    return {
        "ok": True,
        "course_id": course_key,
        "bridges": [
            {
                "family_title": record.get("family_title"),
                "source_count": len(record.get("source_assignment_ids") or []),
                "bridge_assignment_id": record.get("bridge_assignment_id"),
                "registered": True,
            }
            for record in records
        ],
    }


def _course_assignments(course_id: str) -> list[dict]:
    rows, error, complete = canvas_client.canvas_get_all_complete(
        f"/api/v1/courses/{course_id}/assignments", {"per_page": 100}
    )
    if error or not complete or not isinstance(rows, list):
        raise ValueError("assignment discovery is incomplete")
    return [row for row in rows if isinstance(row, dict)]


def _student_free_coverage(course_id: str, source_ids: list[str]) -> dict:
    member_sets = []
    for assignment_id in source_ids:
        overrides, error, complete = canvas_client.canvas_get_all_complete(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides",
            {"per_page": 100},
        )
        if error or not complete or not isinstance(overrides, list):
            return {"complete": False, "source_count": len(source_ids), "member_count": 0, "overlap_count": None}
        members = set()
        for override in overrides:
            for value in (override.get("student_ids") or []):
                if str(value).strip():
                    members.add(str(value).strip())
        member_sets.append(members)
    union = set().union(*member_sets) if member_sets else set()
    overlaps = sum(1 for member in union if sum(member in values for values in member_sets) > 1)
    return {
        "complete": bool(member_sets) and all(member_sets),
        "source_count": len(source_ids),
        "member_count": len(union),
        "overlap_count": overlaps,
        "exact_source_member_coverage": overlaps == 0 and bool(member_sets),
    }


def reconcile_sis_grade_bridges(course_id: str, *, assignments: list[dict] | None = None) -> dict:
    """Student-free CE-owned family reconciliation matrix.

    This is deliberately read-only. Missing registrations and bridge repairs
    are represented as actionable rows for the reviewed operation path.
    """
    course_key = str(course_id or "").strip()
    if not course_key:
        return {"ok": False, "error": "course_id is required"}
    try:
        rows = assignments if assignments is not None else _course_assignments(course_key)
        families = differentiated_bridge.discover_families(
            rows, config.list_sis_grade_bridges(course_key)
        )
        registrations = config.list_sis_grade_bridges(course_key)
    except Exception as exc:
        return {"ok": False, "error": "bridge discovery could not be completed", "blocking": True}

    matrix = []
    for family in families:
        title = family["family_title"]
        registration = next(
            (record for record in registrations
             if record.get("family_key") == family["family_key"]
             or str(record.get("family_title") or "").casefold() == title.casefold()),
            None,
        )
        source_ids = family["source_assignment_ids"]
        coverage = _student_free_coverage(course_key, source_ids) if assignments is None else {
            "complete": None, "source_count": len(source_ids),
            "member_count": None, "overlap_count": None,
            "exact_source_member_coverage": None,
        }
        reasons = []
        status = "synced"
        bridge = None
        if family["source_count"] < 2 or len(family["source_tiers"]) != family["source_count"]:
            status = "incomplete"
            reasons.append("exact_source_member_coverage_unavailable")
        if family["bridge_count"] > 1:
            status = "blocked"
            reasons.append("multiple_bridge_targets")
        if registration is None:
            status = "missing" if status == "synced" else status
            reasons.append("registration_missing")
        elif str(registration.get("bridge_assignment_id") or "") not in family["bridge_assignment_ids"]:
            status = "blocked"
            reasons.append("registered_bridge_not_in_family")
        elif family["bridge_count"] == 1:
            bridge = next((row for row in rows if str(row.get("id")) == family["bridge_assignment_ids"][0]), None)
            if bridge:
                source_rows = [row for row in rows if str(row.get("id")) in source_ids]
                raw_due = next((row.get("due_at") for row in source_rows if row.get("due_at")), None)
                expected_due = (
                    differentiated_bridge.require_family_delivery(raw_due, "matrix")[2]
                    if raw_due else None
                )
                expected = {
                    "name": differentiated_bridge.bridge_title(title),
                    "description": differentiated_bridge.bridge_description(),
                    "due_at": expected_due,
                }
                drift = sorted(field for field, value in expected.items() if bridge.get(field) != value)
                if drift:
                    status = "drifted"
                    reasons.append("bridge_shape_drift")
                    family["drift_fields"] = drift
        matrix.append({
            "family_key": family["family_key"],
            "family_title": title,
            "status": status,
            "source_assignment_ids": source_ids,
            "source_titles": family.get("source_titles", []),
            "source_tiers": family["source_tiers"],
            "bridge_assignment_id": (family["bridge_assignment_ids"][0] if len(family["bridge_assignment_ids"]) == 1 else None),
            "source_count": family["source_count"],
            "bridge_count": family["bridge_count"],
            "grading_excluded": family["grading_excluded"],
            "bridge_eligible": status not in {"blocked", "incomplete"},
            "coverage": coverage,
            "drift_fields": family.get("drift_fields", []),
            "reasons": reasons,
            "identity_source": family["identity_source"],
        })
    return {"ok": True, "course_id": course_key, "matrix": matrix}


def preview_sis_grade_bridge(
    course_id: str, family_title: str, *, write_origin: str = "assistant",
    discovered_family: dict | None = None,
) -> dict:
    course_key = str(course_id or "").strip()
    title = str(family_title or "").strip()
    if not course_key or not title:
        return {"ok": False, "error": "course_id and family_title are required"}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}

    adapter = registry.get_adapter(KIND)
    try:
        registration = config.get_sis_grade_bridge(course_key, title)
        payload = adapter.build_payload({
            "course_id": course_key,
            "family_title": title,
            "registration": registration,
            "write_origin": write_origin,
            "discovered_family": discovered_family,
        })
        provisional = adapter.verify_targets(
            payload, [{"course_id": course_key}]
        )[0]
        baseline = adapter.capture_baseline(payload, provisional)
        if baseline.get("blocking_error"):
            result = {
                "ok": False,
                "error": baseline["blocking_error"],
                "blocking": True,
            }
            fields = baseline.get("drift_fields")
            if isinstance(fields, list) and all(isinstance(field, str) for field in fields):
                result["drift_fields"] = sorted(set(fields))
            return result
        payload = adapter.freeze_payload(payload, baseline)
        target = adapter.verify_targets(
            payload, [{"course_id": course_key}]
        )[0]
        # Re-read once against the frozen identity so the persisted baseline is
        # already the exact state that apply will drift-check.
        baseline = adapter.capture_baseline(payload, target)
        if baseline.get("blocking_error"):
            result = {
                "ok": False,
                "error": baseline["blocking_error"],
                "blocking": True,
            }
            fields = baseline.get("drift_fields")
            if isinstance(fields, list) and all(isinstance(field, str) for field in fields):
                result["drift_fields"] = sorted(set(fields))
            return result

        target_record = models.new_target(
            target_key=target["target_key"],
            idempotency_key=target["idempotency_key"],
            course_id=target["course_id"],
            baseline=baseline,
            steps=adapter.initial_steps(payload, baseline),
        )
        operation_id = models.new_operation_id()
        operation = models.new_operation(
            operation_id=operation_id,
            kind=KIND,
            source_ref={
                "type": (
                    "sis_grade_bridge_routine"
                    if write_origin == "routine"
                    else "sis_grade_bridge"
                )
            },
            source_digest=adapter.source_digest(payload),
            normalized_payload=payload,
            targets=[target_record],
        )
        operations.create_operation(operation)
        frozen = adapter.freeze_review(payload, target_record, baseline)
        batch = batches.freeze_batch(
            [operation_id], {operation_id: [frozen]}
        )
        operations.set_operation_review(operation_id, batch)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "blocking": True}
    except Exception:
        return {"ok": False, "error": "bridge preview could not be prepared"}

    return {
        "ok": True,
        "operation_id": operation_id,
        "batch_id": batch["batch_id"],
        "review_digest": batch["review_digest"],
        "preview": frozen,
    }


def preview_sis_grade_bridge_reconciliation(
    course_id: str, family_title: str, *, assignments: list[dict] | None = None,
) -> dict:
    """Create a reviewed operation for a CE-discovered missing/drifted family."""
    matrix = reconcile_sis_grade_bridges(course_id, assignments=assignments)
    if not matrix.get("ok"):
        return matrix
    row = next((item for item in matrix["matrix"] if item["family_title"] == family_title), None)
    if row is None:
        return {"ok": False, "error": "differentiated family was not discovered", "blocking": True}
    if row["status"] not in {"missing", "drifted"}:
        return {"ok": False, "error": f"family is {row['status']}", "blocking": row["status"] in {"blocked", "incomplete"}}
    family = dict(row)
    return preview_sis_grade_bridge(course_id, family_title, discovered_family=family)


def apply_sis_grade_bridge(
    operation_id: str, batch_id: str, review_digest: str
) -> dict:
    operation_key = str(operation_id or "").strip()
    batch_key = str(batch_id or "").strip()
    digest = str(review_digest or "").strip()
    if not operation_key or not batch_key or not digest:
        return {
            "ok": False,
            "error": "operation_id, batch_id, and review_digest are required",
        }
    operation = operations.get_operation(operation_key)
    if operation is None or operation.get("kind") != KIND:
        return {"ok": False, "error": "bridge operation was not found"}
    try:
        result = executor.apply_operation(operation_key, batch_key, digest)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "bridge apply could not complete"}

    return _result_projection(operation_key, result, operation)


def _result_projection(operation_key: str, result: dict, fallback: dict) -> dict:
    stored = operations.get_operation(operation_key) or fallback
    target = (stored.get("targets") or [{}])[0]
    baseline = target.get("baseline") or {}
    counts = copy.deepcopy(baseline.get("counts") or {})
    action_counts = {
        "score": "copied_scores",
        "excuse": "copied_excused",
        "missing": "missing_zeroes",
        "clear": "cleared_prior_values",
    }
    for count_key in action_counts.values():
        counts[count_key] = 0
    step_rows = [
        {
            "step_key": step.get("step_key"),
            "state": step.get("state"),
            "error_code": step.get("error_code"),
        }
        for step in (target.get("steps") or [])
    ]
    entries = baseline.get("grade_entries") or []
    for step in target.get("steps") or []:
        if step.get("state") != "applied":
            continue
        try:
            entry = entries[int(str(step.get("step_key") or "").split(":", 1)[1])]
        except (IndexError, TypeError, ValueError):
            continue
        count_key = action_counts.get(entry.get("action"))
        if count_key:
            counts[count_key] += 1
    receipt_id = None
    for receipt in receipts.list_receipts():
        if receipt.get("subject_id") == operation_key:
            receipt_id = receipt.get("receipt_id")
            break
    return {
        "ok": bool(result.get("ok")),
        "operation_id": operation_key,
        "status": result.get("status"),
        "counts": counts,
        "warnings": copy.deepcopy(baseline.get("warnings") or []),
        "bridge_assignment_id": target.get("returned_object_id"),
        "bridge_url": target.get("returned_object_url"),
        "steps": step_rows,
        "receipt_id": receipt_id,
    }


__all__ = [
    "list_sis_grade_bridges",
    "reconcile_sis_grade_bridges",
    "preview_sis_grade_bridge_reconciliation",
    "preview_sis_grade_bridge",
    "apply_sis_grade_bridge",
]
