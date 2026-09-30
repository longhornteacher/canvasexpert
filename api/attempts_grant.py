"""Reviewed extra attempts and reopened windows for one course assignment.

The conversation decides who gets another try; this module never infers a list
from scores. ``preview_attempts_grant`` validates the grant, classifies the
assignment from one live read, resolves pseudonyms to Canvas user ids (only
here, never in an MCP payload), freezes each student's live ``before`` value,
and stores one reviewed Operation Ledger operation. ``apply_attempts_grant``
executes exactly that review. Canvas facts:
``docs/reference/gradebook-module-map.md``, "Verified Canvas facts: attempts
and reopening".
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from api.identity_vault_service import open_vault
from api.mcp_server import pseudonym
from api.operation_ledger import batches, executor, models, operations, receipts, registry
from api.operation_ledger.adapters import attempts_grant as adapter_module
from api.operation_ledger.adapters.attempts_grant import (
    GRANTED, KIND, SKIPPED, UNLIMITED,
)
from api.platform_services import config


_GRANT_KEYS = {"students", "extra_attempts", "reopen"}
_BLOCKING_MESSAGES = {
    "unsupported_assignment": (
        "Attempts can be granted on online upload, URL, text-entry, Classic Quiz, "
        "and New Quiz assignments only."),
    "assignment_live_unavailable": "Canvas could not be read for this assignment right now.",
    "attempts_unreadable": "This assignment's attempts setting could not be read.",
    "attempts_unavailable": "The students' current extra attempts could not be read.",
    "overrides_unavailable": "This assignment's existing overrides could not be read.",
}
_REASON_MESSAGES = {
    "extension_not_applied": (
        "Canvas did not apply the extension; the student may not be able to see this "
        "assignment (for example, it is unpublished)."),
}
_ATTENTION = {
    "window_locked": (
        "The window is locked for the selected students, so extra attempts cannot be "
        "used until it is reopened."),
    "new_quiz_unverified": (
        "New Quiz per-student attempts are unverified on live Canvas, and Canvas may "
        "replace a prior accommodation value."),
    "grades_unchanged": "Current scores stay until the teacher regrades a new attempt.",
}


class _Refused(Exception):
    def __init__(self, code: str, error: str, **extra):
        super().__init__(error)
        self.result = {"ok": False, "code": code, "error": error, **extra}


def _invalid(error: str) -> _Refused:
    return _Refused("invalid_grant", error)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _vault():
    return open_vault()


def _current_course(course_id: str) -> bool:
    return str(course_id or "").strip() in {
        str(course.get("id") or "").strip() for course in config.active_courses()
    }


def _aware(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _canonical_reopen(reopen) -> dict:
    if not isinstance(reopen, dict) or set(reopen) != {"due_at", "lock_at"}:
        raise _invalid("reopen needs exactly due_at and lock_at.")
    moments = {key: _aware(reopen[key]) for key in ("due_at", "lock_at")}
    if any(moment is None for moment in moments.values()):
        raise _invalid("reopen due_at and lock_at must be ISO 8601 times with an offset.")
    if any(moment <= _now() for moment in moments.values()):
        raise _invalid("reopen due_at and lock_at must both be in the future.")
    if moments["lock_at"] < moments["due_at"]:
        raise _invalid("reopen lock_at must not be before due_at.")
    return {key: moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for key, moment in moments.items()}


def _canonical_grant(grant) -> dict:
    if not isinstance(grant, dict):
        raise _invalid("The grant must be an object with students plus extra_attempts, "
                       "reopen, or both.")
    unknown = sorted(set(grant) - _GRANT_KEYS)
    if unknown:
        raise _invalid(f"The grant field '{unknown[0]}' is not supported.")
    students = grant.get("students")
    if isinstance(students, list) and students:
        if any(not isinstance(item, str) or not item.strip() for item in students):
            raise _invalid("Every entry in students must be a pseudonym string.")
        students = [item.strip() for item in students]
        if len(set(students)) != len(students):
            raise _invalid("The students list must not repeat a pseudonym.")
    elif students != "all":
        raise _invalid('students must be "all" or a non-empty list of pseudonyms.')
    canonical = {"students": students}
    attempts = grant.get("extra_attempts")
    if attempts is not None:
        if attempts == "unlimited":
            if students != "all":
                raise _invalid("Canvas has no per-student unlimited attempts; "
                               'use students "all" or a number from 1 to 100.')
        elif not (isinstance(attempts, int) and not isinstance(attempts, bool)
                  and 1 <= attempts <= 100):
            raise _invalid('extra_attempts must be a whole number from 1 to 100 or "unlimited".')
        canonical["extra_attempts"] = attempts
    if grant.get("reopen") is not None:
        canonical["reopen"] = _canonical_reopen(grant["reopen"])
    if "extra_attempts" not in canonical and "reopen" not in canonical:
        raise _invalid("The grant needs extra_attempts, reopen, or both.")
    return canonical


def _blocking_refusal(baseline: dict) -> dict:
    code = baseline.get("blocking_error")
    if code == "freshness_attention":
        return {
            "ok": False,
            "error": (baseline.get("attention") or {}).get(
                "reason",
                "This local Canvas snapshot is outside the configured freshness window."),
            "freshness": baseline.get("freshness") or {},
            "attention": baseline.get("attention") or {"action": "ask_teacher_confirmation"},
        }
    return {"ok": False, "code": code or "attempts_grant_preview_failed",
            "error": _BLOCKING_MESSAGES.get(code, "The attempts grant could not be prepared."),
            "blocking": True}


def _resolve_students(canonical: dict, baseline: dict) -> list[dict]:
    roster, on_assignment = baseline["roster"], set(baseline["on_assignment_ids"])
    vault = _vault()
    entries = []
    with vault.transaction():
        for requested in canonical["students"]:
            user_id = pseudonym.resolve_pseudonym(vault, roster, requested)
            if not user_id or str(user_id) not in on_assignment:
                raise _invalid(f"Pseudonym '{requested}' is not a current student "
                               "on this assignment.")
            entries.append({"user_id": str(user_id),
                            "pseudonym": vault.get_or_assign(str(user_id))})
    entries.sort(key=lambda row: row["pseudonym"])
    return entries


def _is_locked(lock_at) -> bool:
    moment = adapter_module.instant(lock_at)
    return moment is not None and moment <= _now()


def _locked_for_selection(facts: dict, baseline: dict, entries: list[dict]) -> bool:
    if not entries:
        return _is_locked(facts["lock_at"])
    own_lock = {}
    for override in baseline.get("overrides") or []:
        if override.get("lock_at"):
            for user_id in override.get("student_ids") or []:
                own_lock[str(user_id)] = override["lock_at"]
    return any(_is_locked(own_lock.get(entry["user_id"], facts["lock_at"]))
               for entry in entries)


def _whole_class_plan(canonical: dict, facts: dict) -> dict:
    whole = {}
    if "extra_attempts" in canonical:
        before = facts["allowed_attempts"]
        if before == UNLIMITED:
            raise _Refused("already_unlimited", "Attempts are already unlimited on this "
                           "assignment, so extra attempts would change nothing.")
        extra = canonical["extra_attempts"]
        whole["attempts"] = {"before": before,
                             "after": UNLIMITED if extra == "unlimited" else before + extra}
    if "reopen" in canonical:
        current = adapter_module.window_of(facts)
        if not adapter_module.same_window(current, canonical["reopen"]):
            whole["dates"] = {"before": current, "after": canonical["reopen"]}
    if not whole:
        raise _Refused("no_changes", "The assignment already has these dates, "
                       "so nothing would change.")
    return whole


def _student_plan(canonical: dict, facts: dict, baseline: dict,
                  entries: list[dict], course_id: str, assignment_id: str) -> None:
    extra = canonical.get("extra_attempts")
    if extra is not None and facts["allowed_attempts"] == UNLIMITED:
        raise _Refused("already_unlimited", "Attempts are already unlimited on this "
                       "assignment, so extra attempts would change nothing; "
                       "send a reopen alone.")
    if "reopen" in canonical:
        chosen = {entry["user_id"]: entry["pseudonym"] for entry in entries}
        blocked = sorted({
            chosen[str(user_id)]
            for override in baseline.get("overrides") or []
            for user_id in override.get("student_ids") or []
            if str(user_id) in chosen
        })
        if blocked:
            raise _Refused(
                "student_has_override",
                "These students already have an assignment override, so a reopen "
                f"would conflict: {', '.join(blocked)}.",
                pseudonyms=blocked)
    current = {}
    if extra is not None and facts["kind"] != "new_quiz":
        current, code = adapter_module.read_extra_attempts(
            facts["kind"], course_id, assignment_id, facts["quiz_id"],
            [entry["user_id"] for entry in entries])
        if code:
            raise _Refused(code, _BLOCKING_MESSAGES[code], blocking=True)
    for entry in entries:
        if extra is None:
            entry.update(before_extra=None, after_extra=None)
        elif facts["kind"] == "new_quiz":
            entry.update(before_extra=None, after_extra=extra)
        else:
            before = current[entry["user_id"]]
            entry.update(before_extra=before, after_extra=before + extra)


def _attention(canonical: dict, facts: dict, baseline: dict,
               entries: list[dict]) -> list[dict]:
    codes = []
    if ("extra_attempts" in canonical and "reopen" not in canonical
            and _locked_for_selection(facts, baseline, entries)):
        codes.append("window_locked")
    if facts["kind"] == "new_quiz" and entries and "extra_attempts" in canonical:
        codes.append("new_quiz_unverified")
    codes.append("grades_unchanged")
    return [{"code": code, "message": _ATTENTION[code]} for code in codes]


def _review(canonical: dict, facts: dict, whole: dict, entries: list[dict],
            attention: list[dict]) -> dict:
    review = {"assignment_title": facts["title"], "kind": facts["kind"],
              "scope": "students" if entries else "all", "attention": attention}
    if not entries:
        review["whole_class"] = {
            item: ({"before": adapter_module.total_label(change["before"]),
                    "after": adapter_module.total_label(change["after"])}
                   if item == "attempts" else copy.deepcopy(change))
            for item, change in whole.items()
        }
        return review
    review["students"] = [
        {"pseudonym": entry["pseudonym"], "before_extra": entry["before_extra"],
         "after_extra": entry["after_extra"]} for entry in entries
    ]
    if "extra_attempts" in canonical:
        review["extra_attempts"] = canonical["extra_attempts"]
    if "reopen" in canonical:
        review["reopen"] = copy.deepcopy(canonical["reopen"])
    return review


def preview_attempts_grant(course_id: str, assignment_id: str, grant: dict) -> dict:
    course_key = str(course_id or "").strip()
    assignment_key = str(assignment_id or "").strip()
    if not course_key or not assignment_key:
        return {"ok": False, "code": "invalid_grant",
                "error": "course_id and assignment_id are required."}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}
    try:
        canonical = _canonical_grant(grant)
        adapter = registry.get_adapter(KIND)
        payload = adapter.build_payload({
            "course_id": course_key, "assignment_id": assignment_key,
            "grant": canonical,
        })
        provisional = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        baseline = adapter.capture_baseline(payload, provisional)
        if baseline.get("blocking_error"):
            return _blocking_refusal(baseline)
        facts = baseline["assignment"]
        if canonical["students"] == "all":
            entries, whole = [], _whole_class_plan(canonical, facts)
        else:
            entries, whole = _resolve_students(canonical, baseline), {}
            _student_plan(canonical, facts, baseline, entries,
                          course_key, assignment_key)
        attention = _attention(canonical, facts, baseline, entries)
        payload.update({
            "kind": facts["kind"], "quiz_id": facts["quiz_id"],
            "assignment_name": facts["title"],
            "scope": "students" if entries else "all",
            "whole_class": whole, "entries": entries,
            "reopen": copy.deepcopy(canonical.get("reopen")) if entries else None,
            "_review": _review(canonical, facts, whole, entries, attention),
        })
        stored_baseline = {"course_id": course_key, "assignment_id": assignment_key,
                           "assignment": copy.deepcopy(facts)}
        for key in ("synced_at", "freshness"):
            if baseline.get(key) is not None:
                stored_baseline[key] = copy.deepcopy(baseline[key])
        target = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        target_record = models.new_target(
            target_key=target["target_key"], idempotency_key=target["idempotency_key"],
            course_id=course_key, baseline=stored_baseline,
            steps=adapter.initial_steps(payload, stored_baseline),
        )
        operation_id = models.new_operation_id()
        operation = models.new_operation(
            operation_id=operation_id, kind=KIND,
            source_ref={"type": "attempts_grant"},
            source_digest=adapter.source_digest(payload),
            normalized_payload=payload, targets=[target_record],
        )
        operations.create_operation(operation)
        frozen = adapter.freeze_review(payload, target_record, stored_baseline)
        batch = batches.freeze_batch([operation_id], {operation_id: [frozen]})
        operations.set_operation_review(operation_id, batch)
    except _Refused as refusal:
        return refusal.result
    except Exception:
        return {"ok": False, "error": "attempts grant preview could not be prepared"}
    return {
        "ok": True,
        "operation_id": operation_id,
        "batch_id": batch["batch_id"],
        "review_digest": batch["review_digest"],
        "preview": frozen,
    }


def _operation_receipt(operation_id: str) -> dict | None:
    return next((item for item in receipts.list_receipts()
                 if item.get("subject_id") == operation_id
                 and item.get("kind") == KIND), None)


def _bucket(outcome: str) -> str:
    if outcome in GRANTED:
        return "granted"
    return "skipped" if outcome in SKIPPED else "failed"


def _apply_projection(operation_id: str, result: dict, fallback: dict) -> dict:
    """Pseudonym-only result; Canvas user ids and override ids never appear."""
    stored = operations.get_operation(operation_id) or fallback
    payload = stored.get("normalized_payload") or {}
    target = (stored.get("targets") or [{}])[0]
    rows = {row.get("step"): row for row in target.get("failed_items") or []
            if isinstance(row, dict)}
    states = {step.get("step_key"): step.get("state")
              for step in target.get("steps") or []}
    counts = {"granted": 0, "skipped": 0, "failed": 0}
    outcomes = []
    for key in adapter_module.step_keys(payload):
        row = rows.get(key) or adapter_module.receipt_row(
            payload, key, "done" if states.get(key) == "applied" else "not_reached")
        bucket = _bucket(str(row.get("outcome") or ""))
        counts[bucket] += 1
        shown = {name: value for name, value in row.items()
                 if name not in {"step", "outcome"}}
        shown["outcome"] = bucket
        if bucket != "granted":
            shown["reason"] = row.get("outcome")
            if shown["reason"] in _REASON_MESSAGES:
                shown["message"] = _REASON_MESSAGES[shown["reason"]]
        outcomes.append(shown)
    summary = _operation_receipt(operation_id)
    projection = {
        "ok": bool(result.get("ok")),
        "operation_id": operation_id,
        "status": result.get("status"),
        "counts": counts,
        "outcomes": outcomes,
        "receipt_id": summary.get("receipt_id") if summary else None,
    }
    if target.get("error_code") and stored.get("status") != "applied":
        projection["attention"] = {
            "code": target["error_code"],
            "next": ("Nothing unsafe was written; prepare a new preview."
                     if target["error_code"] == adapter_module.DRIFT
                     else "Check Canvas for the affected rows before any retry."),
        }
    return projection


def result_projection(operation_id: str, result: dict, fallback: dict) -> dict:
    """The pseudonym-only result shared by apply and resume_operation."""
    return _apply_projection(operation_id, result, fallback)


def apply_attempts_grant(operation_id: str, batch_id: str, review_digest: str) -> dict:
    operation_key = str(operation_id or "").strip()
    if (not operation_key or not str(batch_id or "").strip()
            or not str(review_digest or "").strip()):
        return {"ok": False,
                "error": "operation_id, batch_id, and review_digest are required"}
    operation = operations.get_operation(operation_key)
    if operation is None or operation.get("kind") != KIND:
        return {"ok": False, "error": "attempts grant operation was not found"}
    if operation.get("status") == "applied":
        return _apply_projection(operation_key,
                                 {"ok": True, "status": "already_applied"}, operation)
    try:
        result = executor.apply_operation(operation_key, str(batch_id), str(review_digest))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "attempts grant apply could not complete"}
    return _apply_projection(operation_key, result, operation)


__all__ = ["KIND", "preview_attempts_grant", "apply_attempts_grant", "result_projection"]
