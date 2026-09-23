"""Assistant-facing SIS grade-bridge use case.

This is the shared non-HTTP boundary used by MCP. Discovery and preview read the
local sync/mirror and create one frozen operation; only the approved ledger apply
may reach Canvas Live. Results remain aggregate and student-free.
"""

from __future__ import annotations

import copy
import math
import re

from api.operation_ledger import batches, executor, models, operations, receipts, registry
from api.operation_ledger.adapters.sis_grade_bridge import KIND
from api.operation_ledger.adapters import differentiated_bridge
from api import course_catalog
from api.platform_services import config


_REPAIRABLE_REASONS = frozenset({
    "source_counts_toward_final_grade",
    "source_sis_sync_enabled",
    "family_link_missing",
    "accepted_existing_bridge",
    "bridge_missing",
    "bridge_omitted_from_final_grade",
    "bridge_sis_sync_disabled",
})

# AC1: a bridge candidate whose only unsafe settings are these two fields is
# repairable through the same reviewed path as a source's settings; any other
# unsafe bridge field (not published, accepts submissions, points/group drift,
# ...) still blocks and is never auto-repaired.
_BRIDGE_REPAIRABLE_REASONS = frozenset({
    "bridge_omitted_from_final_grade",
    "bridge_sis_sync_disabled",
})


def _bridge_setting_repairs(row: dict | None) -> list[dict]:
    if not row:
        return []
    fields = []
    if "omit_from_final_grade" in row and row.get("omit_from_final_grade") is not False:
        fields.append("omit_from_final_grade")
    if "post_to_sis" in row and row.get("post_to_sis") is not True:
        fields.append("post_to_sis")
    if not fields:
        return []
    return [{"bridge_assignment_id": str(row.get("id") or ""), "fields": fields}]


def _title_word_signature(title: str) -> tuple[str, ...]:
    """Order-independent word signature used to flag likely title splits (AC3)."""
    return tuple(sorted(re.findall(r"[\w']+", str(title or "").casefold())))


def _is_omittable_single_assignment(
    row: dict, *, has_registration: bool, tier_tags: dict | None = None,
) -> bool:
    """AC1: an ordinary single assignment is not a discovered bridge family.

    Omit a row only when every one of these holds: no saved registration
    (title fallback only), every source is unsuffixed (no configured tier
    tag proves a real tier), there is no bridge candidate, and the source
    count is under two. A title-mismatch or ambiguous-bridge-candidates row
    is always reported (AC2), never silently dropped.
    """
    if has_registration:
        return False
    if row.get("identity_source") != "title_fallback":
        return False
    if row.get("bridge_assignment_id") is not None:
        return False
    if (row.get("source_count") or 0) >= 2:
        return False
    tiers = row.get("source_tiers") or []
    if not tiers or set(tiers) - {"unsuffixed"}:
        return False
    reasons = set(row.get("reasons") or [])
    if "title_mismatch_suspected" in reasons or "bridge_candidates_ambiguous" in reasons:
        return False
    # A title ending in a configured tag word without the " - " separator
    # ("... SCRs Red") may be a messy tier source; keep it visible.
    tags = {str(v).strip().casefold() for v in (tier_tags or {}).values() if str(v or "").strip()}
    for title in row.get("source_titles") or []:
        words = re.findall(r"[\w']+", str(title or "").casefold())
        if words and words[-1] in tags:
            return False
    return True


def _source_setting_repairs(source_rows: list[dict]) -> list[dict]:
    repairs = []
    for row in sorted(source_rows, key=lambda item: str(item.get("id") or "")):
        fields = []
        if "omit_from_final_grade" in row and row.get("omit_from_final_grade") is not True:
            fields.append("omit_from_final_grade")
        if "post_to_sis" in row and row.get("post_to_sis") is not False:
            fields.append("post_to_sis")
        if fields:
            repairs.append({
                "source_assignment_id": str(row.get("id") or ""),
                "fields": fields,
            })
    return repairs


