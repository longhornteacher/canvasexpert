"""Strict, versioned extraction result contract.

Adapters are pure-bytes functions returning an :class:`ExtractionResult`. The
result preserves exact extracted text, ordered located blocks, honest partial
status, and separate native/OCR provenance. It never collapses whitespace or
truncates durable evidence silently; comparison derivatives are separate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math

EXTRACTION_SCHEMA_VERSION = 1
PRIVACY_POLICY_REVISION = 1

# Block kinds are a bounded vocabulary so a reader can filter without parsing.
BLOCK_KINDS = frozenset({
    "paragraph", "heading", "list_item", "table_cell", "table_row",
    "slide_text", "slide_notes", "sheet_cell", "formula", "cached_value",
    "pdf_page", "image_text", "text_line", "code", "comment",
})
# Availability is the honest top-level outcome; a failed page is never an empty
# successful page.
AVAILABILITY = frozenset({"complete", "partial", "empty", "unavailable"})
# Method records how the text was obtained, not an integrity verdict.
METHODS = frozenset({"native", "ocr", "mixed", "none"})
# Bounded, value-free partial reasons.
PARTIAL_REASONS = frozenset({
    "no_extractable_text", "corruption", "encryption", "unsupported_type",
    "missing_dependency", "timeout", "truncated", "resource_limit",
    "visual_content_unprocessed", "recognition_gap", "page_failed",
    "uncached_formula", "external_relation_skipped",
})


class ExtractionError(ValueError):
    """A value-free extraction refusal; never includes offending content."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Block:
    block_id: str
    kind: str
    text: str
    locator: dict = field(default_factory=dict)
    confidence: float | None = None
    method: str = "native"
    # Observed DOCX formatting attributes (scrubbed labels), never layout.
    formatting: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionResult:
    input_digest: str
    detected_format: str
    method: str
    availability: str
    blocks: tuple[Block, ...] = ()
    extractor_version: str = ""
    schema_version: int = EXTRACTION_SCHEMA_VERSION
    privacy_policy_revision: int = PRIVACY_POLICY_REVISION
    partial_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    processed_units: int = 0
    total_units: int = 0
    format_metadata: dict = field(default_factory=dict)
    source_revision: str | None = None

    @property
    def text(self) -> str:
        """Exact concatenated block text in order; never whitespace-collapsed."""
        return "\n".join(block.text for block in self.blocks)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def result_to_dict(result: ExtractionResult) -> dict:
    """Serialize for the supervised worker boundary; exact text is preserved."""
    return {
        "input_digest": result.input_digest,
        "detected_format": result.detected_format,
        "method": result.method,
        "availability": result.availability,
        "extractor_version": result.extractor_version,
        "schema_version": result.schema_version,
        "privacy_policy_revision": result.privacy_policy_revision,
        "partial_reasons": list(result.partial_reasons),
        "warnings": list(result.warnings),
        "processed_units": result.processed_units,
        "total_units": result.total_units,
        "format_metadata": result.format_metadata,
        "source_revision": result.source_revision,
        "blocks": [
            {"block_id": block.block_id, "kind": block.kind, "text": block.text,
             "locator": block.locator, "confidence": block.confidence,
             "method": block.method, "formatting": block.formatting}
            for block in result.blocks
        ],
    }


def result_from_dict(document: dict) -> ExtractionResult:
    if not isinstance(document, dict):
        raise ExtractionError("invalid_result")
    blocks = tuple(
        Block(block_id=row["block_id"], kind=row["kind"], text=row["text"],
              locator=row.get("locator") or {}, confidence=row.get("confidence"),
              method=row.get("method", "native"), formatting=row.get("formatting") or {})
        for row in document.get("blocks", [])
    )
    return validate_result(ExtractionResult(
        input_digest=document["input_digest"],
        detected_format=document["detected_format"],
        method=document["method"],
        availability=document["availability"],
        blocks=blocks,
        extractor_version=document.get("extractor_version", ""),
        schema_version=document.get("schema_version", EXTRACTION_SCHEMA_VERSION),
        privacy_policy_revision=document.get("privacy_policy_revision", PRIVACY_POLICY_REVISION),
        partial_reasons=tuple(document.get("partial_reasons", ())),
        warnings=tuple(document.get("warnings", ())),
        processed_units=document.get("processed_units", 0),
        total_units=document.get("total_units", 0),
        format_metadata=document.get("format_metadata") or {},
        source_revision=document.get("source_revision"),
    ))


def _validate_scalars(mapping: dict, code: str) -> None:
    """Locator and formatting values are the scalars a published fact accepts."""
    if not isinstance(mapping, dict):
        raise ExtractionError(code)
    for key, value in mapping.items():
        if not isinstance(key, str):
            raise ExtractionError(code)
        if isinstance(value, bool) or not isinstance(value, (str, int, float, type(None))):
            raise ExtractionError(code)
        if isinstance(value, float) and not math.isfinite(value):
            raise ExtractionError(code)


def box_bounds(points) -> dict:
    """An OCR quadrilateral as the scalar bounding rectangle a locator can carry."""
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return {"left": round(min(xs)), "top": round(min(ys)),
            "right": round(max(xs)), "bottom": round(max(ys))}


def validate_result(result: ExtractionResult) -> ExtractionResult:
    """Refuse a structurally invalid result before it reaches publication."""
    if not isinstance(result, ExtractionResult):
        raise ExtractionError("invalid_result")
    if result.schema_version != EXTRACTION_SCHEMA_VERSION:
        raise ExtractionError("unsupported_schema")
    if result.availability not in AVAILABILITY:
        raise ExtractionError("invalid_availability")
    if result.method not in METHODS:
        raise ExtractionError("invalid_method")
    if not isinstance(result.input_digest, str) or len(result.input_digest) != 64:
        raise ExtractionError("invalid_digest")
    if not isinstance(result.detected_format, str) or not result.detected_format:
        raise ExtractionError("invalid_format")
    if not set(result.partial_reasons) <= PARTIAL_REASONS:
        raise ExtractionError("invalid_partial_reason")
    if type(result.processed_units) is not int or result.processed_units < 0:
        raise ExtractionError("invalid_counts")
    if type(result.total_units) is not int or result.total_units < 0:
        raise ExtractionError("invalid_counts")
    seen = set()
    for block in result.blocks:
        if not isinstance(block, Block):
            raise ExtractionError("invalid_block")
        if block.kind not in BLOCK_KINDS:
            raise ExtractionError("invalid_block_kind")
        if not isinstance(block.block_id, str) or not block.block_id:
            raise ExtractionError("invalid_block_id")
        if block.block_id in seen:
            raise ExtractionError("duplicate_block_id")
        seen.add(block.block_id)
        if not isinstance(block.text, str):
            raise ExtractionError("invalid_block_text")
        if block.method not in METHODS:
            raise ExtractionError("invalid_block_method")
        if block.confidence is not None and (
                not isinstance(block.confidence, (int, float))
                or isinstance(block.confidence, bool)
                or not math.isfinite(block.confidence)
                or not 0.0 <= block.confidence <= 1.0):
            raise ExtractionError("invalid_confidence")
        _validate_scalars(block.locator, "invalid_locator")
        _validate_scalars(block.formatting, "invalid_formatting")
    return result
