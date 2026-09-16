"""Assistant-facing SIS grade-bridge use case.

This is the shared non-HTTP boundary used by MCP.  Live Canvas behavior stays
inside the ledger adapter; this module creates one frozen operation and shapes
only aggregate, student-free results.
"""

from __future__ import annotations

import copy

from api.operation_ledger import batches, executor, models, operations, receipts, registry
from api.operation_ledger.adapters.sis_grade_bridge import KIND
from api.platform_services import config


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


def preview_sis_grade_bridge(
    course_id: str, family_title: str, *, write_origin: str = "assistant",
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
    "preview_sis_grade_bridge",
    "apply_sis_grade_bridge",
]