def _repairable_reconciliation_row(row: dict) -> bool:
    reasons = {str(value) for value in row.get("reasons") or []}
    return (
        str(row.get("status") or "") == "blocked"
        and bool(reasons)
        and reasons <= _REPAIRABLE_REASONS
        and len(row.get("source_assignment_ids") or []) >= 2
    )


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


class _CatalogNotCurrentError(ValueError):
    """The local course catalog is not current (AC2).

    A ``ValueError`` subclass so a caller with an existing bare
    ``except ValueError`` (for example ``_preview_agent_grouping``'s
    ``mirror_read_failed`` refusal) keeps catching it unchanged; the two
    discovery entry points named by AC2 catch this type specifically first
    to return the richer, distinguished refusal.
    """

    def __init__(self, sections: dict[str, str]):
        super().__init__("local course catalog is not current")
        self.sections = dict(sections)


def _held_student_pseudonyms(user_ids: list[str] | None) -> list[str]:
    """AC6: pseudonyms only, resolved through CE's existing identity/pseudonym
    service -- never Canvas user ids or names. Never a new mapping; any
    resolution failure fails closed to an empty list rather than raise, so a
    held count is never blocked by a pseudonym-service hiccup."""
    ids = sorted({str(value).strip() for value in (user_ids or []) if str(value).strip()})
    if not ids:
        return []
    try:
        from api.identity_vault_service import open_vault
        vault = open_vault()
        with vault.transaction():
            pseudonyms = [vault.get_or_assign(user_id) for user_id in ids]
    except Exception:
        return []
    return sorted({str(value) for value in pseudonyms if value})


def _blocking_error_result(baseline: dict) -> dict:
    """Shape a preview's ``capture_baseline`` blocking_error (AC2/AC5).

    ``catalog_not_current`` gets the full host-neutral refusal shape;
    every other blocking reason keeps its existing plain ``error``/
    ``drift_fields`` shape unchanged.
    """
    error = baseline.get("blocking_error")
    if error == "catalog_not_current":
        return _catalog_not_current_refusal(baseline.get("sections") or {})
    result = {"ok": False, "error": error, "blocking": True}
    fields = baseline.get("drift_fields")
    if isinstance(fields, list) and all(isinstance(field, str) for field in fields):
        result["drift_fields"] = sorted(set(fields))
    return result


def _catalog_not_current_refusal(sections: dict[str, str]) -> dict:
    """AC2/AC5: the one host-neutral, plain-text shape any MCP host can act
    on -- documented in ``docs/mcp-server.md``, never assistant-specific."""
    return {
        "ok": False,
        "code": "catalog_not_current",
        "blocking": True,
        "sections": dict(sections),
        "error": "The local course catalog is not current.",
        "next": (
            "Ask the teacher whether to refresh this course's structure "
            "(refresh_course_structure). Do not refresh automatically."
        ),
    }


def _course_assignments(course_id: str) -> list[dict]:
    result = course_catalog.read_catalog(course_id)
    catalog = result.get("catalog") if isinstance(result, dict) else None
    scope = catalog.get("assignments") if isinstance(catalog, dict) else None
    if not isinstance(scope, dict) or scope.get("state") != "current":
        state = scope.get("state") if isinstance(scope, dict) else "unavailable"
        raise _CatalogNotCurrentError({"assignments": str(state or "unavailable")})
    records = scope.get("records")
    if not isinstance(records, dict):
        raise _CatalogNotCurrentError({"assignments": "incomplete"})
    return [
        {"course_id": str(course_id), "id": str(assignment_id), **row}
        for assignment_id, row in records.items()
        if isinstance(row, dict)
    ]


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


def _bridge_safety_reasons(row: dict, *, expected_points=None, expected_group=None) -> list[str]:
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
    if row.get("overrides") not in (None, []):
        reasons.append("bridge_overrides_present")
    return sorted(set(reasons))


def _points_equal(left, right) -> bool:
    try:
        left_number = float(left)
        right_number = float(right)
    except (TypeError, ValueError):
        return left == right
    if not math.isfinite(left_number) or not math.isfinite(right_number):
        return False
    return math.isclose(left_number, right_number, rel_tol=0.0, abs_tol=1e-6)


