"""Extraction schema, registry, and text/DOCX adapter laws."""
from __future__ import annotations

import pytest

from api.mirror.extraction import registry
from api.mirror.extraction.docx import extract as extract_docx
from api.mirror.extraction.schema import (
    Block, ExtractionError, ExtractionResult, result_from_dict, result_to_dict,
    validate_result,
)
from api.mirror.extraction.text import extract as extract_text
from api.tests.mirror.extraction import document_samples


def test_registry_declares_every_required_format():
    for extension in registry.REQUIRED_FORMATS:
        assert extension in registry.ADAPTERS
        assert registry.adapter_name(f"file{extension}") == extension


def test_implemented_adapters_load():
    for extension in (".txt", ".docx"):
        assert registry.adapter_for(f"file{extension}") is not None
        assert registry.extractor_version(f"file{extension}")


def test_registry_unknown_extension_has_no_adapter():
    assert registry.adapter_for("file.exe") is None
    assert registry.adapter_for("file") is None
    assert registry.extractor_version("file.exe") is None


def test_result_round_trips_through_worker_serialization():
    result = extract_text(b"line one\nline two")
    restored = result_from_dict(result_to_dict(result))
    assert restored == result
    assert restored.text == "line one\nline two"


@pytest.mark.parametrize("changes", [
    {"availability": "maybe"}, {"method": "guess"}, {"partial_reasons": ("made_up",)},
    {"processed_units": -1}, {"input_digest": "short"},
])
def test_invalid_results_are_refused(changes):
    base = dict(input_digest="a" * 64, detected_format="text", method="native",
                availability="complete")
    base.update(changes)
    with pytest.raises(ExtractionError):
        validate_result(ExtractionResult(**base))


def test_duplicate_block_ids_are_refused():
    block = Block(block_id="x", kind="paragraph", text="a")
    with pytest.raises(ExtractionError, match="duplicate_block_id"):
        validate_result(ExtractionResult(
            input_digest="a" * 64, detected_format="text", method="native",
            availability="complete", blocks=(block, block)))


def test_text_preserves_exact_whitespace_and_punctuation():
    payload = "def f():\n    return 1  # keep  spaces\n\n“curly” — dash\ttab"
    result = extract_text(payload.encode("utf-8"))
    assert result.availability == "complete"
    assert result.text == payload
    assert "\t" in result.text and "  " in result.text


def test_text_empty_and_oversize_are_honest():
    empty = extract_text(b"   \n  ")
    assert empty.availability == "empty"
    assert "no_extractable_text" in empty.partial_reasons


def test_docx_preserves_order_formatting_and_tables():
    data = document_samples.build_docx(
        paragraphs=("Alpha paragraph.", "Beta paragraph."),
        heading="Synthetic Heading",
        table=[["Cell A", "Cell B"], ["Cell C", "Cell D"]],
        bold_word="Beta",
    )
    result = extract_docx(data)
    assert result.availability == "complete"
    kinds = [block.kind for block in result.blocks]
    assert "heading" in kinds and "table_row" in kinds
    assert result.text.index("Alpha") < result.text.index("Beta")
    bold = next(block for block in result.blocks if "Beta" in block.text)
    assert bold.formatting.get("bold") is True
    assert any("Cell A | Cell B" == block.text for block in result.blocks)


def test_docx_keeps_tracked_changes_distinct_from_visible_text():
    data = document_samples.build_docx(
        paragraphs=("Visible sentence.",),
        tracked={"inserted": "INSERTED", "deleted": "DELETED"},
    )
    result = extract_docx(data)
    inserted = next(block for block in result.blocks if block.formatting.get("revision") == "inserted")
    deleted = next(block for block in result.blocks if block.formatting.get("revision") == "deleted")
    assert inserted.text == "INSERTED"
    assert deleted.text == "DELETED"
    visible = next(block for block in result.blocks if block.text == "Visible sentence.")
    assert "INSERTED" not in visible.text and "DELETED" not in visible.text


def test_docx_corrupt_and_empty_are_typed():
    corrupt = extract_docx(b"not a zip at all")
    assert corrupt.availability == "unavailable"
    assert "corruption" in corrupt.partial_reasons
    empty = extract_docx(document_samples.build_docx(paragraphs=(), heading=""))
    assert empty.availability == "empty"


def test_docx_tabs_are_preserved():
    data = document_samples.build_docx(paragraphs=("Column",), tabs=True)
    result = extract_docx(data)
    assert any("\t" in block.text for block in result.blocks)
