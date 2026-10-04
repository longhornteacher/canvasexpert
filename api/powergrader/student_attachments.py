"""Shared local routing for student-upload evidence.

This module is intentionally independent of Canvas transport.  It receives a
downloaded local file, preserves the original for teacher review, and decides
whether text or a metadata-stripped image derivative may enter an AI bundle.
"""

from __future__ import annotations

import io
import mimetypes
import os
from pathlib import Path

from api.platform_services import workspace
from api.source_material_extractors import collapse_ws, decode_bytes
from api.powergrader import writing_timeline


TRUSTED_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".py", ".html", ".htm",
    ".css", ".js",
}
RASTER_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
LOCAL_ONLY_EXTS = {".pdf", ".doc", ".docm", ".xls", ".xlsx", ".ppt", ".pptx"}
MAX_AI_TEXT_CHARS = 750_000

# The extensions `route_bytes` turns into AI-sendable response *text*.  This is the
# single source of truth for "can PowerGrader read this student's work"; the
# scheduled-autoscore gate derives its own set from this one rather than keeping a
# parallel hand-maintained list.  Rasters are AI-eligible too, but they carry no
# response text, so they are deliberately excluded.
AI_TEXT_EXTS = TRUSTED_TEXT_EXTS | {".docx"}


def _media_type(filename: str, data: bytes = b"") -> str:
    ext = Path(filename or "").suffix.lower()
    known = {
        ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
        ".json": "application/json", ".py": "text/x-python", ".html": "text/html",
        ".htm": "text/html", ".css": "text/css", ".js": "text/javascript",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf",
    }
    return known.get(ext) or mimetypes.guess_type(filename or "")[0] or "application/octet-stream"


def _docx_segments(data: bytes) -> tuple[str, list[dict]]:
    """Extract visible body order and inline image parts without document metadata."""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except Exception as e:
        raise ValueError(f"DOCX extraction requires python-docx: {type(e).__name__}") from e

    doc = Document(io.BytesIO(data))
    chunks: list[str] = []
    media: list[dict] = []
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, doc)
            parts = []
            # Walk the XML children rather than using Paragraph.text so an
            # inline drawing stays between the text nodes that surround it.
            for node in child.iter():
                local = node.tag.rsplit("}", 1)[-1]
                if local == "t":
                    parts.append(node.text or "")
                elif local == "tab":
                    parts.append("\t")
                elif local in {"br", "cr"}:
                    parts.append("\n")
                elif local == "blip":
                    rel_id = next(
                        (value for key, value in node.attrib.items()
                         if key.rsplit("}", 1)[-1] == "embed"),
                        None,
                    )
                    part = doc.part.related_parts.get(rel_id) if rel_id else None
                    blob = getattr(part, "blob", None)
                    if blob:
                        marker = f"[Inline image {len(media) + 1}]"
                        parts.append(f"\n{marker}\n")
                        media.append({"position": len(chunks) + len(parts),
                                      "data": bytes(blob),
                                      "filename": f"inline-image-{len(media) + 1}.png",
                                      "media_type": _media_type(getattr(part, "partname", ""))})
            text = "".join(parts).strip()
            if text:
                style = str(getattr(paragraph.style, "name", "") or "")
                chunks.append(f"[{style}] {text}" if style.lower().startswith("heading") else text)
        elif tag == "tbl":
            table = Table(child, doc)
            rows = []
            for row in table.rows:
                values = [cell.text.strip() for cell in row.cells]
                if any(values):
                    rows.append(" | ".join(values))
            if rows:
                chunks.append("[Table]\n" + "\n".join(rows))
    text = collapse_ws("\n\n".join(chunks))
    if not text and not media:
        raise ValueError("No readable visible body was found in DOCX.")
    return text, media


def _validate_raster(data: bytes, filename: str) -> tuple[str, bytes]:
    try:
        from PIL import Image
    except Exception as e:
        raise ValueError(f"image validation requires Pillow: {type(e).__name__}") from e
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        fmt = (image.format or "").upper()
        if fmt not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise ValueError("unsupported image encoding")
        image.load()
        # Re-encode from pixels only.  No EXIF, ICC profile, comments, or other
        # source metadata is copied into the derivative.
        if fmt == "JPEG":
            converted = image.convert("RGB")
            out_fmt, media_type = "JPEG", "image/jpeg"
        elif fmt == "GIF":
            converted = image.convert("RGBA")
            out_fmt, media_type = "PNG", "image/png"
        elif fmt == "WEBP":
            converted = image.convert("RGBA")
            out_fmt, media_type = "PNG", "image/png"
        else:
            converted = image.convert("RGBA")
            out_fmt, media_type = "PNG", "image/png"
        out = io.BytesIO()
        converted.save(out, format=out_fmt)
        return media_type, out.getvalue()
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        raise ValueError(f"image validation failed: {type(e).__name__}") from e


