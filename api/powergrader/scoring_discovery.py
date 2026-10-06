"""Cross-course, student-free discovery for MCP Scoring Sessions."""
from __future__ import annotations

from datetime import datetime, timezone

from .scoring_local import FRESHNESS_COLUMNS

# Leading columns are unchanged for existing renderers; new ones are appended.
ASSIGNMENT_COLUMNS = (
    "course_id", "course_name", "assignment_id", "assignment_name", "due_at",
    "points", "ungraded", "partially_scored", "late_ungraded",
    "resumable_status", "scoring_session_id", "mirror_revision",
    "family_title", "family_role", "family_link_state",
    "bridge_assignment_id", "bridge_status", "bridge_required", "next_step",
    "coverage", "counts_complete",
)
ATTENTION_COLUMNS = (
    "course_id", "course_name", "code", "retryable", "operation_id",
    "refresh_status", "user_action", "assignment_id",
)
DISCOVERY_FRESHNESS_COLUMNS = (*FRESHNESS_COLUMNS, "coverage")
_ACTIONABLE_STATUSES = frozenset({"ready", "needs_teacher_input", "staged"})


def _family_advisory(assignments: list[dict], *, tier_tags=None, registrations=()):
    """Classify snapshot rows without a Canvas read or student data."""
    from api.operation_ledger.adapters import differentiated_bridge

    registrations = [row for row in (registrations or ()) if isinstance(row, dict)]
    # Stable family/tier metadata is authoritative when a teacher has renamed
    # a source or uses public tags that are not the current Settings values.
    # Keep the title-based path below for ordinary mirror rows, but seed it
    # with the same family facts used by bridge reconciliation so every
    # differentiated source receives one bridge obligation.
    metadata_by_id = {}
    assignments_by_id = {
        str(row.get("id") or ""): row
        for row in assignments if isinstance(row, dict)
    }
    for family in differentiated_bridge.discover_families(
        assignments, registrations, tier_tags
    ):
        source_ids = {str(value) for value in family.get("source_assignment_ids") or []}
        bridge_ids = [str(value) for value in family.get("bridge_assignment_ids") or []]
        registration = next(
            (
                item for item in registrations
                if str(item.get("family_key") or item.get("family_title") or "").casefold()
                == str(family.get("family_key") or family.get("family_title") or "").casefold()
            ),
            None,
        )
        registered_bridge_id = str((registration or {}).get("bridge_assignment_id") or "")
        if registered_bridge_id and registered_bridge_id not in bridge_ids:
            bridge_ids.append(registered_bridge_id)
        if len(source_ids) < 2 and not bridge_ids and not registration:
            # Title fallback also discovers ordinary single assignments. A lone
            # unsuffixed row is not evidence of differentiated work.
            explicit_tier = any(
                differentiated_bridge.title_tag_parts(
                    assignments_by_id.get(source_id, {}).get("name"), tier_tags
                )[1]
                or (
                    assignments_by_id.get(source_id, {}).get("metadata", {}).get("tier")
                    if isinstance(assignments_by_id.get(source_id, {}).get("metadata"), dict)
                    else None
                )
                for source_id in source_ids
            )
            if not explicit_tier:
                continue
        bridge_id = bridge_ids[0] if len(bridge_ids) == 1 else (registered_bridge_id or None)
        bridge_status = (
            "ambiguous" if len(bridge_ids) > 1 else
            ("linked" if registration and bridge_id == registered_bridge_id else
             ("present" if bridge_id else "missing"))
        )
        facts = {
            "family_title": str(family.get("family_title") or "").strip(),
            "source_count": len(source_ids),
            "bridge_id": bridge_id,
            "bridge_status": bridge_status,
        }
        for assignment_id in source_ids:
            metadata_by_id[assignment_id] = {"role": "source", **facts}
        for assignment_id in bridge_ids:
            metadata_by_id[assignment_id] = {"role": "bridge", **facts}
    by_id = {}
    for registration in registrations:
        for key in ("source_assignment_ids", "bridge_assignment_id"):
            values = registration.get(key) if key == "source_assignment_ids" else [registration.get(key)]
            for value in values or []:
                if str(value or "").strip():
                    by_id[str(value)] = registration
    bases = {}
    bridges_by_base = {}
    sources_by_base = {}

    def is_bridge_shape(row: dict) -> bool:
        return (
            sorted(row.get("submission_types") or []) == ["none"]
            and row.get("only_visible_to_overrides") is False
            and row.get("omit_from_final_grade") is False
            and row.get("post_to_sis") is True
            and row.get("published") is True
            and row.get("grading_type") == "points"
        )

    for row in assignments:
        if not isinstance(row, dict):
            continue
        base, tag = differentiated_bridge.title_tag_parts(row.get("name"), tier_tags)
        bridge_named = str(row.get("name") or "").strip().casefold().endswith(" - bridge")
        if tag or bridge_named or is_bridge_shape(row):
            family = bases.setdefault(base.casefold(), {"title": base, "sources": []})
            if is_bridge_shape(row) or bridge_named:
                bridges_by_base.setdefault(base.casefold(), []).append(str(row.get("id") or ""))
            else:
                family["sources"].append(str(row.get("id") or ""))
                sources_by_base.setdefault(base.casefold(), []).append(str(row.get("id") or ""))
    result = {}
    for row in assignments:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "")
        registration = by_id.get(aid)
        metadata_family = metadata_by_id.get(aid)
        base, tag = differentiated_bridge.title_tag_parts(row.get("name"), tier_tags)
        bridge_named = str(row.get("name") or "").strip().casefold().endswith(" - bridge")
        shape_bridge = is_bridge_shape(row)
        family_key = base.casefold()
        registration_bridge = str((registration or {}).get("bridge_assignment_id") or "")
        family_title = str(
            (metadata_family or {}).get("family_title")
            or (registration or {}).get("family_title")
            or base
        ).strip() if (metadata_family or tag or registration or bridge_named or shape_bridge) else None
        role = (
            (metadata_family or {}).get("role")
            or ("bridge" if (registration and aid == registration_bridge) or bridge_named or shape_bridge else ("source" if tag else None))
        )
        family = bases.get(base.casefold())
        source_count = (
            (metadata_family or {}).get("source_count")
            if metadata_family else len(family.get("sources") or []) if family else 0
        )
        bridge_ids = list(bridges_by_base.get(family_key) or [])
        if registration_bridge and registration_bridge not in bridge_ids:
            bridge_ids.append(registration_bridge)
        bridge_id = (
            (metadata_family or {}).get("bridge_id")
            if metadata_family else
            (bridge_ids[0] if len(bridge_ids) == 1 else (registration_bridge or None))
        )
        state = "linked" if registration else ("needs_repair" if source_count >= 2 and role in {"source", "bridge"} else None)
        bridge_status = (
            (metadata_family or {}).get("bridge_status")
            if metadata_family else
            ("linked" if registration and bridge_id == registration_bridge else
             ("present" if bridge_id else ("missing" if source_count >= 2 else None)))
        )
        result[aid] = {
            "family_title": family_title,
            "family_role": role,
            "family_link_state": state,
            "bridge_assignment_id": bridge_id,
            "bridge_status": bridge_status,
            "bridge_required": role == "source",
            "next_step": (
                (
                    "This differentiated family has no bridge yet. Reconcile the exact "
                    "family before scoring; scoring remains incomplete until the bridge "
                    "score is prepared and applied through the reviewed SIS bridge operation."
                    if bridge_status == "missing" else
                    "Scoring this tier is incomplete until the corresponding bridge score "
                    "is prepared and applied through the reviewed SIS bridge operation."
                )
                if role == "source" else None
            ),
        }
    return result



