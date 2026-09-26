"""Pure grading-policy math, plus these two file readers: the effort-credit
mark, suggested late days, and the two plain workspace files the teacher
edits directly.

No Canvas or session access. See docs/contracts/grading-policy-contract.md
for the product decisions this implements.
"""
from __future__ import annotations

import csv
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from api.freshness_policy import LOCAL_TIMEZONE
from api.platform_services import workspace

POLICY_FILENAME = "Grading Policy.txt"
HOLIDAYS_SUBFOLDER = "Calendars"
HOLIDAYS_FILENAME = "Holidays.csv"

_POLICY_KEYS = ("floor_percent", "missing_percent", "sweep_after_school_days")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class GradingPolicyFileError(ValueError):
    """Raised when Library/Grading Policy.txt exists but is not usable.

    The message is a plain, one-sentence, teacher-facing explanation of the
    exact problem -- a missing key, a non-integer value, or a value out of
    range.
    """


def _policy_path(root=None):
    base = workspace.library_root(root)
    return os.path.join(base, POLICY_FILENAME) if base else None


def _holidays_path(root=None):
    base = workspace.library_folder(HOLIDAYS_SUBFOLDER, root)
    return os.path.join(base, HOLIDAYS_FILENAME) if base else None


def load_policy(root=None) -> dict | None:
    """Read ``Library/Grading Policy.txt``, or ``None`` when the file is absent.

    ``key: value`` lines, ``#`` starts a comment, blank lines are ignored,
    and keys are case-insensitive. All three keys (``floor_percent``,
    ``missing_percent``, ``sweep_after_school_days``) are required integers
    with ``0 <= missing_percent <= floor_percent <= 100`` and
    ``1 <= sweep_after_school_days <= 60``. A present but invalid file raises
    ``GradingPolicyFileError`` naming the exact problem. The file is read
    fresh every call; nothing is cached.
    """
    path = _policy_path(root)
    if not path or not os.path.isfile(path):
        return None
    values: dict[str, str] = {}
    # utf-8-sig strips a leading BOM transparently (Excel and Notepad both
    # sometimes save one) and reads a plain utf-8 file identically otherwise.
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            values[key.strip().lower()] = value.strip()
    missing_keys = [key for key in _POLICY_KEYS if key not in values]
    if missing_keys:
        raise GradingPolicyFileError(
            f"Grading Policy.txt is missing {', '.join(missing_keys)}.")
    parsed: dict[str, int] = {}
    for key in _POLICY_KEYS:
        raw_value = values[key]
        try:
            parsed[key] = int(raw_value)
        except ValueError:
            raise GradingPolicyFileError(
                f"Grading Policy.txt's {key} must be a whole number, not "
                f"'{raw_value}'.") from None
    floor_percent = parsed["floor_percent"]
    missing_percent = parsed["missing_percent"]
    sweep_after_school_days = parsed["sweep_after_school_days"]
    if not (0 <= missing_percent <= floor_percent <= 100):
        raise GradingPolicyFileError(
            "Grading Policy.txt's floor_percent must be at or above "
            "missing_percent, and both must be between 0 and 100.")
    if not (1 <= sweep_after_school_days <= 60):
        raise GradingPolicyFileError(
            "Grading Policy.txt's sweep_after_school_days must be between "
            "1 and 60.")
    return parsed


def _parse_date(text) -> date | None:
    """Parse one CSV date cell as ISO ``YYYY-MM-DD`` or US ``M/D/YYYY``
    (one or two digit month and day, four digit year -- the form Excel
    rewrites an ISO date column to on save). ``None`` when neither matches.
    """
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    match = _US_DATE.match(text)
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def load_no_school_dates(root=None) -> list[str]:
    """Read ``Library/Calendars/Holidays.csv``, or ``[]`` when it is absent.

    Each row is ``start`` or ``start,end`` or ``start,end,name`` -- a date
    cell is ISO ``YYYY-MM-DD`` or US ``M/D/YYYY`` (``name`` is ignored). A
    header row or any row whose first cell parses as neither form is
    skipped. A range expands to every date from ``start`` to ``end``
    inclusive. Returns sorted, deduplicated ISO dates. A leading UTF-8 BOM
    (Excel sometimes saves one) is read transparently, so a file a teacher
    opened and saved in Excel works the same as one written by hand. The
    file is read fresh every call; nothing is cached.
    """
    path = _holidays_path(root)
    if not path or not os.path.isfile(path):
        return []
    dates: set[str] = set()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            start = _parse_date(row[0])
            if start is None:
                continue
            end = _parse_date(row[1]) if len(row) > 1 else None
            if end is None:
                end = start
            if end < start:
                start, end = end, start
            current = start
            while current <= end:
                dates.add(current.isoformat())
                current += timedelta(days=1)
    return sorted(dates)


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
