"""Aggregate roster-warning discovery with no roster-route mutation."""

from __future__ import annotations

from collections import Counter

from api import roster_service
from api.identity_vault_service import open_vault
from api.platform_services import config
from api.platform_services import workspace

from . import WorkCourseReads, check_deadline, finding, text


_WARNING_CODES = {"missing_pseudonym", "extra_time_without_days"}


def _vault_context() -> dict:
    try:
        private_root = workspace.identity_vault_dir()
        entries = open_vault().entries() if private_root else []
    except Exception:
        entries = []
    return {text(item.get("canvas_id")): item for item in entries if isinstance(item, dict)}


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
    vault_by_id = _vault_context()

    counts = Counter()
    for user in users:
        if not isinstance(user, dict) or user.get("id") is None:
            continue
        user_id = str(user.get("id"))
        student = {
            "id": user_id,
            "extra_time": extra_time.get(user_id, {"enabled": False, "days": 0}),
        }
        warnings = roster_service.compute_warnings(student, vault_by_id)
        counts.update(code for code in warnings if code in _WARNING_CODES)

    output = []
    for warning_code in sorted(counts):
        amount = counts[warning_code]
        output.append(finding(
            kind="roster.warning",
            course_id=str(course_id),
            counts={"total": amount, "pending": amount, "affected": amount},
            now=now,
            title="Roster warning",
            resumable_url=f"/names?course_id={course_id}",
            source_suffix=warning_code,
        ))
    return output


__all__ = ["scan_course"]
