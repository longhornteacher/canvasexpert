"""Course-wide reviewed missing-work sweep: preview, apply, and undo.

Separate lane from ``api/grade_adjustment.py`` (grade-adjustment-contract.md):
this one fills a blank, long-overdue missing row with the policy's missing
value and Canvas's explicit missing status, course-wide, rather than
adjusting an existing numeric score. See
``docs/contracts/grading-policy-contract.md`` section 6 for the product
decisions this implements.
"""
from __future__ import annotations

import copy

from api.identity_vault_service import open_vault
from api.operation_ledger import batches, executor, models, operations, receipts, registry
from api.operation_ledger.adapters.missing_fill import KIND
from api.platform_services import config


def _vault():
    return open_vault()


def _current_course(course_id: str) -> bool:
    return str(course_id or "").strip() in {
        str(course.get("id") or "").strip()
        for course in config.active_courses()
    }


def _blocking_refusal(blocking: dict) -> dict:
    code = blocking.get("blocking_error") or "missing_sweep_preview_failed"
    result = {"ok": False, "code": code, "error": code, "blocking": True}
    if blocking.get("message"):
        result["error"] = blocking["message"]
    attention = blocking.get("attention")
    if attention:
        result["error"] = attention.get("reason", code)
        result["attention"] = attention
    if blocking.get("freshness"):
        result["freshness"] = blocking["freshness"]
    return result


def _operation_receipt(operation_id: str) -> tuple[dict | None, dict | None]:
    for item in receipts.list_receipts():
        if item.get("subject_id") == operation_id and item.get("kind") == KIND:
            return item, receipts.get_receipt(item["receipt_id"])
    return None, None


def _receipt_entries(operation_id: str) -> list[dict]:
    _summary_row, receipt = _operation_receipt(operation_id)
    if not receipt:
        return []
    output = []
    for target in receipt.get("targets") or []:
        for row in target.get("failed_items") or []:
            if isinstance(row, dict) and row.get("user_id") is not None:
                output.append(copy.deepcopy(row))
    return output


def _completed_sweep(operation_id: str, course_id: str) -> dict | None:
    operation = operations.get_operation(operation_id)
    if (not operation or operation.get("kind") != KIND
            or operation.get("status") != "applied"):
        return None
    payload = operation.get("normalized_payload") or {}
    if str(payload.get("course_id")) != str(course_id):
        return None
    if payload.get("mode") != "sweep":
        # Only a completed sweep (not an undo of one) can itself be undone.
        return None
    summary_row, _receipt = _operation_receipt(operation_id)
    return operation if summary_row and summary_row.get("status") == "applied" else None


def _summary(entries: list[dict], skipped: dict) -> dict:
    by_assignment: dict[str, dict] = {}
    for entry in entries:
        row = by_assignment.setdefault(entry["assignment_id"], {
            "assignment_id": entry["assignment_id"],
            "title": entry["assignment_title"],
            "due_at": entry.get("cached_due_date"),
            "points_possible": entry.get("points_possible"),
            "missing_value": entry.get("missing_value"),
            "row_count": 0,
            "pseudonyms": [],
        })
        row["row_count"] += 1
        row["pseudonyms"].append(entry["pseudonym"])
    assignments = sorted(by_assignment.values(), key=lambda row: row["title"])
    for row in assignments:
        row["pseudonyms"].sort()
    return {
        "assignments": assignments,
        "row_count": len(entries),
        "skipped": dict(skipped),
    }


