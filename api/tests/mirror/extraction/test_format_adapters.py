"""PPTX/XLSX/PDF/image adapter laws over real synthetic documents."""
from __future__ import annotations

import pytest

from api.mirror.extraction.image import extract as extract_image
from api.mirror.extraction.pdf import extract as extract_pdf
from api.mirror.extraction.pptx import extract as extract_pptx
from api.mirror.extraction.xlsx import extract as extract_xlsx
from api.tests.mirror.extraction import document_samples


def test_pptx_follows_slide_order_and_preserves_notes():
    data = document_samples.build_pptx(
        slides=(("First title", "First body"), ("Second title", "Second body")),
        notes=("Note for first", "Note for second"))
    result = extract_pptx(data)
    assert result.availability == "complete"
    text = result.text
    assert text.index("First title") < text.index("Second title")
    assert "Note for first" in text and "Note for second" in text
    assert any(block.kind == "slide_notes" for block in result.blocks)


def test_pptx_corrupt_is_typed():
    result = extract_pptx(b"not a zip")
    assert result.availability == "unavailable"
    assert "corruption" in result.partial_reasons


def test_xlsx_preserves_sheet_order_cells_and_formulas():
    data = document_samples.build_xlsx(
        sheets=(("Alpha", [["Header", "Value"], ["Row", 42]]),
                ("Beta", [["Second", "Sheet"]])),
        formula=("Alpha", "C1", "=SUM(B1:B2)"))
    result = extract_xlsx(data)
    assert result.availability in {"complete", "partial"}
    text = result.text
    assert text.index("Header") < text.index("Second")
    assert any(block.kind == "formula" and block.text == "SUM(B1:B2)"
               for block in result.blocks)
    assert any(block.kind == "sheet_cell" and block.text == "42" for block in result.blocks)


def test_xlsx_uncached_formula_is_marked_partial():
    data = document_samples.build_xlsx(formula=("Sheet1", "C1", "=1+1"))
    result = extract_xlsx(data)
    assert "uncached_formula" in result.partial_reasons
    assert result.availability == "partial"


def test_pdf_native_text_is_extracted_without_ocr():
    data = document_samples.build_native_pdf("Native PDF sentence")
    result = extract_pdf(data)
    assert result.method == "native"
    assert "Native PDF sentence" in result.text
    assert result.availability == "complete"


def test_pdf_corrupt_is_typed():
    result = extract_pdf(b"%PDF-1.4 broken")
    assert result.availability == "unavailable"


def test_image_ocr_uses_injected_runtime_and_reports_gap_when_empty():
    from api.mirror.extraction.ocr_runtime import OcrBlock, OcrResult

    def fake_ocr(path, *, timeout):
        return OcrResult(blocks=(OcrBlock("Recognized text", ((0, 0), (1, 0), (1, 1), (0, 1)), 0.9),),
                         model_version="synthetic")

    image = document_samples.build_text_png("Recognized text")
    result = extract_image(image, ocr=fake_ocr)
    assert result.method == "ocr"
    assert "Recognized text" in result.text
    assert result.blocks[0].confidence == 0.9

    def empty_ocr(path, *, timeout):
        return OcrResult(blocks=(), model_version="synthetic")

    gap = extract_image(image, ocr=empty_ocr)
    assert gap.availability == "empty"
    assert "recognition_gap" in gap.partial_reasons


def test_image_corrupt_is_typed():
    result = extract_image(b"not an image")
    assert result.availability == "unavailable"
    assert "corruption" in result.partial_reasons


def test_image_and_scanned_pdf_use_the_real_local_ocr_runtime(tmp_path):
    """End-to-end adapter proof against S00's proven runtime (no injection).

    In an environment without the OCR wheels this fails with a typed
    dependency error, exactly like S00's own runtime tests; the isolated
    acceptance environment runs it for real.
    """
    from api.tests.mirror.extraction.ocr_samples import build_scanned_pdf, build_text_jpg

    jpg = build_text_jpg(tmp_path / "sample.jpg", "Canvas evidence")
    image_result = extract_image(jpg.read_bytes())
    assert image_result.method == "ocr"
    assert "Canvas" in image_result.text or "evidence" in image_result.text

    pdf = build_scanned_pdf(tmp_path / "scanned.pdf", "Scanned page text")
    pdf_result = extract_pdf(pdf.read_bytes())
    assert pdf_result.method in {"ocr", "mixed"}
    assert "Scanned" in pdf_result.text or "page" in pdf_result.text
