"""PDF extraction: native per-page text first, rasterize + OCR textless pages.

Mixed documents retain page-level method and gaps. A failed page is never
silently an empty successful page. OCR runs through S00's proven local runtime.
"""
from __future__ import annotations

import io

from .schema import Block, ExtractionResult, ExtractionError, box_bounds, digest_bytes

EXTRACTOR_VERSION = "pdf-3"
MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES_PER_CHUNK = 25
OCR_TIMEOUT = 60.0


def _native_pages(data: bytes) -> list[str]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append(None)  # a failed page is distinct from an empty page
    return pages


def _rasterize_page(data: bytes, index: int) -> bytes:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(io.BytesIO(data))
    page = document[index]
    bitmap = page.render(scale=2.0)
    image = bitmap.to_pil()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def extract(data: bytes, *, filename: str = "", ocr=None) -> ExtractionResult:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    digest = digest_bytes(data)
    if len(data) > MAX_BYTES:
        return ExtractionResult(input_digest=digest, detected_format="pdf",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("resource_limit",), total_units=1)
    try:
        pages = _native_pages(data)
    except Exception:
        return ExtractionResult(input_digest=digest, detected_format="pdf",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("corruption",), total_units=1)
    if ocr is None:
        from .ocr_runtime import recognize as ocr
    blocks: list[Block] = []
    methods = set()
    reasons: set[str] = set()
    failed_pages = 0
    for index, text in enumerate(pages):
        if text is None:
            failed_pages += 1
            reasons.add("page_failed")
            blocks.append(Block(block_id=f"page:{index}:failed", kind="pdf_page",
                                text="", locator={"page": index + 1},
                                formatting={"page_failed": 1}))
            continue
        if text.strip():
            methods.add("native")
            blocks.append(Block(block_id=f"page:{index}", kind="pdf_page", text=text,
                                locator={"page": index + 1}, method="native"))
            continue
        # Textless page: rasterize and OCR. A recognition failure is a gap.
        try:
            raster = _rasterize_page(data, index)
            result = _ocr_raster(ocr, raster)
            methods.add("ocr")
            for block_index, ocr_block in enumerate(result.blocks):
                blocks.append(Block(block_id=f"page:{index}:ocr:{block_index}",
                                    kind="image_text", text=ocr_block.text,
                                    locator={"page": index + 1, **box_bounds(ocr_block.box)},
                                    confidence=ocr_block.confidence, method="ocr"))
            if not result.blocks:
                reasons.add("recognition_gap")
        except ExtractionError as exc:
            reasons.add(exc.code)
            failed_pages += 1
        except Exception:
            reasons.add("recognition_gap")
            failed_pages += 1
    if not any(block.text.strip() for block in blocks):
        return ExtractionResult(input_digest=digest, detected_format="pdf",
                                method="ocr" if "ocr" in methods else "native",
                                availability="empty", extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=tuple(sorted(reasons | {"no_extractable_text"})),
                                total_units=len(pages), blocks=tuple(blocks))
    method = "mixed" if len(methods) > 1 else (next(iter(methods)) if methods else "none")
    availability = "partial" if reasons else "complete"
    return ExtractionResult(input_digest=digest, detected_format="pdf", method=method,
                            availability=availability, blocks=tuple(blocks),
                            extractor_version=EXTRACTOR_VERSION,
                            partial_reasons=tuple(sorted(reasons)),
                            processed_units=len(pages) - failed_pages, total_units=len(pages))


def _ocr_raster(ocr, raster: bytes):
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(raster)
        path = Path(handle.name)
    try:
        return ocr(path, timeout=OCR_TIMEOUT)
    finally:
        path.unlink(missing_ok=True)
