"""Teacher-facing freshness windows shared by agent read surfaces."""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from api.platform_services import config


LOCAL_TIMEZONE = ZoneInfo("America/Chicago")
SCHOOL_START = time(7, 0)
SCHOOL_END = time(16, 30)
SCHOOL_WINDOW_MINUTES = 60
OUTSIDE_WINDOW_MINUTES = 600


def policy_window_minutes(now: datetime | None = None,
                          holidays: list[str] | None = None) -> tuple[int, bool]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_now = now.astimezone(LOCAL_TIMEZONE)
    configured_holidays = (config.get_late_sweep_holidays()
                           if holidays is None else holidays)
    holiday_dates = {str(value) for value in configured_holidays or ()}
    school_day = local_now.weekday() < 5 and local_now.date().isoformat() not in holiday_dates
    school_hours = bool(school_day and SCHOOL_START <= local_now.time() < SCHOOL_END)
    return (SCHOOL_WINDOW_MINUTES if school_hours else OUTSIDE_WINDOW_MINUTES,
            school_hours)


def freshness_envelope(source: str, section: str, state: str,
                       synced_at: str, *, now: datetime | None = None,
                       holidays: list[str] | None = None) -> dict:
    """Describe age and teacher policy for one local Canvas projection."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    policy_window, school_hours = policy_window_minutes(now, holidays)

    timestamp = None
    try:
        timestamp = datetime.fromisoformat(str(synced_at or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        pass
    if timestamp is not None:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (now - timestamp.astimezone(timezone.utc)).total_seconds())
        age_minutes = int(age_seconds // 60)
        within_policy = age_seconds < policy_window * 60
    else:
        age_minutes = 0
        within_policy = False

    normalized_state = str(state or "unavailable").casefold()
    if normalized_state == "current" and not within_policy:
        normalized_state = "stale"
    return {
        "source": str(source),
        "section": str(section),
        "synced_at": str(synced_at or ""),
        "age_minutes": age_minutes,
        "state": normalized_state,
        "within_policy": within_policy,
        "policy_window_minutes": policy_window,
        "school_hours": school_hours,
    }
