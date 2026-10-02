"""Mirror-first reviewed grade adjustments for one course assignment."""
from __future__ import annotations

import copy
import math

from api import freshness_policy
from api import score_curves, score_ledger
from api.identity_vault_service import open_vault
from api.mcp_server import pseudonym
from api.operation_ledger import batches, executor, models, operations, receipts, registry
from api.operation_ledger.adapters.grade_adjustment import (
    KIND, GradeAdjustmentAdapter, _is_number, _number, _numbers_equal,
)
from api.platform_services import config
from api.powergrader import session_store


_RULES = {"flat_bump", "target_average", "proportional", "floor_cap"}
_KINDS = ("rule", "explicit", "revert", "revert_rule")


def _current_course(course_id: str) -> bool:
    return str(course_id or "").strip() in {
        str(course.get("id") or "").strip()
        for course in config.active_courses()
    }


def _vault():
    return open_vault()


def _freshness_refusal(baseline: dict) -> dict:
    if baseline.get("blocking_error") == "freshness_attention":
        return {
            "ok": False,
            "error": (baseline.get("attention") or {}).get(
                "reason",
                "This local Canvas snapshot is outside the configured freshness window.",
            ),
            "freshness": baseline.get("freshness") or {},
            "attention": baseline.get("attention") or {
                "action": "ask_teacher_confirmation",
            },
        }
    code = baseline.get("blocking_error") or "grade_adjustment_preview_failed"
    result = {"ok": False, "code": code, "error": code, "blocking": True}
    attention = baseline.get("attention")
    if attention:
        result["error"] = attention.get("reason", code)
        result["attention"] = attention
    return result


def _as_float(value, field: str):
    if not _is_number(value) or not math.isfinite(float(value)):
        raise ValueError(f"invalid_adjustment: field '{field}' must be a finite number")
    return float(value)


def _canonical_adjustment(adjustment: dict) -> dict:
    if not isinstance(adjustment, dict):
        raise ValueError("invalid_adjustment: field 'adjustment' must be an object")
    kind = adjustment.get("kind")
    if kind not in _KINDS:
        raise ValueError("invalid_adjustment: field 'kind' is invalid; use one of: "
                         + " | ".join(_KINDS))
    if kind == "revert":
        operation_id = str(adjustment.get("operation_id") or "").strip()
        if not operation_id:
            raise ValueError("invalid_adjustment: field 'operation_id' is required")
        return {"kind": "revert", "operation_id": operation_id}
    if kind == "revert_rule":
        rule_id = str(adjustment.get("rule_id") or "").strip()
        if not rule_id:
            raise ValueError("invalid_adjustment: field 'rule_id' is required")
        return {"kind": "revert_rule", "rule_id": rule_id}
    if kind == "explicit":
        rows = adjustment.get("entries", adjustment.get("rows"))
        if not isinstance(rows, list):
            raise ValueError("invalid_adjustment: field 'entries' must be a list")
        return {
            "kind": "explicit",
            "entries": copy.deepcopy(rows),
            "allow_above_points": bool(adjustment.get("allow_above_points", False)),
        }

    if adjustment.get("rule_id"):
        basis = adjustment.get("baseline_basis")
        if basis not in {"raw", "entered"}:
            raise ValueError("invalid_adjustment: baseline_basis must be raw or entered")
        return {"kind": "rule", "rule_id": str(adjustment["rule_id"]),
                "baseline_basis": basis}
    model = (adjustment.get("model") or adjustment.get("rule")
             or adjustment.get("curve_type"))
    if model not in _RULES:
        raise ValueError("invalid_adjustment: field 'model' is invalid; use one of: "
                         + " | ".join(sorted(_RULES)))
    settings = adjustment.get("settings")
    if settings is None:
        settings = {
            key: value for key, value in adjustment.items()
            if key not in {"kind", "model", "rule", "curve_type"}
        }
    if not isinstance(settings, dict):
        raise ValueError("invalid_adjustment: field 'settings' must be an object")
    exclusions = adjustment.get("exclude_pseudonyms", [])
    if not isinstance(exclusions, list):
        raise ValueError("invalid_adjustment: field 'exclude_pseudonyms' must be a list")
    if any(not isinstance(value, str) or not value.strip() for value in exclusions):
        raise ValueError("invalid_adjustment: exclusions must be non-empty pseudonym strings")
    if len(exclusions) != len(set(exclusions)):
        raise ValueError("invalid_adjustment: exclude_pseudonyms contains a duplicate")
    normalized = {"kind": "rule", "model": model, "settings": copy.deepcopy(settings)}
    normalized["exclude_pseudonyms"] = [value.strip() for value in exclusions]
    for key in ("do_no_harm", "cap"):
        if key in adjustment and key not in normalized["settings"]:
            normalized["settings"][key] = adjustment[key]
    normalized["settings"].setdefault("do_no_harm", True)
    if "cap" not in normalized["settings"]:
        normalized["settings"]["cap"] = None
    required = {
        "flat_bump": "bump",
        "target_average": "target_avg_pct",
        "proportional": "target_avg_pct",
        "floor_cap": "floor",
    }[model]
    if required not in normalized["settings"]:
        raise ValueError(f"invalid_adjustment: field '{required}' is required for {model}")
    _as_float(normalized["settings"][required], required)
    if normalized["settings"].get("cap") not in (None, ""):
        _as_float(normalized["settings"]["cap"], "cap")
    return normalized