def _course_identity(course: dict) -> tuple[str, str]:
    course_id = str(course.get("id") or course.get("course_id") or "").strip()
    course_name = str(course.get("nickname") or course.get("name") or course.get("course_name") or "").strip()
    return course_id, course_name


_ATTENTION_ACTIONS = {
    "evidence_not_acquired": "Refresh this course with refresh_mirror, then retry discovery; other courses are unaffected.",
    "evidence_membership_incomplete": "Saved evidence for this item is partial, so counts are observed only. Refresh this course to complete it.",
    "evidence_update_required": "Update Canvas Expert on this computer; this course was saved by a newer version.",
    "evidence_index_pending": "Saved evidence is waiting for local indexing and updates automatically; retry shortly.",
    "scoring_resume_unavailable": "Existing scoring sessions could not be checked. Do not assume there is no open session before preparing.",
}


def _attention(course_id, course_name, code, *, assignment_id=None, retryable=True) -> dict:
    return {
        "course_id": course_id, "course_name": course_name, "code": code,
        "retryable": retryable, "operation_id": None, "refresh_status": None,
        "user_action": _ATTENTION_ACTIONS[code], "assignment_id": assignment_id,
    }


def _session_index(actionable_sessions) -> dict[tuple[str, str], dict]:
    indexed = {}
    for summary in actionable_sessions or ():
        if not isinstance(summary, dict):
            continue
        if summary.get("session_kind") and str(summary["session_kind"]) != "scoring_assignment":
            continue
        if str(summary.get("status") or "") not in _ACTIONABLE_STATUSES:
            continue
        key = (str(summary.get("course_id") or ""), str(summary.get("assignment_id") or ""))
        if all(key) and key not in indexed:
            indexed[key] = summary
    return indexed


