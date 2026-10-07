"""Existing text formats: preserve exact meaningful whitespace and decoding.

Unlike the legacy source-material helper, this adapter never collapses
whitespace or truncates durable evidence. Comparison derivatives are separate.
"""
from __future__ import annotations

from .schema import Block, ExtractionResult, digest_bytes

EXTRACTOR_VERSION = "text-2"
MAX_BYTES = 100 * 1024 * 1024
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def extract(data: bytes, *, filename: str = "") -> ExtractionResult:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    digest = digest_bytes(data)
    if len(data) > MAX_BYTES:
        return ExtractionResult(
            input_digest=digest, detected_format="text", method="native",
            availability="unavailable", extractor_version=EXTRACTOR_VERSION,
            partial_reasons=("resource_limit",), total_units=1,
        )
    text, encoding = _decode(data)
    if not text.strip():
        return ExtractionResult(
            input_digest=digest, detected_format="text", method="native",
            availability="empty", extractor_version=EXTRACTOR_VERSION,
            partial_reasons=("no_extractable_text",), total_units=1,
            format_metadata={"encoding": encoding},
        )
    # One block per line preserves exact whitespace and visible order.
    blocks = tuple(
        Block(block_id=f"line:{index}", kind="text_line", text=line,
              locator={"line": index + 1})
        for index, line in enumerate(text.split("\n"))
    )
    return ExtractionResult(
        input_digest=digest, detected_format="text", method="native",
        availability="complete", blocks=blocks, extractor_version=EXTRACTOR_VERSION,
        processed_units=1, total_units=1, format_metadata={"encoding": encoding},
    )