def _rule_score(score: float, model: str, settings: dict,
                points_possible: float, current_average: float,
                max_score: float, total_count: int) -> tuple[float, bool]:
    cap = (points_possible if settings.get("cap") in (None, "")
           else _as_float(settings["cap"], "cap"))
    do_no_harm = bool(settings.get("do_no_harm", True))
    if model == "flat_bump":
        candidate = score + _as_float(settings["bump"], "bump")
    elif model == "target_average":
        target_pct = _as_float(settings["target_avg_pct"], "target_avg_pct")
        candidate = score + (points_possible * target_pct / 100 - current_average)
    elif model == "proportional":
        target_pct = _as_float(settings["target_avg_pct"], "target_avg_pct")
        total_lift = (points_possible * target_pct / 100 - current_average) * total_count
        weights = max_score - score + 1
        total_weight = sum(
            max_score - value + 1 for value in settings.get("_scores", [])
        ) or 1
        candidate = score + weights / total_weight * total_lift
    else:
        floor = _as_float(settings["floor"], "floor")
        candidate = max(min(score, cap), floor)
    candidate = min(candidate, cap)
    if do_no_harm:
        candidate = max(candidate, score)
    candidate = round(candidate, 2)
    return candidate, candidate >= cap and score < cap


def _summary(baseline: dict, entries: list[dict], *, extra_skipped=None,
             excluded_ids=None) -> dict:
    excluded_ids = {str(value) for value in excluded_ids or set()}
    eligible = [row for row in baseline.get("entries") or []
                if row.get("eligible") and str(row.get("user_id")) not in excluded_ids]
    before_scores = [float(row["before"]) for row in eligible]
    changed = [row for row in entries if row.get("changed")]
    after_by_user = {str(row["user_id"]): row.get("after") for row in changed}
    after_scores = [after_by_user.get(str(row["user_id"]), row["before"])
                    for row in eligible]
    skipped = {}
    for row in baseline.get("entries") or []:
        if row.get("skip_reason"):
            skipped[row["skip_reason"]] = skipped.get(row["skip_reason"], 0) + 1
    for key, value in (extra_skipped or {}).items():
        skipped[key] = skipped.get(key, 0) + int(value)
    capped = sum(1 for row in changed if row.get("capped"))
    return {
        "eligible": len(eligible),
        "changed": len(changed),
        "unchanged": max(0, len(eligible) - len(changed)),
        "raised": sum(1 for row in changed if row["after"] > row["before"]),
        "lowered": sum(1 for row in changed if row["after"] < row["before"]),
        "capped": capped,
        "skipped": skipped,
        "class_average_before_pct": round(sum(before_scores) / len(before_scores)
                                           / float(baseline["assignment"]["points_possible"])
                                           * 100, 1) if before_scores else None,
        "class_average_after_pct": round(sum(float(value) for value in after_scores)
                                          / len(after_scores)
                                          / float(baseline["assignment"]["points_possible"])
                                          * 100, 1) if after_scores else None,
    }


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


def _completed_operation(operation_id: str) -> dict | None:
    operation = operations.get_operation(operation_id)
    if not operation or operation.get("kind") != KIND or operation.get("status") != "applied":
        return None
    summary_row, _receipt = _operation_receipt(operation_id)
    return operation if summary_row and summary_row.get("status") == "applied" else None


def _pseudonyms(vault, roster: list[dict]) -> dict[str, str]:
    return {
        str(student["id"]): vault.get_or_assign(str(student["id"]))
        for student in roster if student.get("id") is not None
    }


