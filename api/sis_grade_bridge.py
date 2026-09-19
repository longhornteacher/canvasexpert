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
        return {"ok": False, "error": "bridge family links could not be read"}
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
    active_rows, error, complete = canvas_client.canvas_get_all_complete(
        f"/api/v1/courses/{course_id}/users",
        {"enrollment_type[]": "student", "enrollment_state[]": "active", "per_page": 100},
    )
    if error or not complete or not isinstance(active_rows, list):
        return {"complete": False, "source_count": len(source_ids), "member_count": 0, "overlap_count": None, "active_count": None, "exact_source_member_coverage": False}
    active = {str(row.get("id")) for row in active_rows if str(row.get("id") or "").strip()}
    member_sets = []
    for assignment_id in source_ids:
        overrides, error, complete = canvas_client.canvas_get_all_complete(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides",
            {"per_page": 100},
        )
        if error or not complete or not isinstance(overrides, list):
            return {"complete": False, "source_count": len(source_ids), "member_count": 0, "overlap_count": None, "active_count": len(active), "exact_source_member_coverage": False}
        members = set()
        for override in overrides:
            for value in (override.get("student_ids") or []):
                if str(value).strip():
                    members.add(str(value).strip())
            group_id = str(override.get("group_id") or "").strip()
            if group_id:
                group_rows, group_error, group_complete = canvas_client.canvas_get_all_complete(
                    f"/api/v1/groups/{group_id}/users", {"per_page": 100}
                )
                if group_error or not group_complete or not isinstance(group_rows, list):
                    return {"complete": False, "source_count": len(source_ids), "member_count": 0, "overlap_count": None, "active_count": len(active), "exact_source_member_coverage": False}
                members.update(str(row.get("id")) for row in group_rows if str(row.get("id") or "").strip())
        member_sets.append(members)
    union = set().union(*member_sets) if member_sets else set()
    overlaps = sum(1 for member in active if sum(member in values for values in member_sets) > 1)
    return {
        "complete": bool(member_sets) and all(member_sets),
        "source_count": len(source_ids),
        "member_count": len(union & active),
        "active_count": len(active),
        "overlap_count": overlaps,
        "exact_source_member_coverage": bool(member_sets) and all(member_sets) and overlaps == 0 and (union & active) == active,
    }


def _source_shape_reasons(row: dict) -> list[str]:
    """Return stable source-law failures without exposing assignment content."""
    checks = (
        ("published", True, "source_not_published"),
        ("grading_type", "points", "source_not_point_graded"),
        ("only_visible_to_overrides", True, "source_not_override_only"),
        ("omit_from_final_grade", True, "source_counts_toward_final_grade"),
        ("post_to_sis", False, "source_sis_sync_enabled"),
    )
    reasons = [code for field, expected, code in checks if field in row and row.get(field) != expected]
    if "points_possible" in row:
        try:
            if float(row.get("points_possible")) < 0:
                reasons.append("source_points_invalid")
        except (TypeError, ValueError):
            reasons.append("source_points_invalid")
    return reasons


def _bridge_safety_reasons(row: dict, *, expected_points=None, expected_group=None, expected_due=None) -> list[str]:
    checks = (
        ("published", True, "bridge_not_published"),
        ("grading_type", "points", "bridge_not_point_graded"),
        ("only_visible_to_overrides", False, "bridge_override_visibility"),
        ("omit_from_final_grade", False, "bridge_omitted_from_final_grade"),
        ("post_to_sis", True, "bridge_sis_sync_disabled"),
    )
    reasons = [code for field, expected, code in checks if field in row and row.get(field) != expected]
    if "submission_types" in row and sorted(row.get("submission_types") or []) != ["none"]:
        reasons.append("bridge_accepts_submissions")
    if expected_points is not None and "points_possible" in row:
        try:
            if abs(float(row.get("points_possible")) - float(expected_points)) > 1e-6:
                reasons.append("bridge_points_drift")
        except (TypeError, ValueError):
            reasons.append("bridge_points_drift")
    if expected_group is not None and "assignment_group_id" in row and str(row.get("assignment_group_id")) != str(expected_group):
        reasons.append("bridge_assignment_group_drift")
    if expected_due is not None and "due_at" in row and row.get("due_at") != expected_due:
        reasons.append("bridge_due_date_drift")
    if row.get("overrides") not in (None, []):
        reasons.append("bridge_overrides_present")
    return sorted(set(reasons))


