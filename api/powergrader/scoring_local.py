"""Local-only CanvasMirror input for Scoring Sessions.

This adapter deliberately bypasses the ordinary mirror serve-age policy.  The
scoring owner applies its own small freshness decision (30 minutes) while still
requiring every private projection to be current and structurally usable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from api import gradebook_snapshot
from api.mirror import read_service
from api.platform_services import workspace


SCORING_FRESHNESS_LIMIT_MINUTES = 30
FRESHNESS_COLUMNS = (
    "course_id", "course_name", "state", "last_success_at", "age_minutes",
    "requires_teacher_confirmation",
)


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_minutes(timestamp: datetime, now: datetime) -> int:
    return max(0, int((now - timestamp).total_seconds() // 60))


def _freshness(course_id: str, course_name: str, scopes: list[dict], *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    timestamps = [
        _parse_timestamp(scope.get("last_success_at"))
        for scope in scopes
        if isinstance(scope, dict)
    ]
    valid_timestamps = [stamp for stamp in timestamps if stamp is not None]
    all_current = all(
        isinstance(scope, dict)
        and scope.get("state") == "current"
        and not str(scope.get("error_code") or "").strip()
        for scope in scopes
    )
    usable_timestamps = len(valid_timestamps) == len(scopes) and bool(scopes)
    oldest = min(valid_timestamps) if usable_timestamps else None
    age_minutes = _age_minutes(oldest, now) if oldest else 0
    state = "current" if all_current and usable_timestamps else "unavailable"
    return {
        "course_id": str(course_id),
        "course_name": str(course_name or ""),
        "state": state,
        "last_success_at": oldest.isoformat().replace("+00:00", "Z") if oldest else "",
        "age_minutes": age_minutes,
        "requires_teacher_confirmation": bool(
            state == "current" and age_minutes > SCORING_FRESHNESS_LIMIT_MINUTES
        ),
    }


def load_scoring_snapshot(course_id: str, *, course_name: str = "", root=None,
                          now=None) -> dict:
    """Return a local gradebook snapshot plus the student-free freshness record.

    No Canvas fallback, refresh enqueue, or coordinator interaction is allowed
    here.  The result contains ``snapshot`` only when all three required scopes
    and their records can be used by the existing gradebook snapshot builder.
    """
    root = root or workspace.workspace_root()
    if not root:
        freshness = _freshness(course_id, course_name, [], now=now)
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_workspace_unavailable"}

    try:
        scopes = [
            read_service.private_roster(course_id, root=root, max_age_hours=None),
            read_service.private_assignments(course_id, root=root, max_age_hours=None),
            read_service.private_submissions(course_id, root=root, max_age_hours=None),
        ]
    except Exception:
        freshness = _freshness(course_id, course_name, [], now=now)
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}

    freshness = _freshness(course_id, course_name, scopes, now=now)
    if freshness["state"] != "current":
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}
    if any(not isinstance(scope.get("records"), list) for scope in scopes):
        freshness["state"] = "unavailable"
        freshness["requires_teacher_confirmation"] = False
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}

    try:
        snapshot = gradebook_snapshot.build_snapshot(
            scopes[0]["records"], scopes[1]["records"], scopes[2]["records"],
        )
    except Exception:
        freshness["state"] = "unavailable"
        freshness["requires_teacher_confirmation"] = False
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}

    revisions = {int(scope.get("mirror_revision") or 0) for scope in scopes}
    snapshots = {str(scope.get("snapshot_id") or "") for scope in scopes
                 if str(scope.get("snapshot_id") or "")}
    if len(revisions) > 1 or len(snapshots) > 1:
        freshness["state"] = "unavailable"
        freshness["requires_teacher_confirmation"] = False
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}

    snapshot.update({
        "source": "mirror",
        "synced_at": freshness["last_success_at"],
        "mirror_revision": next(iter(revisions), 0),
        "snapshot_id": next(iter(snapshots), ""),
    })
    return {"snapshot": snapshot, "freshness": freshness, "error": None}
