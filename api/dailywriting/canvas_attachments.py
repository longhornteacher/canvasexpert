"""Acquire one uploaded Word document's text for the writing record.

An uploaded file is invisible to the local mirror by construction: `api/mirror/
store.py` `_attempt_record` keeps `attachment_names` only -- filenames, never
bytes, never a URL -- and Canvas leaves `body` empty for an `online_upload`
submission. There is no cached copy to read, so this module makes the writing
record's only live Canvas calls: one focused submission fetch and one bounded
file download, for a submission the mirror shows as an upload with no text.

That is fine here because `docs/mirror.md` design law 6 (agent reads come from
the mirror) covers the AI-facing MCP tools, whose whole path to Canvas stays
indirect; `api/portfolio_service.py` and `api/student_packet.py` already make
the same kind of focused per-submission call for a teacher-invoked report. The
constraint that comes with it: this path should not be exposed as an MCP tool,
because that would put a live Canvas path under the assistant, and Canvas
Expert is meant to be the only thing that talks to Canvas.

Both calls go through `api.platform_services.canvas_client` rather than
`api.submission_transport`, which builds its own session with neither Canvas
429 retry nor the mirror coordinator's cooperative yield. One teacher-watched
report can afford that; an unattended sequential pass over a class of thirty
cannot, and a third uncoordinated Canvas caller is not worth adding.

Nothing here reaches the store. It returns text or it returns a reason, so a
refusal cannot leave a student's record half written; scrub still happens at
ingest, on the way in, where INV-7 requires it.

A re-run re-downloads. Canvas signs attachment URLs with an expiry, so a cached
URL cannot be reused across runs, and a skip-if-unchanged check would mean
storing a per-submission size or hash -- a store change for a cost that is
already bounded to one teacher-initiated pass over one class.
"""
from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone
from pathlib import Path

from api.powergrader import student_attachments
from api.platform_services import canvas_client

# Generous for a text-bearing DOCX -- a 1500-word essay is tens of kilobytes,
# a few megabytes once a student pastes in photographs -- while still bounding
# an unattended pass over a whole class.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_CHUNK_BYTES = 16_384
_DOWNLOAD_TIMEOUT = 120

_MEGABYTE = 1024 * 1024

# `_docx_segments` annotates its own output for a reader: `[Inline image N]` on
# its own line, `[Table]` above a table's rows, `[Heading N] ` before a
# heading-styled paragraph's text. Those are the extractor's words, not the
# student's, and they must not reach the record: they would be counted by
# `student_word_count`, attributed to the student by segmentation, and quoted
# back as evidence. A plain essay -- measured -- carries none
# of them, so this strips annotations only and never rewrites student text.
_IMAGE_MARKER = re.compile(r"^\[Inline image \d+\]$")
_BLOCK_ANNOTATION = re.compile(r"^\[(?:Heading[^\]]*|Table)\]\s*")


@dataclasses.dataclass(frozen=True)
class AcquiredText:
    """One submission's uploaded text, or the reason there is none.

    `notes` are teacher-facing progress lines (they name files, so they belong
    on the teacher's own CLI or web response and never in a stored record or an
    outbound payload). `outcome` is what the driver counts.
    """

    text: str = ""
    notes: tuple[str, ...] = ()
    outcome: str = "extracted"
    source: str = ""


def transport_ready() -> bool:
    """True when this machine has a Canvas token to fetch an upload with."""
    headers, _base = canvas_client.canvas_headers()
    return bool(headers)


def is_upload_row(row: dict) -> bool:
    """True for a mirror row whose text, if any, lives in an uploaded file.

    Gates on the mirror record alone, so a typed submission and a student who
    never submitted both cost zero network calls. `submission_type` is on the
    served `current` record; `attachment_names` is not (it lives in `attempts`,
    which `read_service.private_submissions` does not serve), so the live
    response is what supplies the actual list of files.
    """
    return (str(row.get("submission_type") or "") == "online_upload"
            and bool(row.get("submitted_at")))


def _filename(attachment: dict) -> str:
    raw = attachment.get("filename") or attachment.get("display_name") or "attachment"
    return Path(str(raw)).name


