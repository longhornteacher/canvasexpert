from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api import freshness_policy


CENTRAL = ZoneInfo("America/Chicago")


@pytest.mark.parametrize(
    "label,local_now,age_minutes,holidays,expected_window,expected_school_hours,expected_within",
    [
        ("50 minutes during school", datetime(2026, 9, 23, 10, 0, tzinfo=CENTRAL),
         50, [], 60, True, True),
        ("70 minutes during school", datetime(2026, 9, 23, 10, 0, tzinfo=CENTRAL),
         70, [], 60, True, False),
        ("nine hours outside school", datetime(2026, 9, 23, 21, 0, tzinfo=CENTRAL),
         540, [], 600, False, True),
        ("configured holiday", datetime(2026, 9, 23, 10, 0, tzinfo=CENTRAL),
         540, ["2026-09-23"], 600, False, True),
        ("school start boundary", datetime(2026, 9, 23, 7, 0, tzinfo=CENTRAL),
         50, [], 60, True, True),
        ("school end boundary", datetime(2026, 9, 23, 16, 30, tzinfo=CENTRAL),
         50, [], 600, False, True),
    ],
)
def test_freshness_policy_window_and_age_rule(
        monkeypatch, label, local_now, age_minutes, holidays,
        expected_window, expected_school_hours, expected_within):
    monkeypatch.setattr(
        freshness_policy.config, "get_no_school_dates", lambda: holidays)
    now = local_now.astimezone(ZoneInfo("UTC"))
    synced_at = (now - timedelta(minutes=age_minutes)).isoformat()

    result = freshness_policy.freshness_envelope(
        "mirror", "assignments", "current", synced_at, now=now,
    )

    assert result["policy_window_minutes"] == expected_window, label
    assert result["school_hours"] is expected_school_hours, label
    assert result["within_policy"] is expected_within, label
    assert result["state"] == ("current" if expected_within else "stale"), label
