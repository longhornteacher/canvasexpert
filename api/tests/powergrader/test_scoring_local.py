from datetime import datetime, timedelta, timezone

from api.powergrader import scoring_local


def _freshness(now, age_minutes, holidays=()):
    stamp = now - timedelta(minutes=age_minutes)
    return scoring_local._freshness(
        "course", "Course", [{
            "state": "current",
            "last_success_at": stamp.isoformat().replace("+00:00", "Z"),
        }], now=now, holidays=list(holidays),
    )


def test_school_hours_accept_under_60_minutes_but_prompt_at_one_hour():
    now = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)  # 08:00 Chicago
    assert _freshness(now, 59)["requires_teacher_confirmation"] is False
    assert _freshness(now, 60)["requires_teacher_confirmation"] is True
    assert _freshness(now, 61)["requires_teacher_confirmation"] is True


def test_outside_school_hours_accept_600_minutes_but_prompt_afterward():
    now = datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc)  # 17:00 Chicago
    assert _freshness(now, 599)["requires_teacher_confirmation"] is False
    assert _freshness(now, 600)["requires_teacher_confirmation"] is True
    assert _freshness(now, 601)["requires_teacher_confirmation"] is True


def test_weekend_uses_outside_hours_threshold():
    now = datetime(2026, 9, 26, 16, 0, tzinfo=timezone.utc)  # Saturday morning
    assert scoring_local.freshness_limit_minutes(now) == 600


def test_chicago_dst_transitions_keep_school_hours_local():
    before_fall = datetime(2026, 10, 30, 13, 0, tzinfo=timezone.utc)
    after_fall = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    after_spring = datetime(2026, 3, 9, 14, 0, tzinfo=timezone.utc)
    assert scoring_local.freshness_limit_minutes(before_fall, holidays=[]) == 60
    assert scoring_local.freshness_limit_minutes(after_fall, holidays=[]) == 60
    assert scoring_local.freshness_limit_minutes(after_spring, holidays=[]) == 60


def test_configured_holiday_uses_outside_hours_window_during_normal_school_hours():
    now = datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc)  # Monday 08:00 Chicago
    holiday = ["2026-09-21"]
    assert scoring_local.freshness_limit_minutes(now, holidays=holiday) == 600
    result = _freshness(now, 540, holidays=holiday)
    assert result["school_hours"] is False
    assert result["within_policy"] is True
    assert result["requires_teacher_confirmation"] is False