def _uploaded_at(attachment: dict, index: int):
    """Sort key for "latest submitted": Canvas's `created_at`, then upload order."""
    text = str(attachment.get("created_at") or "").strip()
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        stamp = datetime.min.replace(tzinfo=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (stamp, index)


def _choose_docx(attachments: list) -> tuple[dict | None, list[str]]:
    """Pick the latest-submitted `.docx` and name every file it passes over.

    Only `.docx` in this batch: PDF text extraction lives on a different path
    (`api/portfolio.py`), images have no text extraction anywhere, and there is
    no OCR in this repository. A skipped file is always named, never silently
    dropped.
    """
    notes: list[str] = []
    candidates = []
    for index, attachment in enumerate(attachments):
        if not isinstance(attachment, dict):
            continue
        name = _filename(attachment)
        if Path(name).suffix.lower() == ".docx":
            candidates.append((index, attachment))
        else:
            notes.append(f"skipped {name} (not a Word document)")
    if not candidates:
        return None, notes
    ordered = sorted(candidates, key=lambda pair: _uploaded_at(pair[1], pair[0]))
    chosen = ordered[-1][1]
    for _index, attachment in ordered[:-1]:
        notes.append(f"skipped {_filename(attachment)} (an earlier upload)")
    return chosen, notes


def _declared_megabytes(size) -> float | None:
    try:
        return int(size) / _MEGABYTE
    except (TypeError, ValueError):
        return None


def _too_large_note(name: str, megabytes: float | None) -> str:
    measured = f"{megabytes:.1f} MB" if megabytes is not None else "the file"
    limit = MAX_ATTACHMENT_BYTES // _MEGABYTE
    return f"skipped {name} ({measured} exceeds the {limit} MB limit for one document)"


def _download_bounded(url: str, name: str) -> tuple[bytes | None, str]:
    """Stream one attachment into memory, refusing at the cap.

    Bounded twice: on the declared size before a request is spent (in the
    caller) and again here while reading, because a declared size can be wrong
    or absent. Nothing is written to disk -- the bytes are extracted, scrubbed
    at ingest, and dropped.
    """
    response, error = canvas_client.canvas_stream_get(url, _DOWNLOAD_TIMEOUT)
    if response is None:
        return None, f"could not download {name} from Canvas ({error})"
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=_CHUNK_BYTES):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_ATTACHMENT_BYTES:
                return None, _too_large_note(name, total / _MEGABYTE)
            chunks.append(chunk)
    except Exception as exc:  # a stream can fail partway through
        return None, f"could not download {name} from Canvas ({type(exc).__name__})"
    finally:
        response.close()
    return b"".join(chunks), ""


def submission_text(extracted: str) -> str:
    """Strip `_docx_segments`' own annotations, leaving the student's text."""
    blocks = []
    for block in str(extracted or "").split("\n\n"):
        lines = [line for line in block.split("\n")
                 if not _IMAGE_MARKER.match(line.strip())]
        cleaned = _BLOCK_ANNOTATION.sub("", "\n".join(lines)).strip()
        if cleaned:
            blocks.append(cleaned)
    return "\n\n".join(blocks)


def text_for(course_id, assignment_id, canvas_user_id) -> AcquiredText:
    """Fetch, choose, download and extract one submission's uploaded essay.

    Never raises for a per-student problem: a missing submission, a failed
    fetch, a non-DOCX upload, an oversized file and an unreadable document all
    come back as an `AcquiredText` with an empty `text`, a named reason, and an
    `outcome` the driver counts, so one student's problem cannot end the run
    for the rest of the class.
    """
    # "manual" is the priority `mirror_service` gives a teacher-triggered pass;
    # `operational_log` accepts a fixed set and rejects anything else outright.
    with canvas_client.canvas_get_telemetry("dailywriting.ingest", "manual"):
        return _acquire(course_id, assignment_id, canvas_user_id)


def _acquire(course_id, assignment_id, canvas_user_id) -> AcquiredText:
    path = (f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            f"/submissions/{canvas_user_id}")
    data, error = canvas_client.canvas_get(path)
    if error or not isinstance(data, dict):
        reason = error or "Canvas returned no submission record"
        return AcquiredText(notes=(f"could not read this submission from Canvas ({reason})",),
                            outcome="transport_failed")

    attachment, notes = _choose_docx(data.get("attachments") or [])
    if attachment is None:
        notes.append("no Word document on this submission")
        return AcquiredText(notes=tuple(notes), outcome="no_docx")

    name = _filename(attachment)
    declared = _declared_megabytes(attachment.get("size"))
    if declared is not None and declared * _MEGABYTE > MAX_ATTACHMENT_BYTES:
        notes.append(_too_large_note(name, declared))
        return AcquiredText(notes=tuple(notes), outcome="too_large")

    url = str(attachment.get("url") or "").strip()
    if not url:
        notes.append(f"skipped {name} (Canvas returned no download link)")
        return AcquiredText(notes=tuple(notes), outcome="transport_failed")

    data_bytes, failure = _download_bounded(url, name)
    if data_bytes is None:
        notes.append(failure)
        outcome = "too_large" if "exceeds the" in failure else "transport_failed"
        return AcquiredText(notes=tuple(notes), outcome=outcome)

    try:
        extracted, _media = student_attachments._docx_segments(data_bytes)
    except ValueError as exc:
        notes.append(f"could not read {name} as a Word document ({exc})")
        return AcquiredText(notes=tuple(notes), outcome="unreadable")

    text = submission_text(extracted)
    if not text:
        notes.append(f"{name} holds no readable writing")
        return AcquiredText(notes=tuple(notes), outcome="unreadable")

    return AcquiredText(text=text, notes=tuple(notes), outcome="extracted", source=name)
