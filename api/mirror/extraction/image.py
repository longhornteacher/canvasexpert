"""JPG/JPEG/PNG extraction: validate, orient privately, CPU OCR into located blocks.

EXIF/GPS/raw pixels remain private; only scrubbed OCR text and geometry/status
are published. Diagram-only or unreadable handwriting reports a recognition gap
rather than fabricated text.
"""
from __future__ import annotations

import io

from .schema import Block, ExtractionResult, ExtractionError, digest_bytes

EXTRACTOR_VERSION = "image-2"
MAX_BYTES = 100 * 1024 * 1024
MAX_PIXELS = 50_000_000
OCR_TIMEOUT = 60.0


def _load_oriented(data: bytes):
    from PIL import Image, ImageOps
    image = Image.open(io.BytesIO(data))
    image.load()
    width, height = image.size
    if width * height > MAX_PIXELS:
        raise ExtractionError("resource_limit")
    # Orientation is applied privately; EXIF is never published.
    return ImageOps.exif_transpose(image)


def extract(data: bytes, *, filename: str = "", ocr=None) -> ExtractionResult:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    digest = digest_bytes(data)
    if len(data) > MAX_BYTES:
        return ExtractionResult(input_digest=digest, detected_format="image",
                                method="ocr", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("resource_limit",), total_units=1)
    try:
        image = _load_oriented(data)
    except ExtractionError as exc:
        return ExtractionResult(input_digest=digest, detected_format="image",
                                method="ocr", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=(exc.code,), total_units=1)
    except Exception:
        return ExtractionResult(input_digest=digest, detected_format="image",
                                method="ocr", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("corruption",), total_units=1)
    if ocr is None:
        from .ocr_runtime import recognize as ocr
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    try:
        result = _ocr_raster(ocr, buffer.getvalue())
    except ExtractionError as exc:
        return ExtractionResult(input_digest=digest, detected_format="image",
                                method="ocr", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=(exc.code,), total_units=1)
    except Exception:
        return ExtractionResult(input_digest=digest, detected_format="image",
                                method="ocr", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("recognition_gap",), total_units=1)
    blocks = tuple(
        Block(block_id=f"ocr:{index}", kind="image_text", text=block.text,
              locator={"box": list(block.box)}, confidence=block.confidence, method="ocr")
        for index, block in enumerate(result.blocks)
    )
    if not any(block.text.strip() for block in blocks):
        return ExtractionResult(input_digest=digest, detected_format="image",
                                method="ocr", availability="empty",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("recognition_gap",), total_units=1,
                                blocks=blocks)
    return ExtractionResult(input_digest=digest, detected_format="image", method="ocr",
                            availability="complete", blocks=blocks,
                            extractor_version=EXTRACTOR_VERSION,
                            processed_units=1, total_units=1)


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