def _prepare_entries(baseline: dict, adjustment: dict, vault) -> tuple[list[dict], dict, set[str]]:
    roster = baseline.get("roster") or []
    pseudo_by_id = _pseudonyms(vault, roster)
    by_user = {str(row["user_id"]): row for row in baseline.get("entries") or []}
    eligible = [row for row in baseline.get("entries") or [] if row.get("eligible")]
    kind = adjustment["kind"]
    proposed = {}
    provenance_by_user = {}
    extra_skipped = {}
    excluded_ids = set()

    if kind == "rule" and adjustment.get("rule_id"):
        course_id = str(baseline.get("course_id") or "")
        rule_id = str(adjustment["rule_id"])
        rule_events = score_ledger.list_rule_events(course_id)
        rule = next((item for item in rule_events
                     if item.get("rule_id") == rule_id and item.get("action") == "create"), None)
        if not rule:
            raise ValueError("invalid_adjustment: rule_id was not found")
        formula = rule.get("formula") or {}
        events = score_ledger.list_events(course_id, str(baseline.get("assignment_id") or ""))
        for row in eligible:
            uid = str(row["user_id"])
            events = score_ledger.list_events(course_id, str(baseline.get("assignment_id") or ""))
            current = row.get("before")
            if adjustment["baseline_basis"] == "raw":
                evidence = [event for event in events if str(event.get("student_id")) == uid
                            and event.get("curve_rule_id") == rule_id
                            and event.get("action") == "verified"
                            and event.get("entered_score") is not None]
                latest = max(evidence, key=lambda item: str(item.get("timestamp") or ""), default=None)
                if (not latest or latest.get("raw_score") is None
                        or str(latest.get("attempt") or "") != str(row.get("attempt") or "")
                        or not _numbers_equal(latest.get("entered_score"), current)
                        or not _numbers_equal(latest.get("canvas_score"), row.get("canvas_score"))):
                    extra_skipped["raw_baseline_unlinked"] = extra_skipped.get("raw_baseline_unlinked", 0) + 1
                    continue
                base = float(latest["raw_score"])
                provenance_by_user[uid] = {"baseline_raw": base, "baseline_rule_id": rule_id,
                    "baseline_event_id": latest.get("event_id"), "attempt": latest.get("attempt")}
            else:
                base = float(current)
                provenance_by_user[uid] = {"baseline_entered": base, "baseline_basis": "entered",
                    "baseline_rule_id": rule_id, "attempt": row.get("attempt")}
            scored = score_curves.apply_formula(base, baseline["assignment"]["points_possible"], formula)
            proposed[uid] = (scored["entered_score"], False)
    elif kind == "rule":
        eligible_ids = {str(row.get("user_id")) for row in eligible}
        for requested in adjustment.get("exclude_pseudonyms", []):
            user_id = pseudonym.resolve_pseudonym(vault, roster, requested)
            if not user_id or str(user_id) not in eligible_ids:
                raise ValueError(
                    f"invalid_adjustment: pseudonym '{requested}' is not eligible for exclusion"
                )
            if str(user_id) in excluded_ids:
                raise ValueError("invalid_adjustment: exclude_pseudonyms resolves to a duplicate")
            excluded_ids.add(str(user_id))
        if excluded_ids:
            extra_skipped["excluded_by_teacher"] = len(excluded_ids)
        rule_rows = []
        for row in eligible:
            if str(row.get("user_id")) not in excluded_ids:
                rule_rows.append(row)
        scores = [float(row["before"]) for row in rule_rows]
        settings = copy.deepcopy(adjustment["settings"])
        settings["_scores"] = scores
        average = sum(scores) / len(scores) if scores else 0
        maximum = max(scores) if scores else 0
        for row in rule_rows:
            after, capped = _rule_score(
                float(row["before"]), adjustment["model"], settings,
                float(baseline["assignment"]["points_possible"]), average,
                maximum, len(scores),
            )
            proposed[str(row["user_id"])] = (after, capped)
    elif kind == "explicit":
        seen = set()
        points = float(baseline["assignment"]["points_possible"])
        for raw in adjustment["entries"]:
            if not isinstance(raw, dict):
                raise ValueError("invalid_adjustment: each explicit entry must be an object")
            requested = raw.get("pseudonym")
            user_id = pseudonym.resolve_pseudonym(vault, roster, requested)
            if not user_id or str(user_id) in seen or str(user_id) not in by_user:
                raise ValueError(f"invalid_adjustment: pseudonym '{requested}' is not eligible")
            seen.add(str(user_id))
            source = by_user[str(user_id)]
            if not source.get("eligible"):
                raise ValueError(f"invalid_adjustment: pseudonym '{requested}' is not eligible")
            has_new = "new_score" in raw
            has_delta = "delta" in raw
            if has_new == has_delta:
                raise ValueError(f"invalid_adjustment: pseudonym '{requested}' must name exactly one of 'new_score' or 'delta'")
            value = _as_float(raw.get("new_score") if has_new else raw.get("delta"),
                              "new_score" if has_new else "delta")
            after = value if has_new else float(source["before"]) + value
            if after < 0:
                raise ValueError(f"invalid_adjustment: pseudonym '{requested}' has a score below zero")
            if after > points and not adjustment.get("allow_above_points"):
                raise ValueError(f"invalid_adjustment: pseudonym '{requested}' exceeds points_possible")
            proposed[str(user_id)] = (_number(after), False)
    elif kind == "revert_rule":
        rule_id = adjustment["rule_id"]
        events = score_ledger.list_events(str(baseline.get("course_id") or ""),
                                          str(baseline.get("assignment_id") or ""))
        successful = [event for event in events if event.get("action") == "verified"
                      and event.get("curve_rule_id") == rule_id]
        reverted = {str(event.get("origin_event_id")) for event in events
                    if event.get("action") == "revert" and event.get("curve_rule_id") == rule_id}
        latest_by_key = {}
        for event in successful:
            key = (str(event.get("student_id")), str(event.get("attempt") or ""), rule_id)
            if str(event.get("event_id")) not in reverted:
                prior = latest_by_key.get(key)
                if prior is None or str(event.get("timestamp") or "") > str(prior.get("timestamp") or ""):
                    latest_by_key[key] = event
        by_user_current = {str(row.get("user_id")): row for row in baseline.get("entries") or []}
        for (uid, attempt, _rid), event in latest_by_key.items():
            row = by_user_current.get(uid)
            if not row or not row.get("eligible"):
                extra_skipped["student_unavailable"] = extra_skipped.get("student_unavailable", 0) + 1
                continue
            try:
                newer = int(row.get("attempt")) > int(attempt)
            except (TypeError, ValueError):
                newer = str(row.get("attempt") or "") != attempt
            if newer:
                extra_skipped["newer_attempt"] = extra_skipped.get("newer_attempt", 0) + 1
            elif event.get("raw_score") is None:
                extra_skipped["raw_unknown"] = extra_skipped.get("raw_unknown", 0) + 1
            elif (str(row.get("attempt") or "") != attempt
                  or not _numbers_equal(row.get("before"), event.get("entered_score"))
                  or not _numbers_equal(row.get("canvas_score"), event.get("canvas_score"))):
                extra_skipped["changed_since_curve"] = extra_skipped.get("changed_since_curve", 0) + 1
            else:
                proposed[uid] = (_number(event.get("raw_score")), False)
                provenance_by_user[uid] = {"baseline_raw": event.get("raw_score"),
                    "baseline_rule_id": rule_id, "baseline_event_id": event.get("event_id"),
                    "origin_event_id": event.get("event_id"), "attempt": event.get("attempt")}
    else:
        original_id = adjustment["operation_id"]
        original = _completed_operation(original_id)
        if original is None:
            raise ValueError("invalid_adjustment: field 'operation_id' is not a completed grade adjustment")
        original_payload = original.get("normalized_payload") or {}
        if (str(original_payload.get("course_id")) != str(baseline.get("course_id"))
                or str(original_payload.get("assignment_id")) != str(baseline.get("assignment_id"))):
            raise ValueError("invalid_adjustment: field 'operation_id' targets a different assignment")
        for item in _receipt_entries(original_id):
            if item.get("outcome") != "done":
                continue
            row = by_user.get(str(item.get("user_id")))
            if not row or not row.get("eligible"):
                extra_skipped["changed_since_adjustment"] = extra_skipped.get("changed_since_adjustment", 0) + 1
                continue
            if not _numbers_equal(row.get("before"), item.get("after")) or row.get("before_excused"):
                extra_skipped["changed_since_adjustment"] = extra_skipped.get("changed_since_adjustment", 0) + 1
                continue
            proposed[str(item["user_id"])] = (_number(item.get("before")), False)

    entries = []
    for user_id, (after, capped) in proposed.items():
        before = by_user[user_id]["before"]
        entries.append({
            "user_id": user_id,
            "pseudonym": pseudo_by_id.get(user_id, ""),
            "before": _number(before),
            "before_excused": bool(by_user[user_id].get("before_excused")),
            "after": _number(after),
            "changed": not _numbers_equal(before, after),
            "capped": bool(capped),
            **({"attempt": by_user[user_id].get("attempt")} if by_user[user_id].get("attempt") is not None else {}),
            **provenance_by_user.get(user_id, {}),
        })
    entries.sort(key=lambda row: row.get("pseudonym") or row["user_id"])
    return entries, extra_skipped, excluded_ids


