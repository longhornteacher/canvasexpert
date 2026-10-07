"""PPTX extraction: presentation relationship order, slide/shape/table text, notes.

Slide order follows the presentation's slide-id relationship list, never lexical
``slide10`` sorting. Speaker notes are preserved with locators. Embedded-image
text is reported as an explicit unprocessed-visual gap rather than fabricated.
"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from .schema import Block, ExtractionResult, ExtractionError, digest_bytes

EXTRACTOR_VERSION = "pptx-2"
MAX_MEMBERS = 20_000
MAX_EXPANSION_BYTES = 512 * 1024 * 1024
MAX_BYTES = 100 * 1024 * 1024

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _bounded(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ExtractionError("resource_limit")
    if sum(member.file_size for member in members) > MAX_EXPANSION_BYTES:
        raise ExtractionError("resource_limit")


def _slide_order(archive: zipfile.ZipFile) -> list[str]:
    """Resolve slide order from presentation.xml + its relationships."""
    presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    order = []
    for slide_id in presentation.findall(f"{_P}sldIdLst/{_P}sldId"):
        rel_id = slide_id.get(f"{_R}id")
        target = targets.get(rel_id)
        if target:
            order.append("ppt/" + target.lstrip("/").replace("../", ""))
    return order


def _shape_text(shape: ET.Element) -> str:
    return "".join(node.text or "" for node in shape.iter(f"{_A}t"))


def extract(data: bytes, *, filename: str = "") -> ExtractionResult:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    digest = digest_bytes(data)
    if len(data) > MAX_BYTES:
        return ExtractionResult(input_digest=digest, detected_format="pptx",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("resource_limit",), total_units=1)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ExtractionResult(input_digest=digest, detected_format="pptx",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("corruption",), total_units=1)
    with archive:
        try:
            _bounded(archive)
            order = _slide_order(archive)
        except (KeyError, ExtractionError, ET.ParseError, zipfile.BadZipFile):
            return ExtractionResult(input_digest=digest, detected_format="pptx",
                                    method="native", availability="unavailable",
                                    extractor_version=EXTRACTOR_VERSION,
                                    partial_reasons=("corruption",), total_units=1)
        blocks: list[Block] = []
        visual_gap = False
        for index, slide_path in enumerate(order):
            try:
                slide = ET.fromstring(archive.read(slide_path))
            except (KeyError, ET.ParseError):
                blocks.append(Block(block_id=f"slide:{index}:failed", kind="slide_text",
                                    text="", locator={"slide": index + 1},
                                    formatting={"page_failed": True}))
                continue
            for shape_index, shape in enumerate(slide.iter(f"{_P}sp")):
                text = _shape_text(shape)
                if text.strip():
                    blocks.append(Block(block_id=f"slide:{index}:shape:{shape_index}",
                                        kind="slide_text", text=text,
                                        locator={"slide": index + 1, "shape": shape_index}))
            for table_index, table in enumerate(slide.iter(f"{_P}graphicFrame")):
                for row_index, row in enumerate(table.iter(f"{_A}tr")):
                    cells = ["".join(node.text or "" for node in cell.iter(f"{_A}t"))
                             for cell in row.findall(f"{_A}tc")]
                    blocks.append(Block(block_id=f"slide:{index}:table:{table_index}:{row_index}",
                                        kind="table_row", text=" | ".join(cells),
                                        locator={"slide": index + 1, "table": table_index,
                                                 "row": row_index}))
            if any(shape.tag == f"{_P}pic" for shape in slide.iter(f"{_P}pic")):
                visual_gap = True
            notes_path = slide_path.replace("slides/", "notesSlides/").replace(
                ".xml", ".xml")
            notes_rel = slide_path.replace("slides/slide", "slides/_rels/slide") + ".rels"
            try:
                rels = ET.fromstring(archive.read(notes_rel))
                for rel in rels:
                    if rel.get("Type", "").endswith("/notesSlide"):
                        target = "ppt/" + rel.get("Target").lstrip("/").replace("../", "")
                        notes = ET.fromstring(archive.read(target))
                        notes_text = "".join(node.text or "" for node in notes.iter(f"{_A}t"))
                        if notes_text.strip():
                            blocks.append(Block(block_id=f"slide:{index}:notes",
                                                kind="slide_notes", text=notes_text,
                                                locator={"slide": index + 1}))
            except (KeyError, ET.ParseError):
                pass
    if not any(block.text.strip() for block in blocks):
        return ExtractionResult(input_digest=digest, detected_format="pptx",
                                method="native", availability="empty",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("no_extractable_text",),
                                total_units=len(order), blocks=tuple(blocks))
    reasons = ("visual_content_unprocessed",) if visual_gap else ()
    return ExtractionResult(input_digest=digest, detected_format="pptx",
                            method="native",
                            availability="partial" if visual_gap else "complete",
                            blocks=tuple(blocks), extractor_version=EXTRACTOR_VERSION,
                            partial_reasons=reasons, processed_units=len(order),
                            total_units=len(order))
