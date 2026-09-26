"""Pure grading-policy math: the effort-credit mark and suggested late days.

No Canvas, config, or session access -- everything is passed in. See
docs/contracts/grading-policy-contract.md for the product decisions this
implements.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from api.freshness_policy import LOCAL_TIMEZONE


def round_half_up(value) -> int:
    """Round to the nearest whole number, half away from zero.

    Python's ``round()`` is banker's rounding (round-half-to-even) and must
    never be used for a posted grade or the missing value.
    """
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def mark(score, points_possible, floor_percent, insincere):
    """The posted-grade mark for one sincere or insincere attempt.

    ``score`` is the honest rubric result; the mark is the compressed value
    that actually goes to Canvas. A ``None`` score marks nothing. Junk (a 0 or
    a teacher-confirmed insincere attempt) posts unchanged -- it may fall
    below the missing value, which is intended. A sincere attempt above 0
    never marks above its own points_possible unless the score itself already
    did (uncapped extra credit stays uncapped).
    """
    if score is None:
        return None
    if insincere or score <= 0:
        return score
    points = float(points_possible)
    floor = float(floor_percent) / 100.0
    value = floor * points + (1 - floor) * float(score)
    rounded = round_half_up(value)
    if score <= points:
        capped = round_half_up(points)
        if rounded > capped:
            rounded = capped
    return rounded


def school_days_between(start_date, end_date, no_school_dates) -> int:
    """Count local dates ``d`` with ``start_date < d <= end_date``, Monday
    through Friday, excluding every date in ``no_school_dates`` (ISO strings).
    """
    from datetime import timedelta

    excluded = {str(value) for value in (no_school_dates or ())}
    count = 0
    current = start_date + timedelta(days=1)
    while current <= end_date:
        if current.weekday() < 5 and current.isoformat() not in excluded:
            count += 1
        current += timedelta(days=1)
    return count


def _local_date(iso_timestamp):
    parsed = datetime.fromisoformat(str(iso_timestamp).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TIMEZONE).date()


def suggested_late_days(cached_due_date, submitted_at, grace_days, no_school_dates) -> int:
    """School days late, after grace, floored at 0.

    Both timestamps are converted to ``freshness_policy.LOCAL_TIMEZONE`` local
    dates before counting. A late submission is at least one day late (a
    same-day-late submission, or one landing on a Saturday right after a
    Friday due date, both suggest 1); weekends and no-school dates never add.
    """
    if not cached_due_date or not submitted_at:
        return 0
    due_date = _local_date(cached_due_date)
    submitted_date = _local_date(submitted_at)
    if submitted_date <= due_date:
        return 0
    days = school_days_between(due_date, submitted_date, no_school_dates)
    return max(0, max(1, days) - int(grace_days or 0))
