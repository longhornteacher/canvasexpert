"""Integration test for Canvas QTI packaging pipeline."""

import zipfile
import io
import pytest

from engine.parsing.text_parser import TextOutlineParser
from engine.validation.validator import QuizValidator, ValidationStatus
from engine.rendering.canvas.canvas_packager import CanvasPackager


def test_canvas_packaging_pipeline():
    """Test full pipeline: parse -> validate -> package -> verify ZIP structure."""
    # Sample quiz text
    quiz_text = """Title: Simple Test Quiz
---
Type: MC
Points: 0.25
Prompt: Which of these is correct?
Choices:
- [x] Correct answer
- [ ] Wrong answer
- [ ] Also wrong
---
Type: TF
Points: 0.125
Prompt: This is true.
Answer: true
"""

    # Step 1: Parse
    parser = TextOutlineParser()
    quiz = parser.parse_text(quiz_text)

    assert quiz.title == "Simple Test Quiz"
    assert len(quiz.questions) == 2

    # Step 2: Validate
    validator = QuizValidator()
    result = validator.validate(quiz)

    assert result.status in (ValidationStatus.PASS, ValidationStatus.WEAK_PASS)

    # Step 3: Package
    packager = CanvasPackager()
    zip_bytes, guid = packager.package(quiz)

    # Step 4: Verify ZIP structure
    with zipfile.ZipFile(io.BytesIO(zip_bytes), 'r') as zf:
        # Should contain manifest and folder with assessment files
        files = zf.namelist()
        assert "imsmanifest.xml" in files
        assert f"{guid}/{guid}.xml" in files
        assert f"{guid}/assessment_meta.xml" in files

        # Verify manifest content
        manifest_content = zf.read("imsmanifest.xml").decode('utf-8')
        assert "<manifest" in manifest_content
        assert guid in manifest_content

        # Verify assessment content
        assessment_content = zf.read(f"{guid}/{guid}.xml").decode('utf-8')
        assert "<assessment" in assessment_content
        assert "Simple Test Quiz" in assessment_content
        assert "<fieldentry>0.25</fieldentry>" in assessment_content
        assert "<fieldentry>0.125</fieldentry>" in assessment_content

        # Verify metadata content
        meta_content = zf.read(f"{guid}/assessment_meta.xml").decode('utf-8')
        assert "<quiz" in meta_content
        assert "Simple Test Quiz" in meta_content
        assert "<points_possible>0.375</points_possible>" in meta_content
