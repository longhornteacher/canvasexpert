from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from api.tests.mirror.extraction.ocr_samples import build_scanned_pdf, build_text_jpg


SAMPLE = "SYNTHETIC SAMPLE\nThe quick brown fox reviews page seven."


def test_text_jpg_is_valid_jpeg_with_expected_dimensions_and_repeatable_pixels(tmp_path: Path):
    first = build_text_jpg(tmp_path / "first.jpg", SAMPLE)
    second = build_text_jpg(tmp_path / "second.jpg", SAMPLE)

    with Image.open(first) as image:
        assert image.format == "JPEG"
        assert image.size == (1600, 900)
        first_pixels = image.convert("RGB").tobytes()
    with Image.open(second) as image:
        assert image.format == "JPEG"
        assert image.convert("RGB").tobytes() == first_pixels


def test_scanned_pdf_is_valid_image_only_pdf_and_repeatable(tmp_path: Path):
    first = build_scanned_pdf(tmp_path / "first.pdf", SAMPLE)
    second = build_scanned_pdf(tmp_path / "second.pdf", SAMPLE)

    one, two = PdfReader(first), PdfReader(second)
    assert len(one.pages) == len(two.pages) == 1
    assert one.pages[0].extract_text() in (None, "")
    assert two.pages[0].extract_text() in (None, "")
    assert one.pages[0].images
    assert one.pages[0].images[0].data == two.pages[0].images[0].data


def test_builders_reject_empty_text(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError, match="text must contain"):
        build_text_jpg(tmp_path / "empty.jpg", " \n ")
    with pytest.raises(ValueError, match="text must contain"):
        build_scanned_pdf(tmp_path / "empty.pdf", "")
