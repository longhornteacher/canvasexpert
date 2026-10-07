"""DOCX extraction: document order, run formatting, tracked changes, hyperlinks.

Walks body paragraphs and tables in order, preserving tabs, line breaks, quote
characters, and useful run formatting. Tracked insertions/deletions are kept as
separate revision blocks distinct from the final visible text. Author/creator
metadata is never published; absent revision history is unknown, not invented.
Hyperlink display text is retained; raw hyperlink targets are not exposed.
"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from .schema import Block, ExtractionResult, ExtractionError, digest_bytes

EXTRACTOR_VERSION = "docx-2"
MAX_MEMBERS = 20_000
MAX_EXPANSION_BYTES = 512 * 1024 * 1024
MAX_BYTES = 100 * 1024 * 1024

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _bounded_members(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ExtractionError("resource_limit")
    total = sum(member.file_size for member in members)
    if total > MAX_EXPANSION_BYTES:
        raise ExtractionError("resource_limit")


def _run_text(run: ET.Element) -> str:
    parts = []
    for child in run:
        tag = child.tag
        if tag in (f"{_W}t", f"{_W}delText"):
            parts.append(child.text or "")
        elif tag == f"{_W}tab":
            parts.append("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
        elif tag == f"{_W}noBreakHyphen":
            parts.append("\u2011")
    return "".join(parts)


def _run_formatting(run: ET.Element) -> dict:
    props = run.find(f"{_W}rPr")
    if props is None:
        return {}
    result = {}
    for key, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
        element = props.find(f"{_W}{tag}")
        if element is not None and element.get(f"{_W}val") not in {"0", "false", "none"}:
            result[key] = True
    style = props.find(f"{_W}rStyle")
    if style is not None and style.get(f"{_W}val"):
        result["run_style"] = style.get(f"{_W}val")
    return result


def _paragraph_blocks(paragraph: ET.Element, *, index: int, kind: str) -> list[Block]:
    props = paragraph.find(f"{_W}pPr")
    formatting: dict = {}
    if props is not None:
        style = props.find(f"{_W}pStyle")
        if style is not None and style.get(f"{_W}val"):
            formatting["style"] = style.get(f"{_W}val")
        num = props.find(f"{_W}numPr")
        if num is not None:
            level = num.find(f"{_W}ilvl")
            num_id = num.find(f"{_W}numId")
            if level is not None and level.get(f"{_W}val") is not None:
                formatting["list_level"] = int(level.get(f"{_W}val"))
            if num_id is not None and num_id.get(f"{_W}val") is not None:
                formatting["list_id"] = num_id.get(f"{_W}val")
    visible_parts: list[str] = []
    inserted_parts: list[str] = []
    deleted_parts: list[str] = []
    run_formatting: dict = {}
    tabs = breaks = 0

    def consume(run: ET.Element, *, bucket: list[str]) -> None:
        nonlocal tabs, breaks
        text = _run_text(run)
        bucket.append(text)
        tabs += text.count("\t")
        breaks += text.count("\n")
        for key, value in _run_formatting(run).items():
            run_formatting.setdefault(key, value)

    for child in paragraph:
        tag = child.tag
        if tag == f"{_W}r":
            consume(child, bucket=visible_parts)
        elif tag == f"{_W}hyperlink":
            for run in child.findall(f"{_W}r"):
                consume(run, bucket=visible_parts)
        elif tag == f"{_W}ins":
            for run in child.findall(f"{_W}r"):
                consume(run, bucket=inserted_parts)
        elif tag == f"{_W}del":
            for run in child.findall(f"{_W}r"):
                deleted_parts.append(_run_text(run))
    formatting.update(run_formatting)
    if tabs:
        formatting["tabs"] = tabs
    if breaks:
        formatting["breaks"] = breaks
    blocks = []
    visible = "".join(visible_parts)
    if visible.strip() or formatting:
        blocks.append(Block(block_id=f"p:{index}", kind=kind, text=visible,
                            locator={"paragraph": index}, formatting=formatting))
    if inserted_parts:
        blocks.append(Block(block_id=f"p:{index}:ins", kind="paragraph",
                            text="".join(inserted_parts),
                            locator={"paragraph": index, "revision": "inserted"},
                            formatting={"revision": "inserted"}))
    if deleted_parts:
        blocks.append(Block(block_id=f"p:{index}:del", kind="paragraph",
                            text="".join(deleted_parts),
                            locator={"paragraph": index, "revision": "deleted"},
                            formatting={"revision": "deleted"}))
    return blocks


def _table_blocks(table: ET.Element, *, index: int) -> list[Block]:
    blocks = []
    for row_index, row in enumerate(table.findall(f"{_W}tr")):
        cells = []
        for cell in row.findall(f"{_W}tc"):
            cell_text = "".join(
                "".join(_run_text(run) for run in paragraph.iter(f"{_W}r"))
                for paragraph in cell.findall(f"{_W}p"))
            cells.append(cell_text)
        blocks.append(Block(block_id=f"t:{index}:r:{row_index}", kind="table_row",
                            text=" | ".join(cells),
                            locator={"table": index, "row": row_index}))
    return blocks


def extract(data: bytes, *, filename: str = "") -> ExtractionResult:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    digest = digest_bytes(data)
    if len(data) > MAX_BYTES:
        return ExtractionResult(input_digest=digest, detected_format="docx",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("resource_limit",), total_units=1)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ExtractionResult(input_digest=digest, detected_format="docx",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("corruption",), total_units=1)
    with archive:
        try:
            _bounded_members(archive)
            document = archive.read("word/document.xml")
        except (KeyError, ExtractionError, zipfile.BadZipFile):
            return ExtractionResult(input_digest=digest, detected_format="docx",
                                    method="native", availability="unavailable",
                                    extractor_version=EXTRACTOR_VERSION,
                                    partial_reasons=("corruption",), total_units=1)
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        return ExtractionResult(input_digest=digest, detected_format="docx",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("corruption",), total_units=1)
    body = root.find(f"{_W}body")
    if body is None:
        return ExtractionResult(input_digest=digest, detected_format="docx",
                                method="native", availability="empty",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("no_extractable_text",), total_units=1)
    blocks: list[Block] = []
    paragraph_index = table_index = 0
    for child in body:
        if child.tag == f"{_W}p":
            kind = "heading" if (child.find(f"{_W}pPr/{_W}pStyle") is not None
                                 and "Heading" in (child.find(f"{_W}pPr/{_W}pStyle").get(f"{_W}val") or "")) else "paragraph"
            blocks.extend(_paragraph_blocks(child, index=paragraph_index, kind=kind))
            paragraph_index += 1
        elif child.tag == f"{_W}tbl":
            blocks.extend(_table_blocks(child, index=table_index))
            table_index += 1
    if not any(block.text.strip() for block in blocks):
        return ExtractionResult(input_digest=digest, detected_format="docx",
                                method="native", availability="empty",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("no_extractable_text",),
                                total_units=1, blocks=tuple(blocks))
    return ExtractionResult(input_digest=digest, detected_format="docx",
                            method="native", availability="complete",
                            blocks=tuple(blocks), extractor_version=EXTRACTOR_VERSION,
                            processed_units=1, total_units=1)
