"""Cross-course, student-free discovery for MCP Scoring Sessions.

Discovery is deliberately separate from assignment preparation.  It refreshes
each configured Current course, reads only the resulting local gradebook
projection, and returns enough aggregate information for a teacher to choose
the exact assignment(s) to prepare next.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


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
_ACTIONABLE_STATUSES = frozenset({"ready", "needs_teacher_input"})
_REFRESHING_STATES = frozenset({"queued", "running"})


def _family_advisory(assignments: list[dict], *, tier_tags=None, registrations=()):
    """Classify snapshot rows without a Canvas read or student data."""
    from api.operation_ledger.adapters import differentiated_bridge

    tags = tier_tags
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
        base, tag = differentiated_bridge.title_tag_parts(row.get("name"), tags)
        if tag:
            bases.setdefault(base.casefold(), {"title": base, "sources": []})["sources"].append(str(row.get("id") or ""))
    result = {}
    for row in assignments:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "")
        registration = by_id.get(aid)
        base, tag = differentiated_bridge.title_tag_parts(row.get("name"), tags)
        bridge_named = str(row.get("name") or "").strip().casefold().endswith(" - bridge")
        family_title = str((registration or {}).get("family_title") or base).strip() if (tag or registration or bridge_named) else None
        role = "bridge" if (registration and aid == str(registration.get("bridge_assignment_id") or "")) or bridge_named else ("source" if tag else None)
        family = bases.get(base.casefold())
        source_count = len(family.get("sources") or []) if family else 0
        state = None
        if registration:
            state = "linked"
        elif source_count >= 2 and role in {"source", "bridge"}:
            state = "needs_repair"
        result[aid] = {
            "family_title": family_title,
            "family_role": role,
            "family_link_state": state,
        }
    return result


def _course_identity(course: dict) -> tuple[str, str]:
    course_id = str(course.get("id") or course.get("course_id") or "").strip()
    course_name = str(
        course.get("nickname") or course.get("name") or course.get("course_name") or ""
    ).strip()
    return course_id, course_name


def _refresh_failed(refresh_result) -> bool:
    if isinstance(refresh_result, dict):
        return not bool(refresh_result.get("ok")) or refresh_result.get("usable") is False
    return not bool(refresh_result)


def _refresh_in_progress(refresh_result) -> bool:
    if not isinstance(refresh_result, dict):
        return False
    state = str(
        refresh_result.get("state") or refresh_result.get("status") or ""
    ).strip().casefold()
    return state in _REFRESHING_STATES or state == "syncing"


def _refresh_state(refresh_result) -> str | None:
    if not isinstance(refresh_result, dict):
        return None
    state = str(
        refresh_result.get("state") or refresh_result.get("status") or ""
    ).strip()
    return state or None


def _refresh_revision(refresh_result, snapshot: dict) -> int:
    if isinstance(refresh_result, dict):
        for key in ("mirror_revision", "revision"):
            if refresh_result.get(key) not in (None, ""):
                try:
                    return int(refresh_result[key])
                except (TypeError, ValueError):
                    break
    for key in ("mirror_revision", "revision"):
        if snapshot.get(key) not in (None, ""):
            try:
                return int(snapshot[key])
            except (TypeError, ValueError):
                break
    return 0


def _attention(course_id: str, course_name: str, code: str, *,
               operation_id: str | None = None,
               refresh_status: str | None = None) -> dict:
    if code == "mirror_refresh_in_progress":
        user_action = (
            "Retry discover_scoring_work automatically; no teacher action is needed."
        )
    else:
        user_action = (
            "Retry discovery for this Current course; other usable courses remain available."
        )
    return {
        "course_id": course_id,
        "course_name": course_name,
        "code": code,
        "retryable": True,
        "operation_id": operation_id,
        "refresh_status": refresh_status,
        "user_action": user_action,
    }


def _read_course(course: dict, refresh_course, load_snapshot) -> dict:
    course_id, course_name = _course_identity(course)
    try:
        refresh_result = refresh_course(course_id)
    except Exception:
        return {"ok": False, "attention": _attention(course_id, course_name, "mirror_refresh_failed")}
    if _refresh_in_progress(refresh_result):
        return {
            "ok": False,
            "attention": _attention(
                course_id,
                course_name,
                "mirror_refresh_in_progress",
                operation_id=(refresh_result.get("operation_id") or
                              refresh_result.get("plan_id")),
                refresh_status=_refresh_state(refresh_result),
            ),
        }
    if _refresh_failed(refresh_result):
        code = "mirror_refresh_failed"
        if isinstance(refresh_result, dict) and refresh_result.get("error_code"):
            candidate = str(refresh_result["error_code"]).strip()
            if candidate and candidate.isidentifier():
                code = candidate
        return {"ok": False, "attention": _attention(course_id, course_name, code)}

    try:
        loaded = load_snapshot(course_id)
    except Exception:
        return {"ok": False, "attention": _attention(course_id, course_name, "snapshot_unavailable")}
    if isinstance(loaded, tuple):
        snapshot, error = (loaded + (None, None))[:2]
        if error or not isinstance(snapshot, dict):
            return {"ok": False, "attention": _attention(course_id, course_name, "snapshot_unavailable")}
    else:
        snapshot = loaded
        if not isinstance(snapshot, dict) or snapshot.get("ok") is False:
            return {"ok": False, "attention": _attention(course_id, course_name, "snapshot_unavailable")}
    return {
        "ok": True,
        "course_id": course_id,
        "course_name": course_name,
        "snapshot": snapshot,
        "mirror_revision": _refresh_revision(refresh_result, snapshot),
    }


def _session_index(actionable_sessions) -> dict[tuple[str, str], dict]:
    """Keep only one current actionable session for each exact scope."""
    indexed = {}
    for summary in actionable_sessions or ():
        if not isinstance(summary, dict):
            continue
        kind = summary.get("session_kind")
        if kind and str(kind) != "scoring_assignment":
            continue
        status = str(summary.get("status") or "")
        if status not in _ACTIONABLE_STATUSES:
            continue
        course_id = str(summary.get("course_id") or "")
        assignment_id = str(summary.get("assignment_id") or "")
        if not course_id or not assignment_id or (course_id, assignment_id) in indexed:
            continue
        indexed[(course_id, assignment_id)] = summary
    return indexed


def discover_scoring_work(
    current_courses,
    *,
    refresh_course,
    load_snapshot,
    actionable_sessions=(),
    tier_tags=None,
    registrations_by_course=None,
) -> dict:
    """Refresh every Current course and project qualifying work without students.

    ``refresh_course`` and ``load_snapshot`` are injected so fixture tests can
    prove ordering and bounded concurrency without a live Canvas client.
    """
    courses = list(current_courses or [])
    if not courses:
        return {
            "ok": False,
            "code": "no_current_courses",
            "stage": "discover",
            "retryable": False,
            "user_action": "Configure at least one Current course, then retry discovery.",
            "error": "No Current courses are configured.",
        }

    worker_count = min(3, len(courses))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(
            lambda course: _read_course(course, refresh_course, load_snapshot), courses
        ))

    usable = [result for result in results if result.get("ok")]
    attention_rows = [result["attention"] for result in results if not result.get("ok")]
    refreshing_rows = [
        row for row in attention_rows
        if row.get("code") == "mirror_refresh_in_progress"
    ]
    if not usable and refreshing_rows and len(refreshing_rows) == len(attention_rows):
        return {
            "ok": True,
            "status": "refreshing",
            "assignments": {"columns": list(ASSIGNMENT_COLUMNS), "rows": []},
            "totals": {
                "courses_checked": len(courses),
                "courses_usable": 0,
                "assignments": 0,
                "ungraded": 0,
                "partially_scored": 0,
                "late_ungraded": 0,
            },
            "attention": {"columns": list(ATTENTION_COLUMNS), "rows": [
                [row.get(column) for column in ATTENTION_COLUMNS]
                for row in attention_rows
            ]},
        }
    if not usable:
        return {
            "ok": False,
            "code": "scoring_discovery_failed",
            "stage": "discover",
            "retryable": True,
            "user_action": "Retry scoring discovery; no Current-course mirror could be read.",
            "error": "Scoring discovery could not refresh any Current course.",
            "attention": {"columns": list(ATTENTION_COLUMNS), "rows": [
                [row.get(column) for column in ATTENTION_COLUMNS] for row in attention_rows
            ]},
        }

    sessions = _session_index(actionable_sessions)
    assignments = []
    totals = {
        "courses_checked": len(courses),
        "courses_usable": len(usable),
        "assignments": 0,
        "ungraded": 0,
        "partially_scored": 0,
        "late_ungraded": 0,
    }
    for result in usable:
        snapshot = result["snapshot"]
        snapshot_assignments = [item for item in (snapshot.get("assignments") or []) if isinstance(item, dict)]
        registrations = (registrations_by_course or {}).get(result["course_id"], ())
        advisory = _family_advisory(snapshot_assignments, tier_tags=tier_tags, registrations=registrations)
        # Classification deliberately precedes the positive-work filter.  It
        # is local advisory context and never authorizes reconciliation writes.
        for assignment in snapshot_assignments:
            if not isinstance(assignment, dict):
                continue
            ungraded = int(assignment.get("ungraded") or 0)
            partially = int(assignment.get("partially_scored") or 0)
            late_ungraded = int(assignment.get("late_ungraded") or 0)
            if ungraded <= 0 and partially <= 0:
                continue
            course_id = result["course_id"]
            assignment_id = str(assignment.get("id") or "")
            session = sessions.get((course_id, assignment_id))
            assignments.append({
                "course_id": course_id,
                "course_name": result["course_name"],
                "assignment_id": assignment_id,
                "assignment_name": str(assignment.get("name") or assignment.get("title") or ""),
                "due_at": assignment.get("due_at") or None,
                "points": assignment.get("points", assignment.get("points_possible")),
                "ungraded": ungraded,
                "partially_scored": partially,
                "late_ungraded": late_ungraded,
                "resumable_status": session.get("status") if session else None,
                "scoring_session_id": (
                    session.get("session_id") or session.get("scoring_session_id")
                ) if session else None,
                "mirror_revision": result["mirror_revision"],
                **advisory.get(assignment_id, {
                    "family_title": None, "family_role": None,
                    "family_link_state": None,
                }),
            })
            totals["assignments"] += 1
            totals["ungraded"] += ungraded
            totals["partially_scored"] += partially
            totals["late_ungraded"] += late_ungraded

    order = {str(_course_identity(course)[0]): index for index, course in enumerate(courses)}
    assignments.sort(key=lambda row: (
        order.get(row["course_id"], len(courses)),
        1 if not row["due_at"] else 0,
        str(row["due_at"] or ""),
        row["assignment_name"].casefold(),
        row["assignment_id"],
    ))
    status = (
        "refreshing" if refreshing_rows else
        ("partial" if attention_rows else ("ready" if assignments else "nothing_to_grade"))
    )
    return {
        "ok": True,
        "status": status,
        "assignments": {
            "columns": list(ASSIGNMENT_COLUMNS),
            "rows": [[row.get(column) for column in ASSIGNMENT_COLUMNS] for row in assignments],
        },
        "totals": totals,
        "attention": {
            "columns": list(ATTENTION_COLUMNS),
            "rows": [[row.get(column) for column in ATTENTION_COLUMNS] for row in attention_rows],
        },
    }
