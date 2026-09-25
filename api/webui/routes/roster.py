"""Roster Console API — unified student-level settings surface.

Prefix: /api/roster

Routes:
    GET  /api/roster          — full roster merge (students + vault + settings)
    POST /api/roster/student  — update one student's settings
    POST /api/roster/bulk     — bulk action on many students

"""
import json
from datetime import datetime

from fastapi import APIRouter, Form, Query
from fastapi.responses import JSONResponse

from api import feedback_scrub
from api import roster_context
from api import roster_service
from api.mirror import store as mirror_store
from api.platform_services import config
from api.platform_services.canvas_client import canvas_get_all
from .names import _vault
from . import roster_changes
from . import roster_updates
from .roster_helpers import (
    _as_int,
    _compute_warnings,
    _enrollment_section_ids,
    _value_name,
)

router = APIRouter(prefix="/api/roster", tags=["roster"])

ALLOWED_STUDENT_PATCH_KEYS = {
    "nicknames", "pseudonym", "regenerate_pseudonym",
    "extra_time", "monitored", "classroom_profile",
}

# Compatibility re-exports for focused Roster tests and existing callers.  The
# root module is the sole shared implementation used by the Web UI and MCP.
SCORE_MATRIX_ID_RE = roster_context.SCORE_MATRIX_ID_RE
SCORE_MATRIX_FIELDS = roster_context.SCORE_MATRIX_FIELDS
SCORE_MATRIX_PATCH_FIELDS = roster_context.SCORE_MATRIX_PATCH_FIELDS
_empty_score_matrix = roster_context._empty_score_matrix
_safe_score_matrix_id = roster_context._safe_score_matrix_id
_finite_score = roster_context._finite_score
_validate_score_matrix_columns = roster_context._validate_score_matrix_columns
_normalize_score_matrix = roster_context._normalize_score_matrix
_apply_score_matrix_patch = roster_context._apply_score_matrix_patch


def _fetch_sections(course_id: str) -> dict:
    return roster_service.fetch_sections(course_id, canvas_get_all=canvas_get_all)


_fetch_students = roster_service.fetch_students
_upsert_roster = roster_service.upsert_roster


def _load_roster_users(course_id: str) -> tuple[list[dict], dict, str | None]:
    """Users + section map, mirror-first with live fallback -- shared by the
    roster GET below and the roster-change acknowledge/migrate actions, so
    every one of them agrees on exactly who is "currently on the roster"."""
    roster_document = mirror_store.read_roster(course_id)
    if roster_document is not None and roster_document.get("state") == "current":
        return list(roster_document["students"].values()), roster_document["sections"], None
    users, err = _fetch_students(course_id)
    if err:
        return [], {}, f"Canvas fetch failed: {err}"
    return users, _fetch_sections(course_id), None