def _distinct_point_values(values: list[object]) -> list[object]:
    raw_values = []
    display_values = []
    for value in values:
        if any(_points_equal(value, previous) for previous in raw_values):
            continue
        raw_values.append(value)
        try:
            number = float(value)
            if math.isfinite(number):
                value = int(number) if number.is_integer() else number
        except (TypeError, ValueError):
            pass
        display_values.append(value)
    return display_values


def _distinct_group_values(values: list[object]) -> list[str]:
    result = []
    for value in values:
        display = str(value or "")
        if display not in result:
            result.append(display)
    return result


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
        # Discovery is mirror-backed even when the caller did not provide a
        # synthetic assignment list. This keeps the rest of the matrix logic
        # on the supplied, local snapshot branch and out of CanvasLive.
        assignments = rows
        registrations = config.list_sis_grade_bridges(course_key)
        families = differentiated_bridge.discover_families(
            rows, registrations, config.get_tier_tags()
        )
    except _CatalogNotCurrentError as exc:
        # AC2: this is its own answer, not a failure and not drift -- the
        # local catalog simply is not current yet.
        return _catalog_not_current_refusal(exc.sections)
    except Exception:
        return {"ok": False, "error": "bridge discovery could not be completed", "blocking": True}

    matrix = []
    discovered_keys = set()
    omitted_single_assignments = 0
    title_counts = {}
    for candidate in families:
        candidate_title = str(candidate.get("family_title") or "").casefold()
        title_counts[candidate_title] = title_counts.get(candidate_title, 0) + 1
    # AC3: two families with the same normalized word set but a different
    # word order are never merged; both are reported so the teacher can
    # disambiguate rather than watch one silently vanish into the other.
    signature_titles: dict[tuple[str, ...], set[str]] = {}
    for candidate in families:
        signature = _title_word_signature(candidate.get("family_title"))
        if signature:
            signature_titles.setdefault(signature, set()).add(
                str(candidate.get("family_title") or "").casefold()
            )
    mismatch_signatures = {
        signature for signature, titles in signature_titles.items() if len(titles) > 1
    }
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
        base = title.casefold()
        # A saved registration's source IDs are proof-bearing exact identity.
        # AC1 never reclassifies a registered source as the bridge, whatever
        # its title looks like.
        registered_source_ids = {
            str(v) for v in (registration or {}).get("source_assignment_ids") or []
        }
        # AC1: an unsuffixed row (exact base title, no configured tag) is
        # bridge material -- never a source -- whenever at least two real
        # tag-suffixed sources exist, whatever the unsuffixed row's current
        # settings are. With fewer than two suffixed sources there is nothing
        # yet to anchor that rule, so an unsuffixed row keeps the legacy
        # source-shape-proves-the-role behavior.
        unsuffixed_rows = [
            row for row in source_rows
            if str(row.get("name") or "").strip().casefold() == base
            and str(row.get("id")) not in registered_source_ids
        ]
        suffixed_rows = [row for row in source_rows if row not in unsuffixed_rows]
        for row in rows:
            row_id = str(row.get("id"))
            if row_id in {str(v) for v in source_ids}:
                continue
            if row_id in registered_source_ids:
                continue
            if str(row.get("name") or "").strip().casefold() == base:
                unsuffixed_rows.append(row)

        reasons = []
        status = "synced"
        if title_counts.get(title.casefold(), 0) > 1:
            status = "blocked"
            reasons.append("ambiguous_family_identity")
        signature = _title_word_signature(title)
        ambiguous_bridge_candidates = False
        if signature in mismatch_signatures:
            status = "blocked"
            reasons.append("title_mismatch_suspected")

        if len(suffixed_rows) >= 2 and len(unsuffixed_rows) > 1:
            ambiguous_bridge_candidates = True
            status = "blocked"
            reasons.append("bridge_candidates_ambiguous")
            source_ids = [str(row.get("id")) for row in suffixed_rows]
            source_rows = suffixed_rows
        elif len(suffixed_rows) >= 2 and len(unsuffixed_rows) == 1:
            # The lone unsuffixed row is bridge candidate material: pull it
            # out of the sources so it is picked up by the bridge-candidate
            # scan below, whatever its current settings prove.
            source_ids = [str(row.get("id")) for row in suffixed_rows]
            source_rows = suffixed_rows
        source_titles = [str(row.get("name") or "") for row in sorted(source_rows, key=lambda item: str(item.get("id") or ""))]
        source_failures = []
        for row in source_rows:
            source_failures.extend(_source_shape_reasons(row))
        supplied_points = [row.get("points_possible") for row in source_rows if row.get("points_possible") is not None]
        if supplied_points and not all(_points_equal(supplied_points[0], value) for value in supplied_points[1:]):
            source_failures.append("mixed_points_possible")
        supplied_groups = [str(row.get("assignment_group_id")) for row in source_rows if row.get("assignment_group_id") is not None]
        if supplied_groups and len(set(supplied_groups)) != 1:
            source_failures.append("mixed_assignment_groups")
        if len(source_ids) < 2:
            status = "incomplete"
            reasons.append("two_source_threshold_not_met")
        if len(source_ids) >= 2 and source_failures:
            status = "blocked"
            reasons.extend(sorted(set(source_failures)))

        expected_name = differentiated_bridge.bridge_title(title)
        bridge_candidates = []
        if not ambiguous_bridge_candidates:
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
        repairable_bridge = None
        repairable_bridge_reasons: list[str] = []
        unsafe_bridge_reasons = []
        for candidate_row in bridge_candidates:
            safety = _bridge_safety_reasons(
                candidate_row,
                expected_points=(source_rows[0].get("points_possible") if source_rows else None),
                expected_group=(source_rows[0].get("assignment_group_id") if source_rows else None),
            )
            if not safety:
                safe_bridges.append(candidate_row)
            elif set(safety) <= _BRIDGE_REPAIRABLE_REASONS and repairable_bridge is None and not safe_bridges:
                repairable_bridge = candidate_row
                repairable_bridge_reasons = safety
            else:
                unsafe_bridge_reasons.extend(safety)
        if len(safe_bridges) > 1:
            status = "blocked"
            reasons.append("multiple_bridge_targets")
        bridge = None
        if len(safe_bridges) == 1:
            bridge = safe_bridges[0]
        elif not safe_bridges and repairable_bridge is not None and not unsafe_bridge_reasons:
            # AC1: a bridge candidate that is only SIS-off or still counts
            # toward the final grade is repairable, not a hard block.
            bridge = repairable_bridge
            status = "blocked"
            reasons.extend(sorted(set(repairable_bridge_reasons)))
        if unsafe_bridge_reasons and not safe_bridges and bridge is None:
            status = "blocked"
            reasons.extend(sorted(set(unsafe_bridge_reasons)))
        drift_fields = []
        if bridge is None and not unsafe_bridge_reasons and status == "synced":
            status = "missing"
        if bridge is None and not unsafe_bridge_reasons:
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
        action = (
            "create" if bridge is None else
            ("link" if registration is None and not drift_fields else
             ("repair" if drift_fields else "none"))
        )
        repairable = _repairable_reconciliation_row({
            "status": status,
            "reasons": reasons,
            "source_assignment_ids": source_ids,
        })
        mirror_coverage = {
            "source": "mirror",
            "complete": None,
            "source_count": len(source_ids),
            "member_count": None,
            "active_count": None,
            "overlap_count": None,
            "exact_source_member_coverage": None,
        }
        matrix_row = {
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
            "coverage": mirror_coverage,
            "drift_fields": drift_fields,
            "reasons": sorted(set(reasons)),
            "identity_source": family["identity_source"],
            "action": action,
            "repairable": repairable,
            "repair_plan": {
                "source_assignment_ids": list(source_ids),
                "bridge_assignment_id": str(bridge.get("id")) if bridge else None,
                "action": action,
                "source_setting_repairs": _source_setting_repairs(source_rows),
                "bridge_setting_repairs": _bridge_setting_repairs(bridge),
            },
        }
        # AC1: an ordinary single assignment (no registration, no real tag,
        # no bridge candidate, under the two-source threshold) is not a
        # discovered bridge family; it is counted, never listed (AC3).
        if _is_omittable_single_assignment(
            matrix_row, has_registration=registration is not None,
            tier_tags=config.get_tier_tags(),
        ):
            omitted_single_assignments += 1
            continue
        matrix.append(matrix_row)
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
            "repairable": False,
            "repair_plan": {
                "source_assignment_ids": list(registration.get("source_assignment_ids") or []),
                "bridge_assignment_id": registration.get("bridge_assignment_id"),
                "action": "blocked",
                "source_setting_repairs": [],
                "bridge_setting_repairs": [],
            },
        })
    return {
        "ok": True,
        "course_id": course_key,
        "matrix": matrix,
        "omitted_single_assignments": omitted_single_assignments,
    }


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
            return _blocking_error_result(baseline)
        payload = adapter.freeze_payload(payload, baseline)
        target = adapter.verify_targets(
            payload, [{"course_id": course_key}]
        )[0]
        # Re-read once against the frozen identity so the persisted baseline is
        # already the exact state that apply will drift-check.
        baseline = adapter.capture_baseline(payload, target)
        if baseline.get("blocking_error"):
            return _blocking_error_result(baseline)

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
        if payload.get("mode") != "reconcile":
            # AC6: per-family preview summary. held_students is pseudonyms
            # only, resolved here (the assistant-facing boundary) -- the
            # adapter's baseline stays student-free/mirror-only and only
            # ever carries raw local user ids, never exposed as such.
            counts = baseline.get("counts") or {}
            frozen = {
                **frozen,
                "raises": int(counts.get("raised_scores") or 0),
                "already_canon": int(counts.get("already_matching") or 0),
                "held": int(counts.get("held") or 0),
                "held_students": _held_student_pseudonyms(baseline.get("held_user_ids")),
            }
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


