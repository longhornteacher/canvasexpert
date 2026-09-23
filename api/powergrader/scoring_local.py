"""Local-only CanvasMirror input for Scoring Sessions.

This adapter deliberately bypasses the ordinary mirror serve-age policy.  The
scoring owner applies a teacher-friendly local-time freshness advisory while
still requiring every private projection to be current and structurally usable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from api import gradebook_snapshot
from api import freshness_policy
from api.mirror import read_service


FRESHNESS_COLUMNS = (
    # Preserve the original leading table columns for existing host renderers;
    # richer policy metadata is appended so consumers can migrate additively.
    "course_id", "course_name", "state", "last_success_at", "age_minutes",
    "requires_teacher_confirmation", "source", "section", "synced_at",
    "within_policy", "policy_window_minutes", "school_hours",
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


def freshness_limit_minutes(now: datetime, *, holidays=None) -> int:
    """Return the advisory threshold for the local Chicago clock."""
    return freshness_policy.policy_window_minutes(now, holidays)[0]


def _freshness(course_id: str, course_name: str, scopes: list[dict], *, now=None,
               holidays=None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    timestamps = [
        _parse_timestamp(scope.get("last_success_at"))
        for scope in scopes
        if isinstance(scope, dict)
    ]
    valid_timestamps = [stamp for stamp in timestamps if stamp is not None]
    all_available = all(
        isinstance(scope, dict)
        and scope.get("state") in {"current", "stale"}
        for scope in scopes
    )
    usable_timestamps = len(valid_timestamps) == len(scopes) and bool(scopes)
    has_stale_scope = any(scope.get("state") == "stale" for scope in scopes
                          if isinstance(scope, dict))
    oldest = min(valid_timestamps) if usable_timestamps else None
    projection_state = "current" if all_available and usable_timestamps else "unavailable"
    synced_at = oldest.isoformat().replace("+00:00", "Z") if oldest else ""
    envelope = freshness_policy.freshness_envelope(
        "mirror", "gradebook_snapshot",
        ("stale" if has_stale_scope and projection_state == "current" else projection_state),
        synced_at, now=now,
        holidays=holidays,
    )
    return {
        "course_id": str(course_id),
        "course_name": str(course_name or ""),
        **envelope,
        "last_success_at": synced_at,
        "projection_state": projection_state,
        "requires_teacher_confirmation": bool(
            projection_state == "current" and not envelope["within_policy"]
        ),
    }


def load_scoring_snapshot(course_id: str, *, course_name: str = "", root=None,
                          now=None) -> dict:
    """Return a local gradebook snapshot plus the student-free freshness record.

    No Canvas fallback, refresh enqueue, or coordinator interaction is allowed
    here.  The result contains ``snapshot`` only when all three required scopes
    and their records can be used by the existing gradebook snapshot builder.
    """
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
    if freshness.get("projection_state") != "current":
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}
    if any(not isinstance(scope.get("records"), list) for scope in scopes):
        freshness["state"] = "unavailable"
        freshness["projection_state"] = "unavailable"
        freshness["within_policy"] = False
        freshness["requires_teacher_confirmation"] = False
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}

    try:
        snapshot = gradebook_snapshot.build_snapshot(
            scopes[0]["records"], scopes[1]["records"], scopes[2]["records"],
        )
    except Exception:
        freshness["state"] = "unavailable"
        freshness["projection_state"] = "unavailable"
        freshness["within_policy"] = False
        freshness["requires_teacher_confirmation"] = False
        return {"snapshot": None, "freshness": freshness,
                "error": "mirror_projection_unavailable"}

    revisions = {int(scope.get("mirror_revision") or 0) for scope in scopes}
    snapshots = {str(scope.get("snapshot_id") or "") for scope in scopes
                 if str(scope.get("snapshot_id") or "")}
    if len(revisions) > 1 or len(snapshots) > 1:
        freshness["state"] = "unavailable"
        freshness["projection_state"] = "unavailable"
        freshness["within_policy"] = False
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
