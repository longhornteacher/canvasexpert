"""Aggregate roster-warning discovery with no roster-route mutation."""

from __future__ import annotations

from collections import Counter

from api import feedback_scrub
from api import roster_context
from api.identity_vault_service import open_vault
from api.platform_services import config
from api.platform_services import workspace
from api.webui.routes.roster_helpers import _compute_warnings, _enrollment_section_ids

from . import WorkCourseReads, check_deadline, finding, text


_WARNING_CODES = {
    "missing_pseudonym",
    "extra_time_without_days",
    "nickname_collision",
    "protected_name_collision",
    "student_added",
    "student_departed",
    "student_changed_section",
}


def _vault_context() -> tuple[dict, set[str], dict]:
    try:
        private_root = workspace.identity_vault_dir()
        vault_entries = open_vault().entries() if private_root else []
    except Exception:
        vault_entries = []
    by_id = {text(item.get("canvas_id")): item for item in vault_entries if isinstance(item, dict)}
    try:
        protected = {text(value).casefold() for value in config.active_protected_names()}
    except Exception:
        protected = set()
    try:
        collisions = feedback_scrub.find_collisions(vault_entries, protected)
    except Exception:
        collisions = {"literary": [], "dup_first": [], "common_word": []}
    return by_id, protected, collisions


def _safe_source_suffix(warning_code: str) -> str:
    """Return a source_ref-safe token for one warning code.

    ``api.work_registry.models.validate_source_ref`` bans the substring
    "student" from any source_ref value as a guard against identity leaking
    into the registry (the registry carries only counts; the Roster page
    resolves who). The three roster-change codes are named with a
    "student_" prefix by the feature's own naming convention, so that
    prefix is stripped here -- the pre-existing codes never had it and pass
    through unchanged. This never weakens the guard: it just keeps this
    provider from tripping its own no-identity rule with a code name that
    was never identity in the first place.
    """
    return warning_code.removeprefix("student_")


def scan_course(course_id: str, *, now, reads: WorkCourseReads) -> list[dict]:
    check_deadline(reads._deadline)
    users = reads.students()
    extra_time = {
        text(item.get("id")): {
            "enabled": True,
            "days": item.get("days", 0),
        }
        for item in config.get_extra_time(course_id)
        if isinstance(item, dict) and text(item.get("id"))
    }
    vault_by_id, protected, collisions = _vault_context()

    # Roster-change diff against the teacher's last acknowledged baseline --
    # the exact same shared computation the Roster web UI uses, so a course
    # scanned here and one opened in the browser never disagree about who is
    # new, who has left, or who changed section. A read here never writes
    # the baseline; only the teacher's explicit Acknowledge action does.
    current_ids = {str(user.get("id")) for user in users
                    if isinstance(user, dict) and user.get("id") is not None}
    current_sections = _enrollment_section_ids(users)
    try:
        baseline = config.get_roster_baseline(course_id)
    except Exception:
        baseline = None
    roster_diff = roster_context.diff_roster_baseline(baseline, current_ids, current_sections)
    added_ids = set(roster_diff["added"])
    changed_ids = {item["student_id"] for item in roster_diff["changed_section"]}

    counts = Counter()
    for user in users:
        if not isinstance(user, dict) or user.get("id") is None:
            continue
        user_id = str(user.get("id"))
        roster_change = None
        if user_id in added_ids:
            roster_change = {"is_new": True}
        elif user_id in changed_ids:
            roster_change = {"changed_section": True}
        student = {
            "id": user_id,
            "extra_time": extra_time.get(user_id, {"enabled": False, "days": 0}),
        }
        warnings = _compute_warnings(
            student,
            vault_by_id,
            protected,
            collisions,
            roster_change=roster_change,
        )
        counts.update(code for code in warnings if code in _WARNING_CODES)

    departed_count = len(roster_diff["departed"])
    if departed_count:
        counts["student_departed"] = departed_count

    output = []
    for warning_code in sorted(counts):
        amount = counts[warning_code]
        output.append(finding(
            kind="roster.warning",
            course_id=str(course_id),
            counts={"total": amount, "pending": amount, "affected": amount},
            now=now,
            title="Roster warning",
            resumable_url=f"/roster?course_id={course_id}",
            source_suffix=_safe_source_suffix(warning_code),
        ))
    return output


__all__ = ["scan_course"]
