"""Laws for the pure grading-policy math: the effort-credit mark and the
suggested late-day count. No Canvas, config, or session access -- everything
is passed in, so these are tested directly, once, at the law.
"""
from __future__ import annotations

import pytest

from api import grading_policy


# --- mark() --------------------------------------------------------------

@pytest.mark.parametrize("score, points, floor_percent, insincere, expected", [
    (15, 100, 30, False, 41),      # 40.5 rounds to 41 (half up, never banker's)
    (100, 100, 30, False, 100),    # mark(P) = P
    (0, 100, 30, False, 0),        # a 0 posts unchanged
    (15, 100, 30, True, 15),       # confirmed-insincere posts unchanged
    (None, 100, 30, False, None),  # no score, no mark
])
def test_mark_locked_cases(score, points, floor_percent, insincere, expected):
    assert grading_policy.mark(score, points, floor_percent, insincere) == expected


def test_mark_is_monotonic_in_score():
    lower = grading_policy.mark(10, 100, 30, False)
    higher = grading_policy.mark(50, 100, 30, False)
    assert higher > lower


def test_mark_never_lifts_above_points_possible_unless_score_already_did():
    # A sincere score at the maximum never marks above it.
    assert grading_policy.mark(100, 100, 30, False) == 100
    # Extra credit above points_possible is not capped: the score already
    # exceeded P, so the mark may too.
    assert grading_policy.mark(110, 100, 30, False) > 100


def test_round_half_up_is_not_bankers_rounding():
    # Python's round() would send 40.5 to 40 (round-half-to-even); this must
    # round away from zero instead.
    assert grading_policy.round_half_up(40.5) == 41
    assert grading_policy.round_half_up(41.5) == 42


# --- school_days_between() / suggested_late_days() ------------------------

FRIDAY_DUE = "2026-09-25T23:59:00-05:00"  # Friday, Central time


@pytest.mark.parametrize("submitted_at, grace_days, expected", [
    ("2026-09-25T22:00:00-05:00", 0, 0),   # submitted before due: not late
    ("2026-09-28T08:00:00-05:00", 0, 1),   # Monday: 1 school day late
    ("2026-09-29T08:00:00-05:00", 0, 2),   # Tuesday: 2 school days late
    ("2026-09-26T08:00:00-05:00", 0, 1),   # Saturday right after Friday due: 1
    ("2026-09-29T08:00:00-05:00", 2, 0),   # Tuesday with 2 grace days: 0
])
def test_suggested_late_days_locked_cases(submitted_at, grace_days, expected):
    assert grading_policy.suggested_late_days(
        FRIDAY_DUE, submitted_at, grace_days, []) == expected


def test_suggested_late_days_excludes_a_no_school_date():
    # Submitted Tuesday is 2 school days late (Monday, Tuesday) with no
    # exclusions. Marking Monday a no-school date leaves only Tuesday.
    assert grading_policy.suggested_late_days(
        FRIDAY_DUE, "2026-09-29T08:00:00-05:00", 0, []) == 2
    assert grading_policy.suggested_late_days(
        FRIDAY_DUE, "2026-09-29T08:00:00-05:00", 0, ["2026-09-28"]) == 1


def test_school_days_between_excludes_weekends_and_no_school_dates():
    from datetime import date

    # Friday -> following Friday: five weekdays, minus one excluded Wednesday.
    count = grading_policy.school_days_between(
        date(2026, 9, 25), date(2026, 10, 2), ["2026-09-30"])
    assert count == 4
