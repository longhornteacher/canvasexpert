"""Shared roster fetch, section, and vault-upsert use cases."""
from __future__ import annotations

from collections.abc import Callable


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


def compute_warnings(student: dict, vault_entries_by_id: dict,
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
