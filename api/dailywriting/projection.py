"""Allowlisted assistant projection of private Writing Record evidence."""
from __future__ import annotations

import hashlib
from datetime import date

from api.dailywriting.core.models import (
    AssignmentContext,
    SegmentationFlag,
    Submission,
)


def _safe_submission_ref(submission_id: str) -> str:
    """Opaque, stable reference derived from the internal store key.

    The internal key (see canvas_source.submission_id_for) deliberately
    embeds the real Canvas user id, e.g. "canvas:course:assignment:900001",
    so a re-ingest overwrites rather than duplicates. That real id must
    never leave the machine (api/mcp_server/pseudonym.py's module docstring
    promises "canvas user id" never does), so the outbound projection
    exposes a one-way hash instead: stable across repeated ingests of the
    same submission (same input -> same hash), with the real id not
    recoverable from it.

    Found while landing feature-freeze-hardening-initiative.md's A3: the
    old field-name allowlist never scanned "submission_id" as free text, so
    Layer 2b's word-bounded real-id check never ran on it and this leak
    shipped undetected.
    """
    return hashlib.sha256(submission_id.encode()).hexdigest()[:16]


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + f" …[truncated {len(text) - max_chars} more chars]"


def _flag_row(
    flag: SegmentationFlag,
    *,
    include_text: bool,
    max_text_chars: int,
) -> dict:
    row = {"code": flag.code}
    if include_text:
        # Flag detail can quote a repeated student phrase, so it must not be
        # a key in feedback_safety._STRUCTURAL_EXEMPT_KEYS (it isn't).
        row["flag_detail"] = _truncate(flag.detail, max_text_chars)
        if flag.span is not None:
            row["span"] = {
                "start": flag.span.start,
                "end": flag.span.end,
                "text": _truncate(flag.span.text, max_text_chars),
            }
    return row


def _submission_row(
    submission: Submission,
    context: AssignmentContext | None,
    *,
    include_text: bool,
    max_text_chars: int,
) -> dict:
    row = {
        "submission_id": _safe_submission_ref(submission.submission_id),
        "rep_id": submission.rep_id,
        "submitted_at": submission.submitted_at.isoformat(),
        "student_word_count": submission.student_word_count,
        "assignment_date": context.date.isoformat() if context else None,
        "prompt_text": (
            _truncate(context.prompt_text, max_text_chars) if context else ""
        ),
        "segments": [
            {
                "origin": segment.origin,
                "span_start": segment.span_start,
                "span_end": segment.span_end,
                "method": segment.method,
                "confidence": segment.confidence,
            }
            for segment in submission.segments
        ],
        "flags": [
            _flag_row(
                flag,
                include_text=include_text,
                max_text_chars=max_text_chars,
            )
            for flag in submission.flags
        ],
    }
    if submission.attempt is not None:
        row["attempt"] = submission.attempt
    # Never a silently blank row: say whether the text is here, left out on
    # request, or absent from the stored row.
    if not include_text:
        row["text_status"] = "omitted"
    elif not submission.raw_text.strip():
        row["text_status"] = "unavailable"
        row["text_reason"] = "no_typed_text"
    else:
        row["text_status"] = "included"
        row["raw_text"] = _truncate(submission.raw_text, max_text_chars)
    return row


def build_history_payload(
    *,
    pseudonym_id: str,
    since: date,
    until: date,
    submissions: list[Submission],
    reps: dict[str, AssignmentContext | None],
    include_text: bool,
    max_text_chars: int,
) -> dict:
    """Rebuild a safe payload from named evidence fields only."""
    return {
        "pseudonym": pseudonym_id,
        "window_start": since.isoformat(),
        "window_end": until.isoformat(),
        "submissions": [
            _submission_row(
                submission,
                reps.get(submission.rep_id),
                include_text=include_text,
                max_text_chars=max_text_chars,
            )
            for submission in sorted(
                submissions,
                key=lambda item: item.submitted_at,
            )
        ],
    }
