"""Cross-course, student-free discovery for MCP Scoring Sessions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .scoring_local import FRESHNESS_COLUMNS


ASSIGNMENT_COLUMNS = (
    "course_id", "course_name", "assignment_id", "assignment_name", "due_at",
    "points", "ungraded", "partially_scored", "late_ungraded",
    "resumable_status", "scoring_session_id", "mirror_revision",
    "family_title", "family_role", "family_link_state",
)
ATTENTION_COLUMNS = (
    "course_id", "course_name", "code", "retryable", "operation_id",
    "refresh_status", "user_action",
)
_ACTIONABLE_STATUSES = frozenset({"ready", "needs_teacher_input", "staged"})


def _family_advisory(assignments: list[dict], *, tier_tags=None, registrations=()):
    """Classify snapshot rows without a Canvas read or student data."""
    from api.operation_ledger.adapters import differentiated_bridge

    registrations = [row for row in (registrations or ()) if isinstance(row, dict)]
    by_id = {}
    for registration in registrations:
        for key in ("source_assignment_ids", "bridge_assignment_id"):
            values = registration.get(key) if key == "source_assignment_ids" else [registration.get(key)]
            for value in values or []:
                if str(value or "").strip():
                    by_id[str(value)] = registration
    bases = {}
    for row in assignments:
        if not isinstance(row, dict):
            continue
        base, tag = differentiated_bridge.title_tag_parts(row.get("name"), tier_tags)
        if tag:
            bases.setdefault(base.casefold(), {"title": base, "sources": []})["sources"].append(str(row.get("id") or ""))
    result = {}
    for row in assignments:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "")
        registration = by_id.get(aid)
        base, tag = differentiated_bridge.title_tag_parts(row.get("name"), tier_tags)
        bridge_named = str(row.get("name") or "").strip().casefold().endswith(" - bridge")
        family_title = str((registration or {}).get("family_title") or base).strip() if (tag or registration or bridge_named) else None
        role = "bridge" if (registration and aid == str(registration.get("bridge_assignment_id") or "")) or bridge_named else ("source" if tag else None)
        family = bases.get(base.casefold())
        source_count = len(family.get("sources") or []) if family else 0
        state = "linked" if registration else ("needs_repair" if source_count >= 2 and role in {"source", "bridge"} else None)
        result[aid] = {"family_title": family_title, "family_role": role, "family_link_state": state}
    return result


def _course_identity(course: dict) -> tuple[str, str]:
    course_id = str(course.get("id") or course.get("course_id") or "").strip()
    course_name = str(course.get("nickname") or course.get("name") or course.get("course_name") or "").strip()
    return course_id, course_name


def _attention(course_id: str, course_name: str, code: str, *, operation_id=None,
               refresh_status=None) -> dict:
    return {
        "course_id": course_id, "course_name": course_name, "code": code,
        "retryable": True, "operation_id": operation_id,
        "refresh_status": refresh_status,
        "user_action": "Refresh the Current course mirror, then retry discovery; other usable courses remain available.",
    }


def _snapshot_revision(snapshot: dict) -> int:
    for key in ("mirror_revision", "revision"):
        if snapshot.get(key) not in (None, ""):
            try:
                return int(snapshot[key])
            except (TypeError, ValueError):
                pass
    return 0


def _read_course(course: dict, load_snapshot) -> dict:
    course_id, course_name = _course_identity(course)
    try:
        try:
            loaded = load_snapshot(course_id, course_name=course_name)
        except TypeError:
            loaded = load_snapshot(course_id)
    except Exception:
        return {"ok": False, "attention": _attention(course_id, course_name, "mirror_projection_unavailable")}

    freshness = None
    if isinstance(loaded, dict) and "snapshot" in loaded:
        snapshot = loaded.get("snapshot")
        freshness = loaded.get("freshness")
        if loaded.get("error") or not isinstance(snapshot, dict):
            return {"ok": False, "attention": _attention(course_id, course_name, "mirror_projection_unavailable"), "freshness": freshness or {}}
    elif isinstance(loaded, tuple):
        snapshot, error = (loaded + (None, None))[:2]
        if error or not isinstance(snapshot, dict):
            return {"ok": False, "attention": _attention(course_id, course_name, "mirror_projection_unavailable"), "freshness": freshness or {}}
    else:
        snapshot = loaded
        if not isinstance(snapshot, dict) or snapshot.get("ok") is False:
            return {"ok": False, "attention": _attention(course_id, course_name, "mirror_projection_unavailable"), "freshness": freshness or {}}

    freshness = dict(freshness or {
        "course_id": course_id, "course_name": course_name, "state": "current",
        "last_success_at": "", "age_minutes": 0,
        "requires_teacher_confirmation": False,
    })
    freshness.update({"course_id": course_id, "course_name": course_name})
    return {"ok": True, "course_id": course_id, "course_name": course_name,
            "snapshot": snapshot, "mirror_revision": _snapshot_revision(snapshot),
            "freshness": freshness}


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
        "freshness": {"columns": list(FRESHNESS_COLUMNS), "rows": [
            [row.get(column) for column in FRESHNESS_COLUMNS] for row in freshness_rows
        ]},
    }


def discover_scoring_work(current_courses, *, load_snapshot,
                          actionable_sessions=(), tier_tags=None,
                          registrations_by_course=None) -> dict:
    """Read every Current course locally and project qualifying work."""
    courses = list(current_courses or [])
    if not courses:
        return {"ok": False, "code": "no_current_courses", "stage": "discover",
                "retryable": False,
                "user_action": "Configure at least one Current course, then retry discovery.",
                "error": "No Current courses are configured."}

    worker_count = min(3, len(courses))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda course: _read_course(course, load_snapshot), courses))
    usable = [result for result in results if result.get("ok")]
    attention_rows = [result["attention"] for result in results if not result.get("ok")]
    freshness_rows = []
    for result, course in zip(results, courses):
        freshness_rows.append(result.get("freshness") or {
            "course_id": _course_identity(course)[0], "course_name": _course_identity(course)[1],
            "state": "unavailable", "last_success_at": "", "age_minutes": 0,
            "requires_teacher_confirmation": False,
        })

    if not usable:
        result = {"ok": False, "code": "scoring_discovery_failed", "stage": "discover",
                  "retryable": True,
                  "user_action": "Refresh the Current course mirror, then retry discovery.",
                  "error": "Scoring discovery could not read any Current-course mirror projection.",
                  "attention": {"columns": list(ATTENTION_COLUMNS), "rows": [
                      [row.get(column) for column in ATTENTION_COLUMNS] for row in attention_rows
                  ]}}
        result.update(_tables(attention_rows, freshness_rows))
        return result

    sessions = _session_index(actionable_sessions)
    assignments = []
    totals = {"courses_checked": len(courses), "courses_usable": len(usable),
              "assignments": 0, "ungraded": 0, "partially_scored": 0, "late_ungraded": 0}
    for result in usable:
        snapshot = result["snapshot"]
        snapshot_assignments = [item for item in (snapshot.get("assignments") or []) if isinstance(item, dict)]
        registrations = (registrations_by_course or {}).get(result["course_id"], ())
        advisory = _family_advisory(snapshot_assignments, tier_tags=tier_tags, registrations=registrations)
        for assignment in snapshot_assignments:
            ungraded = int(assignment.get("ungraded") or 0)
            partially = int(assignment.get("partially_scored") or 0)
            late_ungraded = int(assignment.get("late_ungraded") or 0)
            if ungraded <= 0 and partially <= 0:
                continue
            course_id = result["course_id"]
            assignment_id = str(assignment.get("id") or "")
            session = sessions.get((course_id, assignment_id))
            assignments.append({
                "course_id": course_id, "course_name": result["course_name"],
                "assignment_id": assignment_id,
                "assignment_name": str(assignment.get("name") or assignment.get("title") or ""),
                "due_at": assignment.get("due_at") or None,
                "points": assignment.get("points", assignment.get("points_possible")),
                "ungraded": ungraded, "partially_scored": partially,
                "late_ungraded": late_ungraded,
                "resumable_status": session.get("status") if session else None,
                "scoring_session_id": (session.get("session_id") or session.get("scoring_session_id")) if session else None,
                "mirror_revision": result["mirror_revision"],
                **advisory.get(assignment_id, {"family_title": None, "family_role": None, "family_link_state": None}),
            })
            totals["assignments"] += 1
            totals["ungraded"] += ungraded
            totals["partially_scored"] += partially
            totals["late_ungraded"] += late_ungraded

    order = {str(_course_identity(course)[0]): index for index, course in enumerate(courses)}
    assignments.sort(key=lambda row: (order.get(row["course_id"], len(courses)),
                                      1 if not row["due_at"] else 0,
                                      str(row["due_at"] or ""),
                                      row["assignment_name"].casefold(), row["assignment_id"]))
    result = {"ok": True,
              "status": "partial" if attention_rows else ("ready" if assignments else "nothing_to_grade"),
              "assignments": {"columns": list(ASSIGNMENT_COLUMNS),
                              "rows": [[row.get(column) for column in ASSIGNMENT_COLUMNS] for row in assignments]},
              "totals": totals}
    result.update(_tables(attention_rows, freshness_rows))
    return result
