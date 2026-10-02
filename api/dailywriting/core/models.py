"""Private evidence records for Writing Record."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Origin = Literal["assignment", "scaffold", "student", "quoted_source"]
SegmentMethod = Literal["exact_match", "fuzzy_match", "stem_align", "classifier", "residual"]


@dataclass(frozen=True)
class Span:
    """A character range in scrubbed submission text."""
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Segment:
    """One attributed stretch of scrubbed writing."""
    span_start: int
    span_end: int
    text: str
    origin: Origin
    confidence: float
    method: SegmentMethod


@dataclass(frozen=True)
class ScrubFinding:
    """A roster value removed before writing the record."""
    kind: Literal["roster_name", "roster_id"]
    replacement: str
    span_start: int
    span_end: int
    detail: str = ""


@dataclass(frozen=True)
class SegmentationFlag:
    """A structural fact that may limit how confidently text is read."""
    code: Literal["empty_stem_blank", "no_student_text", "low_confidence_segment", "cross_submission_repeat", "exceeds_word_cap"]
    detail: str
    span: Span | None = None


@dataclass(frozen=True)
class ScaffoldBlock:
    """A supplied stem, frame, instruction, or example."""
    block_id: str
    template: str
    kind: Literal["stem", "frame", "instruction", "example"]


@dataclass(frozen=True)
class AssignmentContext:
    """Assignment facts supplied to ingest; no inferred purpose or rubric."""
    rep_id: str
    date: date
    prompt_text: str
    scaffold_blocks: list[ScaffoldBlock] = field(default_factory=list)
    source_texts: list[str] = field(default_factory=list)
    word_cap: int | None = None
    section_id: str | None = None


@dataclass(frozen=True)
class Submission:
    """One student's scrubbed, structurally attributed writing evidence."""
    submission_id: str
    rep_id: str
    pseudonym_id: str
    submitted_at: datetime
    raw_text: str
    segments: list[Segment]
    student_word_count: int
    flags: list[SegmentationFlag] = field(default_factory=list)
    scrub_findings: list[ScrubFinding] = field(default_factory=list)
    # Mirror attempt number, recorded on rows ingested from Canvas; absent on older rows.
    attempt: int | None = None
