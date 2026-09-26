"""Laws for the pure grading-policy math: the effort-credit mark and the
suggested late-day count. No Canvas, config, or session access -- everything
is passed in, so these are tested directly, once, at the law.
"""
from __future__ import annotations

import codecs
import os

import pytest

from api import grading_policy
from api.platform_services import workspace


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


# --- load_policy() / load_no_school_dates(): the two plain workspace files
# (docs/contracts/grading-policy-contract.md section 4) -----------------------

def test_load_policy_absent_file_returns_none(grading_policy_files):
    assert grading_policy.load_policy() is None


def test_load_policy_valid_file_parses_case_insensitive_keys_and_comments(grading_policy_files):
    grading_policy_files.raw_policy(
        "# a comment line\n"
        "\n"
        "FLOOR_PERCENT: 30  # inline comment\n"
        "missing_percent:20\n"
        "Sweep_After_School_Days: 15\n"
    )
    assert grading_policy.load_policy() == {
        "floor_percent": 30, "missing_percent": 20, "sweep_after_school_days": 15,
    }


@pytest.mark.parametrize("raw", [
    "floor_percent: 30\nmissing_percent: 20\n",  # missing sweep_after_school_days
    "floor_percent: 10\nmissing_percent: 20\nsweep_after_school_days: 15\n",  # floor below missing
    "floor_percent: abc\nmissing_percent: 20\nsweep_after_school_days: 15\n",  # non-integer
    "floor_percent: 30\nmissing_percent: 20\nsweep_after_school_days: 0\n",  # sweep out of range (low)
    "floor_percent: 30\nmissing_percent: 20\nsweep_after_school_days: 61\n",  # sweep out of range (high)
], ids=["missing_key", "floor_below_missing", "non_integer", "sweep_too_low", "sweep_too_high"])
def test_load_policy_invalid_file_raises_with_a_readable_message(grading_policy_files, raw):
    grading_policy_files.raw_policy(raw)
    with pytest.raises(grading_policy.GradingPolicyFileError):
        grading_policy.load_policy()


def test_load_no_school_dates_absent_file_returns_empty_list(grading_policy_files):
    assert grading_policy.load_no_school_dates() == []


def test_load_no_school_dates_expands_ranges_skips_header_and_junk_rows(grading_policy_files):
    grading_policy_files.holidays([
        ["start", "end", "name"],  # header row, skipped
        ["2026-11-23", "2026-11-27", "Thanksgiving"],
        ["2026-12-21"],
        ["not-a-date", "2026-12-22"],  # junk first cell, whole row skipped
        [],  # blank row, skipped
    ])
    assert grading_policy.load_no_school_dates() == [
        "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27",
        "2026-12-21",
    ]

    # Excel-saved variant of the same calendar: a UTF-8 BOM on the first row
    # (no header this time) and US M/D/YYYY dates, including a range -- both
    # must read the same as the hand-written ISO file above.
    path = os.path.join(
        workspace.library_folder(grading_policy.HOLIDAYS_SUBFOLDER),
        grading_policy.HOLIDAYS_FILENAME)
    with open(path, "wb") as handle:
        handle.write(codecs.BOM_UTF8)
        handle.write(
            b"11/23/2026,11/27/2026,Thanksgiving\r\n"
            b"12/21/2026\r\n"
        )
    assert grading_policy.load_no_school_dates() == [
        "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27",
        "2026-12-21",
    ]