def _family_row_identity(family: dict) -> str:
    return str(family.get("family_key") or family.get("family_title") or "").strip().casefold()


def reconcile_sis_grade_bridges(course_id: str, *, assignments: list[dict] | None = None) -> dict:
    """Student-free CE-owned family reconciliation matrix.

    This is deliberately read-only. Missing family links and bridge repairs
    are represented as actionable rows for the reviewed operation path.
    """
    course_key = str(course_id or "").strip()
    if not course_key:
        return {"ok": False, "error": "course_id is required"}
    try:
        rows = assignments if assignments is not None else _course_assignments(course_key)
        registrations = config.list_sis_grade_bridges(course_key)
        families = differentiated_bridge.discover_families(
            rows, registrations, config.get_tier_tags()
        )
    except Exception:
        return {"ok": False, "error": "bridge discovery could not be completed", "blocking": True}

    matrix = []
    discovered_keys = set()
    title_counts = {}
    for candidate in families:
        candidate_title = str(candidate.get("family_title") or "").casefold()
        title_counts[candidate_title] = title_counts.get(candidate_title, 0) + 1
    for family in families:
        title = family["family_title"]
        identity = _family_row_identity(family)
        discovered_keys.add(identity)
        discovered_keys.add(title.casefold())
        registration = next(
            (record for record in registrations
             if str(record.get("family_key") or record.get("family_title") or "").casefold() == identity
             or str(record.get("family_title") or "").casefold() == title.casefold()),
            None,
        )
        source_ids = list(family.get("source_assignment_ids") or [])
        source_rows = [row for row in rows if str(row.get("id")) in {str(v) for v in source_ids}]
        # Discovery treats an unsuffixed title as a structural candidate. If
        # its live shape proves it is the no-submission bridge, move it back
        # to bridge candidates instead of counting it as a source.
        bridge_like = {
            str(row.get("id")) for row in source_rows
            if str(row.get("name") or "").strip().casefold() == title.casefold()
            and _bridge_safety_reasons(row) == []
        }
        if bridge_like:
            source_ids = [value for value in source_ids if str(value) not in bridge_like]
            source_rows = [row for row in source_rows if str(row.get("id")) not in bridge_like]
        source_titles = [str(row.get("name") or "") for row in sorted(source_rows, key=lambda item: str(item.get("id") or ""))]
        # An unsuffixed assignment is a source only when its live source shape
        # proves that role; title alone may never classify it as a bridge.
        base = title.casefold()
        for row in rows:
            if str(row.get("id")) in {str(v) for v in source_ids}:
                continue
            if str(row.get("name") or "").strip().casefold() == base and _source_shape_reasons(row) == [] and row.get("published") is True:
                source_ids.append(str(row.get("id")))
                source_rows.append(row)
                source_titles.append(str(row.get("name") or ""))
        coverage = _student_free_coverage(course_key, source_ids) if assignments is None else {
            "complete": None, "source_count": len(source_ids),
            "member_count": None, "active_count": None, "overlap_count": None,
            "exact_source_member_coverage": None,
        }
        reasons = []
        status = "synced"
        if title_counts.get(title.casefold(), 0) > 1:
            status = "blocked"
            reasons.append("ambiguous_family_identity")
        source_failures = []
        for row in source_rows:
            source_failures.extend(_source_shape_reasons(row))
        supplied_due = [row.get("due_at") for row in source_rows if row.get("due_at") is not None]
        if supplied_due and len(set(supplied_due)) != 1:
            source_failures.append("mixed_effective_due_dates")
        supplied_points = [str(row.get("points_possible")) for row in source_rows if row.get("points_possible") is not None]
        if supplied_points and len(set(supplied_points)) != 1:
            source_failures.append("mixed_points_possible")
        supplied_groups = [str(row.get("assignment_group_id")) for row in source_rows if row.get("assignment_group_id") is not None]
        if supplied_groups and len(set(supplied_groups)) != 1:
            source_failures.append("mixed_assignment_groups")
        # Supplied rows in unit tests may intentionally be slim projections;
        # live reconciliation always re-reads exact assignment structures.
        if assignments is None and source_failures:
            status = "blocked"
            reasons.extend(sorted(set(source_failures)))
        if len(source_ids) < 2:
            status = "incomplete"
            reasons.append("two_source_threshold_not_met")
        if assignments is None and not coverage.get("exact_source_member_coverage"):
            if coverage.get("complete") is False:
                status = "incomplete" if status != "blocked" else status
                reasons.append("source_member_coverage_incomplete")
            elif coverage.get("overlap_count"):
                status = "incomplete" if status != "blocked" else status
                reasons.append("source_member_coverage_overlaps")
            elif assignments is None:
                status = "incomplete" if status != "blocked" else status
                reasons.append("source_member_coverage_incomplete")
        if len(source_ids) >= 2 and assignments is not None and source_failures:
            status = "blocked"
            reasons.extend(sorted(set(source_failures)))

        raw_due = next((row.get("due_at") for row in source_rows if row.get("due_at")), None)
        expected_due = None
        if raw_due:
            try:
                expected_due = differentiated_bridge.require_family_delivery(raw_due, "matrix")[2]
            except ValueError:
                status = "blocked"
                reasons.append("effective_due_time_invalid")
        expected_name = differentiated_bridge.bridge_title(title)
        bridge_candidates = []
        for row in rows:
            row_name = str(row.get("name") or "").strip()
            if row_name.casefold() not in {title.casefold(), expected_name.casefold()}:
                continue
            if str(row.get("id")) in {str(v) for v in source_ids}:
                continue
            bridge_candidates.append(row)
        # A linked bridge ID is exact authority and is retained even when
        # title discovery no longer finds the family.
        if registration and str(registration.get("bridge_assignment_id") or "") not in {str(row.get("id")) for row in bridge_candidates}:
            linked_bridge = next((row for row in rows if str(row.get("id")) == str(registration.get("bridge_assignment_id"))), None)
            if linked_bridge:
                bridge_candidates.append(linked_bridge)
        safe_bridges = []
        unsafe_bridge_reasons = []
        for bridge in bridge_candidates:
            safety = _bridge_safety_reasons(
                bridge,
                expected_points=(source_rows[0].get("points_possible") if source_rows else None),
                expected_group=(source_rows[0].get("assignment_group_id") if source_rows else None),
                expected_due=expected_due,
            )
            if not safety:
                safe_bridges.append(bridge)
            else:
                unsafe_bridge_reasons.extend(safety)
        if len(safe_bridges) > 1:
            status = "blocked"
            reasons.append("multiple_bridge_targets")
        if unsafe_bridge_reasons and not safe_bridges:
            status = "blocked"
            reasons.extend(sorted(set(unsafe_bridge_reasons)))
        bridge = safe_bridges[0] if len(safe_bridges) == 1 else None
        drift_fields = []
        if bridge and expected_due:
            expected = {"name": expected_name, "description": differentiated_bridge.bridge_description(), "due_at": expected_due}
            drift_fields = sorted(field for field, value in expected.items() if bridge.get(field) != value)
            if str(bridge.get("name") or "").casefold() == title.casefold():
                drift_fields = [field for field in drift_fields if field != "name"]
            if drift_fields and status == "synced":
                status = "drifted"
                reasons.append("bridge_shape_drift")
        if bridge is None and not unsafe_bridge_reasons and status == "synced":
            status = "missing"
            if assignments is None:
                reasons.append("bridge_missing")
        if registration is None:
            if status == "synced":
                status = "missing"
            reasons.append("family_link_missing")
            if bridge:
                reasons.append("accepted_existing_bridge")
        elif bridge and str(registration.get("bridge_assignment_id") or "") != str(bridge.get("id") or ""):
            status = "blocked"
            reasons.append("family_link_bridge_not_in_family")
        elif bridge and registration.get("bridge_state_digest"):
            current_digest = differentiated_bridge.structural_digest(
                differentiated_bridge.assignment_shape(bridge, [])
            )
            if not drift_fields and str(registration.get("bridge_state_digest")) != current_digest:
                status = "blocked"
                reasons.append("family_link_bridge_digest_drift")
        matrix.append({
            "family_key": family["family_key"],
            "family_title": title,
            "status": status,
            "source_assignment_ids": source_ids,
            "source_titles": [str(row.get("name") or "") for row in sorted(source_rows, key=lambda item: str(item.get("id") or ""))] or family.get("source_titles", []),
            "source_tiers": family["source_tiers"],
            "bridge_assignment_id": str(bridge.get("id")) if bridge else None,
            "module_id": (registration or {}).get("module_id") or family.get("module_id"),
            "module_name": (registration or {}).get("module_name") or family.get("module_name"),
            "source_count": len(source_ids),
            "bridge_count": len(safe_bridges),
            "grading_excluded": all(row.get("omit_from_final_grade") is True for row in source_rows if "omit_from_final_grade" in row),
            "bridge_eligible": status not in {"blocked", "incomplete"},
            "coverage": coverage,
            "drift_fields": drift_fields,
            "reasons": sorted(set(reasons)),
            "identity_source": family["identity_source"],
            "action": ("create" if bridge is None else ("link" if registration is None and not drift_fields else ("repair" if drift_fields else "none"))),
        })
    # A saved registration is proof-bearing exact identity.  If discovery no
    # longer finds its sources, surface attention rather than silently dropping
    # the family from the teacher's matrix.
    for registration in registrations:
        key = str(registration.get("family_key") or registration.get("family_title") or "").casefold()
        if key in discovered_keys or str(registration.get("family_title") or "").casefold() in discovered_keys:
            continue
        matrix.append({
            "family_key": registration.get("family_key") or registration.get("family_title"),
            "family_title": registration.get("family_title"),
            "status": "blocked",
            "source_assignment_ids": list(registration.get("source_assignment_ids") or []),
            "source_titles": list(registration.get("source_titles") or []),
            "source_tiers": [], "bridge_assignment_id": registration.get("bridge_assignment_id"),
            "source_count": len(registration.get("source_assignment_ids") or []), "bridge_count": 0,
            "grading_excluded": bool(registration.get("grading_excluded", True)), "bridge_eligible": False,
            "coverage": {"complete": False, "source_count": len(registration.get("source_assignment_ids") or []), "member_count": None, "active_count": None, "overlap_count": None, "exact_source_member_coverage": False},
            "drift_fields": [], "reasons": ["family_linked_but_not_discoverable"], "identity_source": "family_link", "action": "blocked",
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
        if registration is None and discovered_family is None:
            return {
                "ok": False,
                "code": "family_link_required",
                "error": "This differentiated family needs a verified family link before SIS projection.",
                "user_action": (
                    "Run reconcile_sis_grade_bridges for this course, review the exact "
                    "family, then apply the reviewed family-link operation."
                ),
                "next": "reconcile_sis_grade_bridges(course_id)",
                "blocking": True,
            }
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
    row = next((item for item in matrix["matrix"] if str(item.get("family_title") or "").casefold() == str(family_title).casefold()), None)
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
