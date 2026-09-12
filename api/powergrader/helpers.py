"""Focused helper functions for private Scoring Session orchestration."""


def build_start_error_payload(
    error: str,
    *,
    privacy_steps: list[dict] | None = None,
    budget: dict | None = None,
    debug_path: str | None = None,
) -> dict:
    payload = {
        "ok": False,
        "error": error,
        "privacy_steps": privacy_steps or [],
    }
    if budget:
        payload["budget"] = budget
    if debug_path:
        payload["debug_path"] = debug_path
    return payload


def build_start_success_payload(
    *,
    session_id: str,
    students: list[dict],
    assignment_name: str,
    mode: str,
    mode_label: str,
    ai_by_uid: dict,
    privacy_steps: list[dict],
    privacy_artifacts: dict,
    copilot_packet: dict | None,
    evidence_status: str = "unknown",
    auto_post_summary: dict | None = None,
) -> dict:
    payload = {
        "ok": True,
        "session_id": session_id,
        "student_count": len(students),
        "assignment_name": assignment_name,
        "mode": mode,
        "mode_label": mode_label,
        "ai_scored": len(ai_by_uid),
        "privacy_steps": privacy_steps,
        "evidence_status": evidence_status,
    }
    if auto_post_summary is not None:
        payload["auto_post_summary"] = auto_post_summary
    return payload


def build_late_preview_students(new_subs: list[dict]) -> list[dict]:
    return [
        {
            "user_id": str(sub.get("user_id", "")),
            "name": (sub.get("user") or {}).get("name")
            or (sub.get("user") or {}).get("sortable_name")
            or str(sub.get("user_id", "")),
            "submitted_at": sub.get("submitted_at") or "",
        }
        for sub in new_subs
    ]


def build_late_preview_payload(new_subs: list[dict]) -> dict:
    message = f"{len(new_subs)} new late submission(s) found."
    return {
        "ok": True,
        "new_count": len(new_subs),
        "students": build_late_preview_students(new_subs),
        "message": message,
    }
