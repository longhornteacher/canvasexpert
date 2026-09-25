"""Pure roster normalization helpers."""

from __future__ import annotations

def _enrollment_section_ids(users: list[dict]) -> dict:
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


def _compute_warnings(student: dict, vault_entries_by_id: dict,
                      protected_names: set[str], collisions: dict,
                      roster_change: dict | None = None) -> list[str]:
    """Return warning string codes for one student row.

    ``roster_change`` is this student's entry (if any) from
    ``api.roster_context.diff_roster_baseline`` against the teacher's last
    acknowledged roster -- ``{"is_new": True}`` or
    ``{"changed_section": <detail>}``. A departed student has no live row to
    attach a warning to, so that code is reported by the caller directly.
    """
    warnings: list[str] = []
    cid = student["id"]

    vault_entry = vault_entries_by_id.get(cid, {})
    pseudo = vault_entry.get("pseudonym", "")
    if not pseudo or pseudo.startswith("S0"):
        warnings.append("missing_pseudonym")

    et = student.get("extra_time", {})
    if et.get("enabled") and not (et.get("days") and int(et.get("days", 0)) > 0):
        warnings.append("extra_time_without_days")

    nicknames = vault_entry.get("nicknames", [])
    for nn in nicknames:
        if nn.lower() in protected_names:
            warnings.append("protected_name_collision")
            break

    if vault_entry:
        if collisions.get("literary"):
            for lit in collisions["literary"]:
                if vault_entry.get("real_name", "").lower() in lit.lower():
                    warnings.append("protected_name_collision")
                    break
        for bucket in ("dup_first", "common_word"):
            for item in collisions.get(bucket, []):
                if vault_entry.get("real_name", "").lower() in item.lower():
                    warnings.append("nickname_collision")
                    break

    if roster_change:
        if roster_change.get("is_new"):
            warnings.append("student_added")
        if roster_change.get("changed_section"):
            warnings.append("student_changed_section")

    return list(dict.fromkeys(warnings))


def _as_int(value, field_name: str) -> tuple[int | None, str | None]:
    """Best-effort int parsing for form JSON values."""
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, f"{field_name} must be an integer."


def _value_name(value: dict | None, user_id: str) -> str:
    """Return a per-user display name from a bulk value payload."""
    if not isinstance(value, dict):
        return ""
    names = value.get("names")
    if isinstance(names, dict):
        return str(names.get(str(user_id), "") or "")
    return str(value.get("name", "") or "")