def route_bytes(filename: str, data: bytes, *, max_ai_chars: int = MAX_AI_TEXT_CHARS) -> dict:
    """Return a local/AI routing decision without sending or retaining a URL."""
    filename = os.path.basename(str(filename or "attachment"))
    ext = Path(filename).suffix.lower()
    result = {
        "original_filename": filename,
        "detected_media_type": _media_type(filename, data),
        "declared_size": None,
        "actual_size": len(data),
        "extraction_status": "not_attempted",
        "text": "",
        "media_derivative": None,
        "media_type": None,
        "warnings": [],
        "ai_eligible": False,
        "local_only": False,
    }
    if ext in TRUSTED_TEXT_EXTS:
        text = decode_bytes(data) if ext not in {".html", ".htm"} else decode_bytes(data)
        if len(text) > max_ai_chars:
            result.update(extraction_status="exceeds_ai_budget", local_only=True)
            result["text"] = text
            result["warnings"].append("text exceeds the AI extraction budget; no silent truncation was applied")
            return result
        result.update(text=text, extraction_status="extracted", ai_eligible=True)
        return result
    if ext == ".docx":
        text, media = _docx_segments(data)
        if len(text) > max_ai_chars:
            result.update(extraction_status="exceeds_ai_budget", local_only=True, text=text)
            result["warnings"].append("DOCX text exceeds the AI extraction budget; no silent truncation was applied")
            return result
        safe_media = []
        for index, item in enumerate(media, start=1):
            media_type, derivative = _validate_raster(item["data"], item["filename"])
            safe_media.append({"position": item.get("position"), "filename": f"inline-image-{index}.png",
                               "media_type": media_type, "data": derivative})
        result.update(text=text, extraction_status="extracted", ai_eligible=True,
                      media_derivative=safe_media, media_type="mixed" if safe_media else None)
        return result
    if ext in RASTER_EXTS:
        media_type, derivative = _validate_raster(data, filename)
        result.update(extraction_status="validated", ai_eligible=True,
                      media_derivative=derivative, media_type=media_type)
        return result
    result.update(extraction_status="local_only", local_only=True)
    result["warnings"].append("format is preserved locally but is not approved for AI transmission")
    return result


def ingest_local_file(path: str, *, attempt_dir: str, original_filename: str | None = None,
                      declared_size=None, attempt=None, item_id=None) -> dict:
    """Extract approved local content and write a teacher-readable text sidecar."""
    source = Path(path)
    filename = os.path.basename(original_filename or source.name)
    # os-level I/O through extended_path: a deep attempt folder can push these
    # past Windows' 260-char limit, and pathlib strips the \\?\ prefix.
    with open(workspace.extended_path(path), "rb") as fh:
        data = fh.read()
    result = route_bytes(filename, data)
    result.update({"filename": filename, "local_path": str(source),
                   "declared_size": declared_size, "attempt": attempt,
                   "item_id": str(item_id or ""), "download_status": "downloaded"})
    if result.get("text"):
        text_dir = os.path.join(attempt_dir, "Extracted Text")
        os.makedirs(workspace.extended_path(text_dir), exist_ok=True)
        stem = Path(filename).stem
        text_path = os.path.join(text_dir, f"{stem}__extracted.txt")
        n = 2
        while os.path.exists(workspace.extended_path(text_path)):
            text_path = os.path.join(text_dir, f"{stem}__extracted ({n}).txt")
            n += 1
        with open(workspace.extended_path(text_path), "w", encoding="utf-8") as fh:
            fh.write(result["text"])
        result["extracted_text_path"] = text_path
    result["actual_size"] = os.path.getsize(workspace.extended_path(path))
    return result