def _report_operations() -> list[dict]:
    operations_by_id = {
        operation.get("operation_id"): operation
        for operation in operations.list_operations()
        if operation.get("kind") == KIND and operation.get("status") == "applied"
    }
    completed = []
    for operation_id, operation in operations_by_id.items():
        summary_row, receipt = _operation_receipt(operation_id)
        if not summary_row or summary_row.get("status") != "applied" or not receipt:
            continue
        completed.append((operation, summary_row, receipt))
    reverted = {
        str((operation.get("normalized_payload") or {}).get("adjustment", {}).get("operation_id"))
        for operation, _summary_row, _receipt in completed
        if ((operation.get("normalized_payload") or {}).get("adjustment", {}).get("kind") == "revert")
    }
    return [
        {"operation": operation, "summary": summary_row, "receipt": receipt}
        for operation, summary_row, receipt in completed
        if ((operation.get("normalized_payload") or {}).get("adjustment", {}).get("kind")
            != "revert")
        if operation.get("operation_id") not in reverted
    ]


def active_adjustment_assignments() -> set[tuple[str, str]]:
    """Return completed, non-reverted assignment coordinates for routine guards."""
    return {
        (str(item["operation"].get("normalized_payload", {}).get("course_id") or ""),
         str(item["operation"].get("normalized_payload", {}).get("assignment_id") or ""))
        for item in _report_operations()
    }


