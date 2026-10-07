"""XLSX extraction: workbook/sheet relationship order, cells, formulas, cached values.

Follows workbook relationships for sheet order, never lexical sorting. Formula
text and cached values are kept separate; uncached formula results are marked
unavailable rather than recalculated. No formula execution, external-link
resolution, or flattened cell soup.
"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from .schema import Block, ExtractionResult, ExtractionError, digest_bytes

EXTRACTOR_VERSION = "xlsx-2"
MAX_MEMBERS = 20_000
MAX_EXPANSION_BYTES = 512 * 1024 * 1024
MAX_BYTES = 100 * 1024 * 1024
MAX_CELLS = 200_000

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _bounded(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        raise ExtractionError("resource_limit")
    if sum(member.file_size for member in members) > MAX_EXPANSION_BYTES:
        raise ExtractionError("resource_limit")


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except (KeyError, ET.ParseError):
        return []
    return ["".join(node.text or "" for node in item.iter(f"{_NS}t"))
            for item in root.findall(f"{_NS}si")]


def _sheet_order(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    order = []
    for sheet in workbook.findall(f"{_NS}sheets/{_NS}sheet"):
        rel_id = sheet.get(f"{_REL}id")
        target = targets.get(rel_id)
        if target:
            order.append((sheet.get("name") or "", _resolve_target(target)))
    return order


def _resolve_target(target: str) -> str:
    """Resolve a workbook relationship target to a package path."""
    if target.startswith("/"):
        return target.lstrip("/")
    return "xl/" + target.replace("../", "")


def extract(data: bytes, *, filename: str = "") -> ExtractionResult:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    digest = digest_bytes(data)
    if len(data) > MAX_BYTES:
        return ExtractionResult(input_digest=digest, detected_format="xlsx",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("resource_limit",), total_units=1)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ExtractionResult(input_digest=digest, detected_format="xlsx",
                                method="native", availability="unavailable",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("corruption",), total_units=1)
    with archive:
        try:
            _bounded(archive)
            shared = _shared_strings(archive)
            order = _sheet_order(archive)
        except (KeyError, ExtractionError, ET.ParseError, zipfile.BadZipFile):
            return ExtractionResult(input_digest=digest, detected_format="xlsx",
                                    method="native", availability="unavailable",
                                    extractor_version=EXTRACTOR_VERSION,
                                    partial_reasons=("corruption",), total_units=1)
        blocks: list[Block] = []
        uncached = False
        cell_count = 0
        for sheet_index, (name, path) in enumerate(order):
            try:
                sheet = ET.fromstring(archive.read(path))
            except (KeyError, ET.ParseError):
                blocks.append(Block(block_id=f"sheet:{sheet_index}:failed", kind="sheet_cell",
                                    text="", locator={"sheet": name},
                                    formatting={"page_failed": True}))
                continue
            for row in sheet.iter(f"{_NS}row"):
                for cell in row.findall(f"{_NS}c"):
                    cell_count += 1
                    if cell_count > MAX_CELLS:
                        return ExtractionResult(
                            input_digest=digest, detected_format="xlsx", method="native",
                            availability="partial", blocks=tuple(blocks),
                            extractor_version=EXTRACTOR_VERSION,
                            partial_reasons=("resource_limit", "truncated"),
                            processed_units=sheet_index, total_units=len(order))
                    ref = cell.get("r") or ""
                    cell_type = cell.get("t")
                    formula = cell.find(f"{_NS}f")
                    value = cell.find(f"{_NS}v")
                    if formula is not None and formula.text:
                        blocks.append(Block(block_id=f"sheet:{sheet_index}:{ref}:f",
                                            kind="formula", text=formula.text,
                                            locator={"sheet": name, "cell": ref}))
                        if value is not None and value.text is not None:
                            blocks.append(Block(block_id=f"sheet:{sheet_index}:{ref}:v",
                                                kind="cached_value", text=value.text,
                                                locator={"sheet": name, "cell": ref}))
                        else:
                            uncached = True
                        continue
                    if value is None or value.text is None:
                        # Inline strings carry their text in <is><t>.
                        inline = cell.find(f"{_NS}is")
                        if inline is not None:
                            text = "".join(node.text or "" for node in inline.iter(f"{_NS}t"))
                            if text:
                                blocks.append(Block(block_id=f"sheet:{sheet_index}:{ref}",
                                                    kind="sheet_cell", text=text,
                                                    locator={"sheet": name, "cell": ref}))
                        continue
                    text = value.text
                    if cell_type == "s":
                        try:
                            text = shared[int(value.text)]
                        except (ValueError, IndexError):
                            text = value.text
                    blocks.append(Block(block_id=f"sheet:{sheet_index}:{ref}",
                                        kind="sheet_cell", text=text,
                                        locator={"sheet": name, "cell": ref}))
    if not any(block.text.strip() for block in blocks):
        return ExtractionResult(input_digest=digest, detected_format="xlsx",
                                method="native", availability="empty",
                                extractor_version=EXTRACTOR_VERSION,
                                partial_reasons=("no_extractable_text",),
                                total_units=len(order), blocks=tuple(blocks))
    reasons = ("uncached_formula",) if uncached else ()
    return ExtractionResult(input_digest=digest, detected_format="xlsx",
                            method="native",
                            availability="partial" if uncached else "complete",
                            blocks=tuple(blocks), extractor_version=EXTRACTOR_VERSION,
                            partial_reasons=reasons, processed_units=len(order),
                            total_units=len(order))
