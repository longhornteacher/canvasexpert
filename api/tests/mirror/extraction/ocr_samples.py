"""Deterministic, wholly synthetic raster samples for local OCR tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


_SIZE = (1600, 900)
_MARGIN = 120


def _text_image(text: str) -> Image.Image:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must contain at least one non-whitespace character")
    image = Image.new("RGB", _SIZE, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    draw.multiline_text((_MARGIN, _MARGIN), text, fill="black", font=font, spacing=28)
    return image


def build_text_jpg(target: Path, text: str) -> Path:
    """Write a readable synthetic text image as a real JPEG file."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _text_image(text).save(target, format="JPEG", quality=95, subsampling=0, optimize=False)
    return target


def build_scanned_pdf(target: Path, text: str) -> Path:
    """Write a one-page, image-only PDF containing synthetic printed text."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _text_image(text).save(target, format="PDF", resolution=150.0)
    return target