def report_adjustments() -> list[dict]:
    """Private student-report projection of active completed adjustments."""
    rows = []
    for item in _report_operations():
        operation = item["operation"]
        payload = operation.get("normalized_payload") or {}
        receipt = item["receipt"]
        entries = []
        for target in receipt.get("targets") or []:
            entries.extend(
                row for row in (target.get("failed_items") or [])
                if isinstance(row, dict) and row.get("outcome") == "done"
            )
        rows.append({
            "course_id": str(payload.get("course_id") or ""),
            "assignment_id": str(payload.get("assignment_id") or ""),
            "assignment_name": str(payload.get("assignment_name")
                                    or payload.get("assignment_id") or ""),
            "applied_at": receipt.get("completed_at") or "",
            "students": entries,
        })
    return rows


def _apply_projection(operation_id: str, result: dict, fallback: dict) -> dict:
    stored = operations.get_operation(operation_id) or fallback
    target = (stored.get("targets") or [{}])[0]
    entries = [
        row for row in (target.get("failed_items") or [])
        if isinstance(row, dict)
    ]
    pseudo_by_user = {}
    try:
        with _vault().transaction() as vault:
            pseudo_by_user = {str(row.get("user_id")): vault.get_or_assign(str(row.get("user_id")))
                              for row in entries if row.get("user_id") is not None}
    except Exception:
        pseudo_by_user = {}
    counts = {"adjusted": 0, "skipped_changed": 0, "failed": 0, "unverified": 0}
    skipped = []
    for row in entries:
        outcome = str(row.get("outcome") or "")
        if outcome == "done":
            counts["adjusted"] += 1
        elif outcome in {"score_changed_since_preview", "changed_since_adjustment"}:
            counts["skipped_changed"] += 1
            if pseudo_by_user.get(str(row.get("user_id"))):
                skipped.append(pseudo_by_user[str(row["user_id"])])
        elif outcome == "grade_write_unverified":
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


