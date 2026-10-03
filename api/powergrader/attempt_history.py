"""Pure classification of retained Canvas submission attempts."""
from __future__ import annotations


def _empty(attempt: dict) -> bool:
    kind = str(attempt.get("submission_type") or "")
    if kind == "online_text_entry":
        return not str(attempt.get("body") or "").strip()
    if kind == "online_upload":
        return not any(str(name or "").strip()
                       for name in attempt.get("attachment_names") or [])
    if kind == "online_url":
        return "url_present" in attempt and not bool(attempt.get("url_present"))
    return False


def summarize(attempts, current_attempt=None) -> dict:
    """Return first meaningful/latest/count/complete for retained attempt records.

    The records are mirror submission-history entries. A gap is considered
    incomplete whenever Canvas' current attempt number exceeds retained count.
    """
    records = [dict(row) for row in (attempts or ()) if isinstance(row, dict)]
    records.sort(key=lambda row: int(row.get("attempt") or 0))
    meaningful = [row for row in records if not _empty(row)]
    unknown_before_meaningful = any(
        str(row.get("submission_type") or "") == "online_url"
        and "url_present" not in row
        for row in records
        if not meaningful or int(row.get("attempt") or 0) <= int(meaningful[0].get("attempt") or 0)
    )
    if unknown_before_meaningful:
        meaningful = []
    try:
        current = int(current_attempt or 0)
    except (TypeError, ValueError):
        current = 0
    return {
        "first_meaningful": meaningful[0] if meaningful else None,
        "latest": records[-1] if records else None,
        "count": len(records),
        "complete": current <= len(records),
        "known": not unknown_before_meaningful,
    }