def _tables(attention_rows, freshness_rows):
    return {
        "attention": {"columns": list(ATTENTION_COLUMNS), "rows": [
            [row.get(column) for column in ATTENTION_COLUMNS] for row in attention_rows
        ]},
        "freshness": {"columns": list(DISCOVERY_FRESHNESS_COLUMNS), "rows": [
            [row.get(column) for column in DISCOVERY_FRESHNESS_COLUMNS] for row in freshness_rows
        ]},
    }


def _parse(value):
    from .scoring_local import _parse_timestamp
    return _parse_timestamp(value)


def _freshness_row(course_id, course_name, record, *, available, coverage, now, holidays) -> dict:
    """Course age from the oldest *available* observation; unknown stays unknown."""
    from api import freshness_policy

    stamps = [record["roster"]["observed_at"], record["assignments_scope"]["observed_at"],
              *(item["submissions_observed_at"] for item in record["assignments"])] if record else []
    parsed = [stamp for stamp in map(_parse, stamps) if stamp is not None]
    oldest = min(parsed) if parsed else None
    synced_at = oldest.isoformat().replace("+00:00", "Z") if oldest else ""
    envelope = freshness_policy.freshness_envelope(
        "mirror", "gradebook_snapshot", "current" if available and oldest else "unavailable",
        synced_at, now=now, holidays=holidays)
    return {
        "course_id": course_id, "course_name": course_name, **envelope,
        "last_success_at": synced_at,
        "age_minutes": envelope["age_minutes"] if oldest else None,
        "requires_teacher_confirmation": bool(
            available and oldest and envelope["state"] == "stale"),
        "coverage": coverage,
    }


