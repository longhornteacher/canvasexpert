"""Scrub and segment one acquired piece of writing into private evidence."""
from __future__ import annotations

from datetime import datetime

from api.dailywriting.core.models import AssignmentContext, Submission
from api.dailywriting.core import scrub, segmentation


def ingest(*, submission_id: str, rep_id: str, pseudonym_id: str,
           submitted_at: datetime, text: str, context: AssignmentContext,
           vault=None, roster_map=None, attempt: int | None = None) -> Submission:
    """Return one scrubbed, segmented submission; this operation does not assess it."""
    scrubbed = scrub.scrub_writing(text, vault=vault, roster_map=roster_map)
    segmented = segmentation.segment_submission(scrubbed.text, context)
    return Submission(
        submission_id=submission_id, rep_id=rep_id, pseudonym_id=pseudonym_id,
        submitted_at=submitted_at, raw_text=scrubbed.text,
        segments=segmented.segments, student_word_count=segmented.student_word_count,
        flags=segmented.flags, scrub_findings=scrubbed.findings, attempt=attempt,
    )