def preview_missing_sweep(course_id: str, revert_operation_id: str = "") -> dict:
    course_key = str(course_id or "").strip()
    if not course_key:
        return {"ok": False, "code": "invalid_request",
                "error": "course_id is required"}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}
    revert_key = str(revert_operation_id or "").strip()
    try:
        adapter = registry.get_adapter(KIND)
        if revert_key:
            original = _completed_sweep(revert_key, course_key)
            if original is None:
                return {"ok": False, "code": "invalid_request",
                        "error": ("revert_operation_id is not a completed missing "
                                 "sweep for this course")}
            entries, skipped = adapter.build_revert_entries(
                course_key, _receipt_entries(revert_key))
            mode = "undo"
        else:
            entries, skipped, blocking = adapter.discover(course_key)
            if blocking:
                return _blocking_refusal(blocking)
            mode = "sweep"

        vault = _vault()
        with vault.transaction():
            for entry in entries:
                entry["pseudonym"] = vault.get_or_assign(str(entry["user_id"]))

        if not entries:
            return {"ok": False, "code": "no_changes",
                    "error": "No missing rows are eligible for the sweep."}

        payload = {"course_id": course_key, "mode": mode,
                  "entries": copy.deepcopy(entries)}
        if mode == "undo":
            payload["revert_operation_id"] = revert_key
        payload["_review"] = _summary(entries, skipped)

        target = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        baseline = {"discovered_at": models.now_iso()}
        target_record = models.new_target(
            target_key=target["target_key"], idempotency_key=target["idempotency_key"],
            course_id=course_key, baseline=baseline,
            steps=adapter.initial_steps(payload, baseline),
        )
        operation_id = models.new_operation_id()
        operation = models.new_operation(
            operation_id=operation_id, kind=KIND,
            source_ref={"type": "missing_sweep", "mode": mode},
            source_digest=adapter.source_digest(payload),
            normalized_payload=payload, targets=[target_record],
        )
        operations.create_operation(operation)
        frozen = adapter.freeze_review(payload, target_record, baseline)
        batch = batches.freeze_batch([operation_id], {operation_id: [frozen]})
        operations.set_operation_review(operation_id, batch)
    except ValueError as exc:
        return {"ok": False, "code": "invalid_request", "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "missing sweep preview could not be prepared"}
    return {
        "ok": True,
        "operation_id": operation_id,
        "batch_id": batch["batch_id"],
        "review_digest": batch["review_digest"],
        "preview": frozen,
    }


def _apply_projection(operation_id: str, result: dict, fallback: dict) -> dict:
    stored = operations.get_operation(operation_id) or fallback
    target = (stored.get("targets") or [{}])[0]
    entries = [row for row in (target.get("failed_items") or [])
               if isinstance(row, dict)]
    pseudo_by_user = {}
    try:
        with _vault().transaction() as vault:
            pseudo_by_user = {
                str(row.get("user_id")): vault.get_or_assign(str(row.get("user_id")))
                for row in entries if row.get("user_id") is not None
            }
    except Exception:
        pseudo_by_user = {}
    counts = {"filled": 0, "skipped_changed": 0, "failed": 0, "unverified": 0}
    skipped = []
    for row in entries:
        outcome = str(row.get("outcome") or "")
        if outcome == "done":
            counts["filled"] += 1
        elif outcome in {"changed_since_preview", "changed_since_sweep"}:
            counts["skipped_changed"] += 1
            if pseudo_by_user.get(str(row.get("user_id"))):
                skipped.append(pseudo_by_user[str(row["user_id"])])
        elif outcome == "missing_fill_unverified":
            counts["unverified"] += 1
        elif outcome:
            counts["failed"] += 1
    receipt_row, _receipt = _operation_receipt(operation_id)
    return {
        "ok": bool(result.get("ok")),
        "operation_id": operation_id,
        "status": result.get("status"),
        "counts": counts,
        "skipped_changed": sorted(set(skipped)),
        "receipt_id": receipt_row.get("receipt_id") if receipt_row else None,
    }


def apply_missing_sweep(operation_id: str, batch_id: str, review_digest: str) -> dict:
    operation_key = str(operation_id or "").strip()
    if (not operation_key or not str(batch_id or "").strip()
            or not str(review_digest or "").strip()):
        return {"ok": False,
                "error": "operation_id, batch_id, and review_digest are required"}
    operation = operations.get_operation(operation_key)
    if operation is None or operation.get("kind") != KIND:
        return {"ok": False, "error": "missing sweep operation was not found"}
    if operation.get("status") == "applied":
        return _apply_projection(operation_key,
                                 {"ok": True, "status": "already_applied"}, operation)
    try:
        result = executor.apply_operation(operation_key, str(batch_id), str(review_digest))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "missing sweep apply could not complete"}
    return _apply_projection(operation_key, result, operation)


__all__ = ["KIND", "preview_missing_sweep", "apply_missing_sweep"]