def discover_scoring_work(current_courses, *, evidence, actionable_sessions=(),
                          resume_incomplete=False, course_errors=None, tier_tags=None,
                          registrations_by_course=None, now=None, holidays=None) -> dict:
    """Project one pinned evidence-index read into a student-free grading digest.

    ``evidence`` is ``EvidenceQueryService.read_scoring_discovery`` output;
    ``course_errors`` maps a course ID to a scoped attention code the caller
    already established (update required, index pending). Nothing here reads
    Canvas, refreshes, or opens private storage.
    """
    now = now or datetime.now(timezone.utc)
    courses = list(current_courses or [])
    if not courses:
        return {"ok": False, "code": "no_current_courses", "stage": "discover",
                "retryable": False,
                "user_action": "Configure at least one Current course, then retry discovery.",
                "error": "No Current courses are configured."}

    records = {str(item.get("course_id")): item for item in (evidence or {}).get("courses", ())}
    course_errors = course_errors or {}
    revision = (evidence or {}).get("revision")
    sessions = _session_index(actionable_sessions)
    attention_rows, freshness_rows, assignments = [], [], []
    totals = {"courses_checked": len(courses), "courses_usable": 0, "assignments": 0,
              "ungraded": 0, "partially_scored": 0, "late_ungraded": 0,
              "counts_complete": True, "assignments_with_unknown_counts": 0}

    for course in courses:
        course_id, course_name = _course_identity(course)
        record = records.get(course_id)
        name = course_name or str((record or {}).get("title") or "")
        error = course_errors.get(course_id)
        usable = bool(record) and not error and record["assignments_scope"]["coverage"] != "unknown"
        course_complete = usable
        if error:
            attention_rows.append(_attention(course_id, name, error, retryable=error != "evidence_update_required"))
        elif not usable:
            attention_rows.append(_attention(course_id, name, "evidence_not_acquired"))
        else:
            totals["courses_usable"] += 1
            if record["assignments_scope"]["coverage"] == "incomplete":
                attention_rows.append(_attention(course_id, name, "evidence_membership_incomplete"))
                course_complete = False
            if record["roster"]["coverage"] == "unknown":
                attention_rows.append(_attention(course_id, name, "evidence_not_acquired"))
                course_complete = False
            elif record["roster"]["coverage"] == "incomplete":
                attention_rows.append(_attention(course_id, name, "evidence_membership_incomplete"))
                course_complete = False

        if usable:
            items = record["assignments"]
            registrations = (registrations_by_course or {}).get(course_id, ())
            advisory = _family_advisory(
                [{"id": item["assignment_id"], "name": item["name"]} for item in items],
                tier_tags=tier_tags, registrations=registrations)
            for item in items:
                ungraded = item["ungraded"] or 0
                partially = item["partially_scored"] or 0
                late_ungraded = item["late_ungraded"] or 0
                known = item["ungraded"] is not None
                if known and ungraded <= 0 and partially <= 0 and item["counts_complete"]:
                    continue  # a complete, genuinely empty queue
                if not item["counts_complete"]:
                    course_complete = False
                    totals["counts_complete"] = False
                    if not known:
                        totals["assignments_with_unknown_counts"] += 1
                    if item["coverage"] == "incomplete":
                        attention_rows.append(_attention(
                            course_id, name, "evidence_membership_incomplete",
                            assignment_id=item["assignment_id"]))
                    elif record["roster"]["coverage"] != "unknown":
                        attention_rows.append(_attention(
                            course_id, name, "evidence_not_acquired",
                            assignment_id=item["assignment_id"]))
                session = sessions.get((course_id, item["assignment_id"]))
                assignments.append({
                    "course_id": course_id, "course_name": name,
                    "assignment_id": item["assignment_id"], "assignment_name": item["name"],
                    "due_at": item["due_at"] or None, "points": item["points"],
                    "ungraded": item["ungraded"], "partially_scored": item["partially_scored"],
                    "late_ungraded": item["late_ungraded"],
                    "resumable_status": session.get("status") if session else None,
                    "scoring_session_id": (session.get("session_id") or session.get("scoring_session_id")) if session else None,
                    "mirror_revision": revision,
                    **advisory.get(item["assignment_id"], {
                        "family_title": None, "family_role": None,
                        "family_link_state": None, "bridge_assignment_id": None,
                        "bridge_status": None, "bridge_required": False,
                        "next_step": None}),
                    "coverage": item["coverage"], "counts_complete": item["counts_complete"],
                })
                totals["assignments"] += 1
                totals["ungraded"] += ungraded
                totals["partially_scored"] += partially
                totals["late_ungraded"] += late_ungraded
        else:
            totals["counts_complete"] = False
        coverage = "complete" if course_complete else ("unknown" if not usable else "incomplete")
        freshness_rows.append(_freshness_row(course_id, name, record, available=usable,
                                             coverage=coverage, now=now, holidays=holidays))

    if resume_incomplete:
        attention_rows.append(_attention("", "", "scoring_resume_unavailable"))

    if not totals["courses_usable"]:
        result = {"ok": False, "code": "scoring_discovery_failed", "stage": "discover",
                  "retryable": True,
                  "user_action": "No Current course has saved evidence yet; see the attention rows for the specific recovery.",
                  "error": "Scoring discovery could not read saved evidence for any Current course."}
        result.update(_tables(attention_rows, freshness_rows))
        return result

    order = {_course_identity(course)[0]: index for index, course in enumerate(courses)}
    assignments.sort(key=lambda row: (order.get(row["course_id"], len(courses)),
                                      1 if not row["due_at"] else 0,
                                      str(row["due_at"] or ""),
                                      row["assignment_name"].casefold(), row["assignment_id"]))
    if attention_rows:
        status = "partial"
    else:
        status = "ready" if assignments else "nothing_to_grade"
    result = {"ok": True, "status": status,
              "assignments": {"columns": list(ASSIGNMENT_COLUMNS),
                              "rows": [[row.get(column) for column in ASSIGNMENT_COLUMNS] for row in assignments]},
              "totals": totals}
    result.update(_tables(attention_rows, freshness_rows))
    return result