def _preview_agent_grouping(
    course_id: str,
    family_title: str,
    *,
    source_assignment_ids: list[str],
    bridge_assignment_id: str | None = None,
) -> dict:
    course_key = str(course_id or "").strip()
    title = str(family_title or "").strip()
    if not _current_course(course_key):
        return {"ok": False, "error": "course is not in Current courses"}

    source_ids = []
    seen_ids = set()
    for value in source_assignment_ids or []:
        assignment_id = str(value).strip() if value is not None else ""
        if assignment_id and assignment_id not in seen_ids:
            seen_ids.add(assignment_id)
            source_ids.append(assignment_id)
    if len(source_ids) < 2:
        return {
            "ok": False,
            "code": "two_source_threshold_not_met",
            "error": "A proposed family must contain at least two distinct source assignments.",
        }

    try:
        rows = _course_assignments(course_key)
    except ValueError:
        return {
            "ok": False,
            "code": "mirror_read_failed",
            "error": "The current course assignment mirror is unavailable or incomplete.",
            "user_action": "Refresh the course mirror once, then retry this proposal.",
            "blocking": True,
        }
    by_id = {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    missing_ids = [assignment_id for assignment_id in source_ids if assignment_id not in by_id]
    if missing_ids:
        return {
            "ok": False,
            "code": "source_exact_id_unverified",
            "missing_assignment_ids": missing_ids,
            "error": "The proposed source assignment IDs are not all present in the local mirror.",
            "user_action": "Refresh the course mirror once, then retry this proposal.",
        }

    try:
        registrations = config.list_sis_grade_bridges(course_key)
    except Exception:
        return {
            "ok": False,
            "code": "family_links_read_failed",
            "error": "Existing bridge family links could not be read.",
            "blocking": True,
        }
    proposed_ids = set(source_ids)
    requested_title_key = title.casefold()
    for registration in registrations or []:
        if not isinstance(registration, dict):
            continue
        existing_title = str(registration.get("family_title") or "").strip()
        existing_ids = {
            str(value).strip()
            for value in (registration.get("source_assignment_ids") or [])
            if str(value).strip()
        }
        bridge_id = str(registration.get("bridge_assignment_id") or "").strip()
        if (
            existing_title.casefold() == requested_title_key
            or proposed_ids.intersection(existing_ids)
            or bridge_id in proposed_ids
        ):
            return {
                "ok": False,
                "code": "family_already_linked",
                "error": "A proposed source or family title is already linked.",
                "conflicting_family_title": existing_title,
            }

    source_rows = [by_id[assignment_id] for assignment_id in source_ids]
    point_values = [row.get("points_possible") for row in source_rows]
    if point_values and not all(_points_equal(point_values[0], value) for value in point_values[1:]):
        values = _distinct_point_values(point_values)
        return {
            "ok": False,
            "code": "mixed_points_possible",
            "points_possible": values,
            "values": values,
        }
    group_values = _distinct_group_values(
        [row.get("assignment_group_id") for row in source_rows]
    )
    if len(group_values) > 1:
        return {
            "ok": False,
            "code": "mixed_assignment_groups",
            "assignment_group_ids": group_values,
            "values": group_values,
        }

    bridge_id = str(bridge_assignment_id).strip() if bridge_assignment_id is not None else ""
    if bridge_id:
        if bridge_id not in by_id or bridge_id in proposed_ids:
            return {
                "ok": False,
                "code": "bridge_exact_id_unverified",
                "bridge_assignment_id": bridge_id,
                "error": "The proposed bridge assignment ID is missing or is also a source.",
            }

    discovered = {
        "family_key": title,
        "family_title": title,
        "source_assignment_ids": source_ids,
        "source_titles": [str(row.get("name") or "") for row in source_rows],
        "bridge_assignment_id": bridge_id or None,
        "module_id": None,
        "module_name": None,
    }
    result = preview_sis_grade_bridge(
        course_key, title, discovered_family=discovered
    )
    reserved_error = "Differentiated family titles must be unsuffixed; '- Bridge' is reserved"
    if result.get("error") == reserved_error:
        return {
            "ok": False,
            "code": "family_title_reserved_suffix",
            "error": "Choose a family title that does not end in '- Bridge'.",
        }
    if result.get("ok"):
        result["user_action"] = (
            "Teacher confirms the exact source titles in the frozen review, then call "
            "apply_sis_grade_bridge with the unchanged operation coordinates."
        )
        result["next"] = (
            "Teacher confirms the frozen review, then call apply_sis_grade_bridge "
            "with the unchanged operation coordinates."
        )
    return result


def preview_sis_grade_bridge_reconciliation(
    course_id: str,
    family_title: str,
    *,
    source_assignment_ids: list[str] | None = None,
    bridge_assignment_id: str | None = None,
    assignments: list[dict] | None = None,
) -> dict:
    """Create a reviewed operation for a CE-discovered missing/drifted family."""
    if source_assignment_ids:
        return _preview_agent_grouping(
            course_id,
            family_title,
            source_assignment_ids=source_assignment_ids,
            bridge_assignment_id=bridge_assignment_id,
        )
    matrix = reconcile_sis_grade_bridges(course_id, assignments=assignments)
    if not matrix.get("ok"):
        return matrix
    row = next((item for item in matrix["matrix"] if str(item.get("family_title") or "").casefold() == str(family_title).casefold()), None)
    if row is None:
        return {"ok": False, "error": "differentiated family was not discovered", "blocking": True}
    if row["status"] not in {"missing", "drifted"} and not row.get("repairable"):
        return {"ok": False, "error": f"family is {row['status']}", "blocking": row["status"] in {"blocked", "incomplete"}}
    family = dict(row)
    result = preview_sis_grade_bridge(course_id, family_title, discovered_family=family)
    if result.get("ok"):
        result["user_action"] = (
            "Reconcile -> preview the exact family -> teacher confirms -> apply the "
            "unchanged operation coordinates."
        )
        result["next"] = "Teacher confirms, then call apply_sis_grade_bridge with the unchanged operation coordinates."
    return result


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
        "score": "raised_scores",
        "excuse": "copied_excused",
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
        "course_id": target.get("course_id"),
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