def preview_grade_adjustment(course_id: str, assignment_id: str,
                             adjustment: dict) -> dict:
    course_key = str(course_id or "").strip()
    assignment_key = str(assignment_id or "").strip()
    if not course_key or not assignment_key:
        return {"ok": False, "code": "invalid_adjustment",
                "error": "course_id and assignment_id are required"}
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}
    try:
        normalized = _canonical_adjustment(adjustment)
        adapter = registry.get_adapter(KIND)
        payload = adapter.build_payload({
            "course_id": course_key,
            "assignment_id": assignment_key,
            "adjustment": normalized,
        })
        provisional = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        baseline = adapter.capture_baseline(payload, provisional)
        if baseline.get("blocking_error"):
            return _freshness_refusal(baseline)
        vault = _vault()
        with vault.transaction():
            entries, extra_skipped, excluded_ids = _prepare_entries(baseline, normalized, vault)
        entries = [row for row in entries if row.get("changed")]
        if not entries:
            return {"ok": False, "code": "no_changes",
                    "error": "No grade entries would change."}
        payload = adapter.freeze_payload(payload, baseline)
        payload["entries"] = copy.deepcopy(entries)
        payload["_review"] = {
            "assignment_title": payload["assignment_name"],
            "points_possible": payload["points_possible"],
            "adjustment": copy.deepcopy(normalized),
            "summary": _summary(baseline, entries, extra_skipped=extra_skipped,
                                 excluded_ids=excluded_ids),
            "changed": [{"pseudonym": row["pseudonym"],
                         "before": row["before"], "after": row["after"]}
                        for row in entries],
        }
        target = adapter.verify_targets(payload, [{"course_id": course_key}])[0]
        target_record = models.new_target(
            target_key=target["target_key"], idempotency_key=target["idempotency_key"],
            course_id=course_key, baseline=baseline,
            steps=adapter.initial_steps(payload, baseline),
        )
        operation_id = models.new_operation_id()
        payload["operation_id"] = operation_id
        operation = models.new_operation(
            operation_id=operation_id, kind=KIND,
            source_ref={"type": "grade_adjustment"},
            source_digest=adapter.source_digest(payload),
            normalized_payload=payload, targets=[target_record],
        )
        operations.create_operation(operation)
        frozen = adapter.freeze_review(payload, target_record, baseline)
        batch = batches.freeze_batch([operation_id], {operation_id: [frozen]})
        operations.set_operation_review(operation_id, batch)
    except ValueError as exc:
        message = str(exc)
        return {"ok": False, "code": "invalid_adjustment",
                "error": message if message.startswith("invalid_adjustment") else message}
    except Exception:
        return {"ok": False, "error": "grade adjustment preview could not be prepared"}
    return {
        "ok": True,
        "operation_id": operation_id,
        "batch_id": batch["batch_id"],
        "review_digest": batch["review_digest"],
        "preview": frozen,
    }


def apply_grade_adjustment(operation_id: str, batch_id: str,
                           review_digest: str) -> dict:
    operation_key = str(operation_id or "").strip()
    if not operation_key or not str(batch_id or "").strip() or not str(review_digest or "").strip():
        return {"ok": False, "error": "operation_id, batch_id, and review_digest are required"}
    operation = operations.get_operation(operation_key)
    if operation is None or operation.get("kind") != KIND:
        return {"ok": False, "error": "grade adjustment operation was not found"}
    if operation.get("status") == "applied":
        _record_rule_reversion(operation)
        return _apply_projection(operation_key,
                                 {"ok": True, "status": "already_applied"}, operation)
    try:
        result = executor.apply_operation(operation_key, str(batch_id), str(review_digest))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:
        return {"ok": False, "error": "grade adjustment apply could not complete"}
    if result.get("state") == "applied":
        _record_rule_reversion(operations.get_operation(operation_key) or operation)
    return _apply_projection(operation_key, result, operation)


def _record_rule_reversion(operation: dict) -> None:
    payload = operation.get("normalized_payload") or {}
    adjustment = payload.get("adjustment") or {}
    if adjustment.get("kind") != "revert_rule":
        return
    course_id = str(payload.get("course_id") or "")
    assignment_id = str(payload.get("assignment_id") or "")
    rule_id = str(adjustment.get("rule_id") or "")
    try:
        rule_events = score_ledger.list_rule_events(course_id)
        rule = next((event for event in rule_events if event.get("rule_id") == rule_id
                     and event.get("action") == "create"), None)
        if not rule:
            return
        if rule.get("scope") == "course":
            score_curves.exclude_assignment(course_id, rule_id, assignment_id)
        elif score_curves.resolve_rule(course_id, assignment_id) is not None:
            score_curves.deactivate_rule(course_id, rule_id)
    except Exception:
        # Reversion grade writes already have their immutable outcome; lifecycle
        # journal failure is surfaced by the next rule resolution as a blocker.
        return


__all__ = ["KIND", "preview_grade_adjustment", "apply_grade_adjustment",
           "report_adjustments", "active_adjustment_assignments"]