@router.get("")
def roster_get(course_id: str = Query("")):
    """Full roster merge for one course.

    1. Fetch students from Canvas + upsert into vault.
    2. Fetch sections.
    3. Merge vault entries, extra-time, and monitored settings.
    4. Return unified rows with counts and warnings.
    """
    if not course_id:
        return JSONResponse({"ok": False, "error": "course_id required."})

    users, section_map, err = _load_roster_users(course_id)
    if err:
        return JSONResponse({"ok": False, "error": err})

    vault = _vault()
    with vault.transaction():
        _upsert_roster(vault, users)
        vault_entries_list = vault.entries()

    enrollment_secs = _enrollment_section_ids(users)

    # Extra time
    extra_time_list = config.get_extra_time(course_id)
    extra_time_by_id: dict[str, dict] = {}
    for et in extra_time_list:
        extra_time_by_id[et.get("id", "")] = {"enabled": True, "days": et.get("days", 0)}

    # Monitored
    monitored = config.get_monitored_students()

    # Private local Roster context, scoped to this course and student.
    raw_roster_settings = config.get_roster_student_settings(course_id)
    score_matrix = _normalize_score_matrix(config.get_roster_score_matrix(course_id))
    relationships = roster_context.normalize_relationships(
        config.get_roster_relationships(course_id)
    )

    # Roster-change diff against the teacher's last acknowledged baseline.
    # Never written here -- see roster_context.diff_roster_baseline and the
    # /changes/acknowledge route below for why a read must not touch it.
    current_student_ids = {str(u["id"]) for u in (users or []) if u.get("id") is not None}
    roster_diff = roster_context.diff_roster_baseline(
        config.get_roster_baseline(course_id), current_student_ids, enrollment_secs)
    added_ids = set(roster_diff["added"])
    changed_section_by_id = {item["student_id"]: item for item in roster_diff["changed_section"]}

    # Protected names for collision check
    protected_names = {p.lower() for p in config.active_protected_names()}

    # Build vault lookup by canvas_id. This also covers students who have
    # left the roster -- the vault keeps their last-known real name, which
    # is the only display name available for the departed list below.
    vault_by_id: dict[str, dict] = {}
    for ve in vault_entries_list:
        vault_by_id[ve["canvas_id"]] = ve
    name_by_id = {uid: (entry.get("real_name") or uid) for uid, entry in vault_by_id.items()}
    course_ids = {str(u["id"]) for u in (users or [])}
    course_vault_entries = [ve for ve in vault_entries_list
                            if str(ve.get("canvas_id", "")) in course_ids]
    collisions = feedback_scrub.find_collisions(course_vault_entries, protected_names)

    # Build rows
    students_out = []
    name_order_map = {}
    profile_warnings = []
    for u in (users or []):
        uid = str(u["id"])
        sortable = u.get("sortable_name") or u.get("name", "")
        display = u.get("name") or sortable
        short = u.get("short_name") or ""

        # Vault entry
        ve = vault_by_id.get(uid, {})

        # Sections
        sec_ids = enrollment_secs.get(uid, [])
        sections = [{"id": sid, "name": section_map.get(sid, f"Section {sid}")}
                    for sid in sec_ids]

        # Extra time
        et = extra_time_by_id.get(uid, {"enabled": False, "days": 0})

        # Monitored
        mon = monitored.get(uid, {})
        monitored_flag = bool(mon)
        monitored_note = mon.get("note", "") if mon else ""

        # Nicknames from vault
        nicknames = ve.get("nicknames", [])
        local_settings = raw_roster_settings.get(uid, {})
        profile_invalid = False
        try:
            classroom_profile = config.validate_classroom_profile(
                local_settings.get("classroom_profile", config.empty_classroom_profile())
                if isinstance(local_settings, dict) else config.empty_classroom_profile()
            )
        except ValueError:
            classroom_profile = config.empty_classroom_profile()
            profile_invalid = True
            profile_warnings.append(f"Classroom profile for {display} is invalid; showing an empty profile.")

        # Roster-change status for this student: new since the baseline, or
        # still enrolled but in a different section now. Mutually exclusive
        # by construction -- a student can only be "changed section" when
        # they were already in the baseline, which a truly new student never is.
        roster_change = None
        if uid in added_ids:
            roster_change = {"is_new": True}
        else:
            change = changed_section_by_id.get(uid)
            if change:
                roster_change = {"changed_section": roster_changes.changed_section_detail(
                    change, score_matrix=score_matrix, relationships=relationships,
                    section_names=section_map, name_by_id=name_by_id,
                )}

        row = {
            "id": uid,
            "canvas_id": uid,
            "name": sortable,
            "display_name": display,
            "short_name": short,
            "sections": sections,
            "nicknames": nicknames,
            "pseudonym": ve.get("pseudonym", ""),
            "extra_time": et,
            "monitored": {"enabled": monitored_flag, "note": monitored_note},
            "classroom_profile": classroom_profile,
            "roster_change": roster_change,
            "warnings": [],
        }
        row["warnings"] = _compute_warnings(
            row, vault_by_id, protected_names, collisions,
            roster_change=roster_change)
        if profile_invalid:
            row["warnings"].append("classroom_profile_invalid")
        students_out.append(row)
        name_order_map[uid] = (sortable or display).lower()

    students_out.sort(key=lambda s: name_order_map.get(s["id"], s["name"].lower()))

    # Counts
    total = len(students_out)
    extra_time_count = sum(1 for s in students_out if s["extra_time"]["enabled"])
    monitored_count = sum(1 for s in students_out if s["monitored"]["enabled"])
    warning_count = sum(1 for s in students_out if s["warnings"])

    notes = []
    if profile_warnings:
        notes.append(" ".join(profile_warnings))
    note = " ".join(notes)

    # Departed students have no live row above to carry a warning, so they
    # are reported here instead -- with exactly what local data is still
    # held for them, per api/webui/routes/roster_changes.departed_detail.
    departed = [
        roster_changes.departed_detail(
            student_id,
            display_name=name_by_id.get(student_id, student_id),
            roster_student_settings=raw_roster_settings,
            extra_time_by_id=extra_time_by_id,
            monitored=monitored,
            score_matrix=score_matrix,
            relationships=relationships,
            section_names=section_map,
            name_by_id=name_by_id,
        )
        for student_id in roster_diff["departed"]
    ]

    return JSONResponse({
        "ok": True,
        "students": students_out,
        "score_matrix": score_matrix,
        "relationships": relationships,
        "roster_changes": {
            "baseline_set": roster_diff["baseline_set"],
            "added_count": len(roster_diff["added"]),
            "changed_section_count": len(roster_diff["changed_section"]),
            "departed": departed,
        },
        "counts": {
            "total": total,
            "extra_time": extra_time_count,
            "monitored": monitored_count,
            "warnings": warning_count,
        },
        "note": note or None,
    })


@router.post("/student")
def roster_student_update(
    course_id: str = Form(...),
    user_id: str = Form(...),
    patch: str = Form(...),
):
    """Update one student's roster settings.

    Accepted patch fields: nicknames, pseudonym, regenerate_pseudonym,
    extra_time, monitored, classroom_profile.
    """
    return JSONResponse(roster_updates.update_student(
        course_id,
        user_id,
        patch,
        vault_factory=_vault,
        get_extra_time=config.get_extra_time,
        set_extra_time=config.set_extra_time,
        set_monitored_student=config.set_monitored_student,
        remove_monitored_student=config.remove_monitored_student,
        update_roster_student_settings=config.update_roster_student_settings,
        validate_classroom_profile=config.validate_classroom_profile,
        as_int=_as_int,
        allowed_keys=ALLOWED_STUDENT_PATCH_KEYS,
    ))