def attach_writing_timelines(
    submissions: list[dict],
    *,
    roster_submissions: list[dict] | None = None,
) -> list[dict]:
    """Parse and categorize private timelines for a tracked assignment.

    The caller owns assignment classification. This helper never guesses from a
    filename alone whether an assignment is tracked; it only handles the DOCX
    attachments the classified caller supplies.
    """
    roster = writing_timeline.roster_records(roster_submissions or submissions)
    for submission in submissions or []:
        canvas_id = str(submission.get("user_id") or "")
        for attachment in submission.get("attachments") or []:
            if not isinstance(attachment, dict):
                continue
            filename = attachment.get("filename") or attachment.get("display_name") or ""
            if Path(str(filename)).suffix.lower() != ".docx":
                continue
            local_path = attachment.get("local_path")
            if not local_path or not os.path.isfile(workspace.extended_path(local_path)):
                report = writing_timeline.unavailable_report()
            else:
                try:
                    with open(workspace.extended_path(local_path), "rb") as source:
                        report = writing_timeline.parse_docx(source.read())
                except OSError:
                    report = writing_timeline.unavailable_report()
            attachment["writing_timeline"] = writing_timeline.categorize_authors(
                report,
                submission_canvas_id=canvas_id,
                roster=roster,
            )
    return submissions


def eligibility_decision(attachments: list[dict], *, expected_count: int | None = None) -> dict:
    """Fail closed for one student if any expected evidence is incomplete."""
    items = list(attachments or [])
    reasons = []
    if expected_count is not None:
        try:
            expected = int(expected_count)
        except (TypeError, ValueError):
            expected = -1
        if expected < 0:
            reasons.append("attachment expectation was malformed")
        elif len(items) != expected:
            reasons.append(f"expected {expected} attachment(s), received {len(items)}")
    for item in items:
        if not isinstance(item, dict):
            reasons.append("attachment evidence was malformed")
            continue
        filename = item.get("original_filename") or item.get("filename") or item.get("display_name") or "attachment"
        # route_bytes() is also used as an in-memory preflight helper.  Once a
        # record carries transport metadata, however, it must prove that the
        # original was downloaded and finalized before it can enter the AI lane.
        if "download_status" in item and item.get("download_status") not in {"downloaded", "reused"}:
            reasons.append(f"{filename}: {item.get('download_status') or 'download status missing'}")
        local_path = item.get("local_path")
        if "download_status" in item and (not local_path or not os.path.isfile(workspace.extended_path(local_path))):
            reasons.append(f"{filename}: original file is unavailable locally")
        declared = item.get("declared_size")
        actual = item.get("actual_size")
        if declared not in (None, "") and actual not in (None, ""):
            try:
                if int(declared) != int(actual):
                    reasons.append(f"{filename}: declared and actual sizes differ")
            except (TypeError, ValueError):
                reasons.append(f"{filename}: file size metadata was malformed")
        status = item.get("extraction_status") or "missing"
        if status not in {"extracted", "validated"}:
            reasons.append(f"{filename}: {status}")
        if item.get("local_only"):
            reasons.append(f"{filename}: local_only")
        if not item.get("ai_eligible"):
            reasons.append(f"{filename}: not eligible for automated scoring")
    return {"eligible": not reasons, "held": bool(reasons), "reasons": reasons,
            "expected_count": expected_count, "attachment_count": len(items)}


def write_safe_derivatives(route: dict, destination_dir: str, *, pseudonym: str, item_id: str,
                            compact: bool = False) -> list[dict]:
    """Persist only synthetic, metadata-stripped media derivatives for AI use.

    When *compact* is True, uses shorter media filenames:
    ``<pseudonym-hash>-<index>.<ext>`` under ``S/<pseudonym-hash>/`` (the
    destination_dir is expected to already include the per-student subfolder).
    """
    os.makedirs(workspace.extended_path(destination_dir), exist_ok=True)
    outputs = []
    media = route.get("media_derivative")
    if isinstance(media, list):
        entries = media
    elif media:
        entries = [{"data": media, "media_type": route.get("media_type") or "image/png"}]
    else:
        entries = []
    for index, entry in enumerate(entries, start=1):
        ext = ".jpg" if entry.get("media_type") == "image/jpeg" else ".png"
        if compact:
            # Compact: use a short deterministic hash of pseudonym + item_id
            short_id = workspace._deterministic_hash(f"{pseudonym}_{item_id}", 8)
            filename = f"{short_id}-{index}{ext}"
        else:
            filename = f"{str(pseudonym).replace(' ', '-')}_{str(item_id)}_image-{index}{ext}"
        filename = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
        path = os.path.join(destination_dir, filename)
        with open(workspace.extended_path(path), "wb") as f:
            f.write(entry["data"])
        outputs.append({"local_path": path, "filename": filename,
                        "media_type": entry.get("media_type") or "image/png",
                        "item_id": str(item_id)})
    return outputs
