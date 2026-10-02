"""Explicit JSON codec for Writing Record's two private artifacts."""
from __future__ import annotations

from datetime import date, datetime

from api.dailywriting.core.models import AssignmentContext, ScaffoldBlock, ScrubFinding, Segment, SegmentationFlag, Span, Submission

DOCUMENT_VERSION = 1


class RecordSchemaError(ValueError):
    """A stored record is missing a required evidence field."""


def _require(document: dict, kind: str, *keys: str) -> None:
    missing = [key for key in keys if key not in document]
    if missing:
        raise RecordSchemaError(f"stored {kind} is missing {missing}")


def span_to_dict(span: Span | None) -> dict | None:
    return None if span is None else {"start": span.start, "end": span.end, "text": span.text}


def span_from_dict(document: dict | None) -> Span | None:
    return None if not document else Span(int(document["start"]), int(document["end"]), document.get("text", ""))


def segment_to_dict(segment: Segment) -> dict:
    return {"span_start": segment.span_start, "span_end": segment.span_end, "text": segment.text, "origin": segment.origin, "confidence": segment.confidence, "method": segment.method}


def segment_from_dict(document: dict) -> Segment:
    _require(document, "segment", "span_start", "span_end", "origin", "method")
    return Segment(int(document["span_start"]), int(document["span_end"]), document.get("text", ""), document["origin"], float(document.get("confidence", 0.0)), document["method"])


def flag_to_dict(flag: SegmentationFlag) -> dict:
    return {"code": flag.code, "detail": flag.detail, "span": span_to_dict(flag.span)}


def flag_from_dict(document: dict) -> SegmentationFlag:
    _require(document, "flag", "code")
    return SegmentationFlag(document["code"], document.get("detail", ""), span_from_dict(document.get("span")))


def finding_to_dict(finding: ScrubFinding) -> dict:
    return {"kind": finding.kind, "replacement": finding.replacement, "span_start": finding.span_start, "span_end": finding.span_end, "detail": finding.detail}


def finding_from_dict(document: dict) -> ScrubFinding:
    _require(document, "scrub finding", "kind", "replacement")
    return ScrubFinding(document["kind"], document["replacement"], int(document.get("span_start", 0)), int(document.get("span_end", 0)), document.get("detail", ""))


def rep_to_dict(context: AssignmentContext) -> dict:
    return {"schema": DOCUMENT_VERSION, "rep_id": context.rep_id, "date": context.date.isoformat(), "prompt_text": context.prompt_text, "scaffold_blocks": [{"block_id": block.block_id, "template": block.template, "kind": block.kind} for block in context.scaffold_blocks], "source_texts": list(context.source_texts), "word_cap": context.word_cap, "section_id": context.section_id}


def rep_from_dict(document: dict) -> AssignmentContext:
    _require(document, "rep", "rep_id", "date", "prompt_text")
    return AssignmentContext(rep_id=document["rep_id"], date=date.fromisoformat(document["date"]), prompt_text=document["prompt_text"], scaffold_blocks=[ScaffoldBlock(block_id=b["block_id"], template=b["template"], kind=b.get("kind", "stem")) for b in document.get("scaffold_blocks", [])], source_texts=list(document.get("source_texts", [])), word_cap=document.get("word_cap"), section_id=document.get("section_id"))


def submission_to_dict(submission: Submission, *, canvas_id: str) -> dict:
    document = _submission_document(submission, canvas_id)
    if submission.attempt is not None:
        document["attempt"] = int(submission.attempt)
    return document


def _submission_document(submission: Submission, canvas_id: str) -> dict:
    return {"schema": DOCUMENT_VERSION, "submission_id": submission.submission_id, "rep_id": submission.rep_id, "canvas_id": str(canvas_id), "submitted_at": submission.submitted_at.isoformat(), "raw_text": submission.raw_text, "student_word_count": submission.student_word_count, "segments": [segment_to_dict(segment) for segment in submission.segments], "flags": [flag_to_dict(flag) for flag in submission.flags], "scrub_findings": [finding_to_dict(finding) for finding in submission.scrub_findings]}


def submission_from_dict(document: dict, *, pseudonym_id: str) -> Submission:
    _require(document, "submission", "submission_id", "rep_id", "submitted_at")
    return Submission(submission_id=document["submission_id"], rep_id=document["rep_id"], pseudonym_id=pseudonym_id, submitted_at=datetime.fromisoformat(document["submitted_at"]), raw_text=document.get("raw_text", ""), segments=[segment_from_dict(segment) for segment in document.get("segments", [])], student_word_count=int(document.get("student_word_count", 0)), flags=[flag_from_dict(flag) for flag in document.get("flags", [])], scrub_findings=[finding_from_dict(finding) for finding in document.get("scrub_findings", [])], attempt=None if document.get("attempt") is None else int(document["attempt"]))