@router.post("/score-matrix")
def roster_score_matrix_update(
    course_id: str = Form(...),
    patch: str = Form(...),
):
    """Apply one local, current-only score-matrix patch for a course."""
    if not course_id:
        return JSONResponse({"ok": False, "error": "course_id required."})
    try:
        data = json.loads(patch)
    except json.JSONDecodeError as error:
        return JSONResponse({"ok": False, "error": f"Invalid score-matrix patch JSON: {error}"})

    matrix, error = _apply_score_matrix_patch(
        config.get_roster_score_matrix(course_id), data
    )
    if error:
        return JSONResponse({"ok": False, "error": error})
    config.set_roster_score_matrix(course_id, matrix)
    return JSONResponse({"ok": True, "score_matrix": matrix})


@router.post("/relationships")
def roster_relationships_update(
    course_id: str = Form(...),
    section_id: str = Form(...),
    relationships: str = Form(...),
):
    """Replace one section's local-only relationship list after full validation."""
    if not course_id:
        return JSONResponse({"ok": False, "error": "course_id required."})
    try:
        data = json.loads(relationships)
    except json.JSONDecodeError as error:
        return JSONResponse({"ok": False, "error": f"Invalid relationships JSON: {error}"})
    updated, error = roster_context.replace_section_relationships(
        config.get_roster_relationships(course_id), section_id, data
    )
    if error:
        return JSONResponse({"ok": False, "error": error})
    config.set_roster_relationships(course_id, updated)
    return JSONResponse({"ok": True, "relationships": updated})


@router.post("/changes/acknowledge")
def roster_changes_acknowledge(course_id: str = Form(...)):
    """Snapshot the live roster as the new comparison baseline.

    Only runs from this explicit teacher action, never from the GET above --
    acknowledging is the one deliberate step allowed to make the
    added/departed/changed-section diff go quiet again for this course.
    """
    if not course_id:
        return JSONResponse({"ok": False, "error": "course_id required."})
    users, _section_map, err = _load_roster_users(course_id)
    if err:
        return JSONResponse({"ok": False, "error": err})
    current_ids = {str(u["id"]) for u in (users or []) if u.get("id") is not None}
    sections = _enrollment_section_ids(users)
    # Every current id needs an entry, even an empty one for a student with
    # no section -- _enrollment_section_ids omits those, and an id missing
    # from the baseline entirely would look newly added on the next diff.
    current_sections = {uid: sections.get(uid, []) for uid in current_ids}
    baseline = roster_context.build_roster_baseline(
        current_sections, acknowledged_at=datetime.now().isoformat(timespec="seconds"))
    config.set_roster_baseline(course_id, baseline)
    return JSONResponse({"ok": True, "baseline": baseline})


@router.post("/changes/migrate-section")
def roster_changes_migrate_section(course_id: str = Form(...), user_id: str = Form(...)):
    """One-click move of one student's stranded old-section data to their new section."""
    if not course_id or not user_id:
        return JSONResponse({"ok": False, "error": "course_id and user_id required."})
    users, _section_map, err = _load_roster_users(course_id)
    if err:
        return JSONResponse({"ok": False, "error": err})
    current_ids = {str(u["id"]) for u in (users or []) if u.get("id") is not None}
    current_sections = _enrollment_section_ids(users)
    diff = roster_context.diff_roster_baseline(
        config.get_roster_baseline(course_id), current_ids, current_sections)
    result, error = roster_changes.migrate_student_section(
        course_id, user_id, diff,
        get_roster_score_matrix=config.get_roster_score_matrix,
        set_roster_score_matrix=config.set_roster_score_matrix,
        get_roster_relationships=config.get_roster_relationships,
        set_roster_relationships=config.set_roster_relationships,
        get_roster_baseline=config.get_roster_baseline,
        set_roster_baseline=config.set_roster_baseline,
    )
    if error:
        return JSONResponse({"ok": False, "error": error})
    return JSONResponse({"ok": True, "migration": result})


@router.post("/bulk")
def roster_bulk_update(
    course_id: str = Form(...),
    user_ids: str = Form(...),
    action: str = Form(...),
    value: str = Form(""),
):
    """Bulk action on many students.

    Supported actions: set_extra_time, clear_extra_time, set_monitored,
    clear_monitored.
    """
    return JSONResponse(roster_updates.update_bulk(
        course_id,
        user_ids,
        action,
        value,
        get_extra_time=config.get_extra_time,
        set_extra_time=config.set_extra_time,
        set_monitored_student=config.set_monitored_student,
        remove_monitored_student=config.remove_monitored_student,
        as_int=_as_int,
        value_name=_value_name,
    ))
