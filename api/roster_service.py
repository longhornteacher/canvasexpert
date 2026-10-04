"""Runtime-owned roster reads, identity synchronization, and local student updates."""
from __future__ import annotations

from collections.abc import Callable

from api.platform_services import config


def enrollment_section_ids(users: list[dict]) -> dict:
    """Return {user_id_str: [section_id_str, ...]} from enrollments."""
    result = {}
    for u in (users or []):
        uid = str(u["id"])
        secs = set()
        for enrollment in (u.get("enrollments") or []):
            sec_id = enrollment.get("course_section_id")
            if sec_id:
                secs.add(str(sec_id))
        if secs:
            result[uid] = sorted(secs)
    return result


def compute_warnings(student: dict, vault_entries_by_id: dict) -> list[str]:
    """Warnings the agent can resolve through the roster tools."""
    warnings = []
    pseudo = vault_entries_by_id.get(student["id"], {}).get("pseudonym", "")
    if not pseudo or pseudo.startswith("S0"):
        warnings.append("missing_pseudonym")
    extra_time = student.get("extra_time", {})
    if extra_time.get("enabled") and not (extra_time.get("days") and int(extra_time.get("days", 0)) > 0):
        warnings.append("extra_time_without_days")
    return warnings


def fetch_students(course_id: str, *, canvas_get_all=None) -> tuple[list[dict] | None, str | None]:
    if canvas_get_all is None:
        from api.platform_services.canvas_client import canvas_get_all as default_canvas_get_all
        canvas_get_all = default_canvas_get_all
    return canvas_get_all(
        f"/api/v1/courses/{course_id}/users",
        {"enrollment_type[]": ["student"], "include[]": ["enrollments"], "per_page": 100},
    )


def fetch_sections(course_id: str, *, canvas_get_all=None) -> dict[str, str]:
    if canvas_get_all is None:
        from api.platform_services.canvas_client import canvas_get_all as default_canvas_get_all
        canvas_get_all = default_canvas_get_all
    sections, error = canvas_get_all(
        f"/api/v1/courses/{course_id}/sections", {"per_page": 100}
    )
    if error or not sections:
        return {}
    return {str(section["id"]): section.get("name", f"Section {section['id']}")
            for section in sections}


def upsert_roster(vault, users: list[dict]) -> None:
    """Mutation-only roster upsert; persistence belongs to the caller."""
    remember_identity = getattr(vault, "remember_identity", None)
    if remember_identity is not None:
        # Populate the shared identity set first. Assignment then sees the
        # complete vault-derived collision set, independent of API row order.
        for user in users or []:
            canvas_id = str(user.get("id", ""))
            if not canvas_id:
                continue
            name = user.get("name") or user.get("sortable_name") or ""
            remember_identity(canvas_id, name, str(user.get("sis_user_id") or ""))

    for user in users or []:
        canvas_id = str(user.get("id", ""))
        if not canvas_id:
            continue
        name = user.get("name") or user.get("sortable_name") or ""
        sis_id = str(user.get("sis_user_id") or "")
        vault.get_or_assign(canvas_id, name, sis_id)
        short_name = (user.get("short_name") or "").strip()
        name_tokens = {token.lower() for token in name.split()}
        if (short_name and short_name.lower() != name.lower()
                and short_name.lower() not in name_tokens):
            vault.add_nicknames(canvas_id, [short_name])


def sync_roster_for_course(
    vault,
    course_id: str,
    *,
    canvas_get_all=None,
    fetch_students_override: Callable[[str], tuple[list[dict] | None, str | None]] | None = None,
) -> tuple[list[dict] | None, str | None]:
    """Fetch first, then transactionally upsert one course roster."""
    fetch = fetch_students_override or (lambda cid: fetch_students(cid, canvas_get_all=canvas_get_all))
    users, error = fetch(course_id)
    if error:
        return None, error
    transaction = getattr(vault, "transaction", None)
    if transaction is None:
        upsert_roster(vault, users)
    else:
        with transaction():
            upsert_roster(vault, users)
    return users, None


# Replacement would erase the teacher's scrub-coverage list. Only additive
# nickname changes are allowed through the runtime, even for direct callers.
ALLOWED_STUDENT_PATCH_KEYS = frozenset({
    "add_nicknames", "extra_time", "monitored", "classroom_profile",
})


def _as_int(value, field_name: str) -> tuple[int | None, str | None]:
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, f"{field_name} must be an integer."


def update_student(course_id: str, user_id: str, patch: dict, vault) -> dict:
    """Validate and apply one student's roster update."""
    if not course_id or not user_id:
        return {"ok": False, "error": "course_id and user_id required."}

    data = patch
    if not isinstance(data, dict):
        return {"ok": False, "error": "patch must be a JSON object."}

    unknown = set(data.keys()) - ALLOWED_STUDENT_PATCH_KEYS
    if unknown:
        return {"ok": False, "error": f"Unknown patch keys: {sorted(unknown)}"}

    # Validate every value before opening the vault transaction or touching any
    # external/local setting store.  MCP uses this same callable path and must
    # never leave an earlier field applied when a later field is malformed.
    nicknames = None
    if "add_nicknames" in data:
        nicknames = data["add_nicknames"]
        if not isinstance(nicknames, list) or any(not isinstance(value, str) for value in nicknames):
            return {"ok": False, "error": "add_nicknames must be a list of strings."}

    classroom_profile = None
    if "classroom_profile" in data and data["classroom_profile"] is not None:
        try:
            classroom_profile = config.validate_classroom_profile(data["classroom_profile"])
        except ValueError as error:
            return {"ok": False, "error": str(error)}

    extra_time = None
    if "extra_time" in data:
        extra_time = data["extra_time"]
        if not isinstance(extra_time, dict):
            return {"ok": False, "error": "extra_time must be an object."}
        if extra_time.get("enabled"):
            _, err = _as_int(extra_time.get("days", 0), "extra_time.days")
            if err:
                return {"ok": False, "error": err}

    monitored = None
    if "monitored" in data:
        monitored = data["monitored"]
        if not isinstance(monitored, dict):
            return {"ok": False, "error": "monitored must be an object."}

    with vault.transaction():
        if nicknames is not None:
            vault.add_nicknames(user_id, nicknames)

    if extra_time is not None:
        et = extra_time
        et_list = config.get_extra_time(course_id)
        et_list = [e for e in et_list if e.get("id") != user_id]
        if et.get("enabled"):
            days, _ = _as_int(et.get("days", 0), "extra_time.days")
            et_list.append({
                "id": user_id,
                "name": et.get("name", ""),
                "days": days,
            })
        config.set_extra_time(course_id, et_list)

    if monitored is not None:
        m = monitored
        if m.get("enabled"):
            config.set_monitored_student(
                user_id,
                name=m.get("name", ""),
                note=m.get("note", ""),
            )
        else:
            config.remove_monitored_student(user_id)

    if "classroom_profile" in data:
        config.update_roster_student_settings(
            course_id, user_id, {"classroom_profile": classroom_profile}
        )

    return {"ok": True}
