"""Private, local-only handling for ordinary Canvas media-recording evidence.

This module deliberately knows nothing about Canvas transport or sessions.  It
accepts an already-finalized original, validates it with local ffprobe, and
creates the single canonical audio derivative used by the review queue.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_MEDIA_SECONDS = 15 * 60


def held(code: str, message: str, *, filename: str = "recording", item_id: str = "", attempt=1) -> dict:
    """Return a stable, URL-free held evidence record."""
    return {
        "filename": filename or "recording",
        "item_id": str(item_id or ""),
        "item_link": str(item_id or ""),
        "attempt": attempt,
        "download_status": "failed",
        "extraction_status": "held",
        "ai_eligible": False,
        "local_only": True,
        "media_recording": True,
        "analysis_unavailable": True,
        "error_code": code,
        "error_message": message,
        "warnings": [],
    }


def normalize_media_comment(comment: object, *, attempt=1) -> tuple[dict | None, dict | None]:
    """Validate the documented Canvas MediaComment shape without retaining URL.

    The source object is returned only to the in-memory caller.  The public
    evidence metadata never contains its ``url``.
    """
    if not isinstance(comment, dict):
        return None, held("media_source_missing", "Canvas did not provide a media recording source.")
    media_id = comment.get("media_id")
    url = comment.get("url")
    media_category = str(comment.get("media_type") or "").strip().lower()
    content_type = str(comment.get("content-type") or "").strip().lower().split(";", 1)[0]
    filename = str(comment.get("display_name") or "recording")
    if not media_id:
        return None, held("media_id_missing", "Canvas did not provide an identity for this recording.", filename=filename, attempt=attempt)
    if not url or not isinstance(url, str):
        return None, held("media_source_missing", "Canvas did not provide a media recording source.", filename=filename, item_id=str(media_id), attempt=attempt)
    # Canvas MediaComment uses ``media_type: audio|video`` as its category and
    # ``content-type: audio/mp4`` (or equivalent) for the MIME type.  Accept
    # that documented pair, while retaining support for a MIME-valued category
    # only when it agrees with its audio/video top-level type.
    mime_category = content_type.split("/", 1)[0] if "/" in content_type else ""
    category = media_category if media_category in {"audio", "video"} else ""
    if not category and media_category.startswith(("audio/", "video/")):
        category = media_category.split("/", 1)[0]
    if not category and mime_category in {"audio", "video"}:
        category = mime_category
    if category not in {"audio", "video"} or (mime_category and mime_category != category):
        return None, held("media_type_unsupported", "Canvas reported an unsupported media recording type.", filename=filename, item_id=str(media_id), attempt=attempt)
    return {
        "url": url,
        "media_id": str(media_id),
        "filename": filename,
        "declared_media_type": content_type or media_category,
        "content_indicator": {key: comment.get(key) for key in ("media_id", "display_name", "media_type", "content-type") if comment.get(key) not in (None, "")},
    }, None


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stream_key(item_id: object) -> str:
    """Opaque browser token for a Canvas media identity; never reversible UI data."""
    return hashlib.sha256(str(item_id or "").encode("utf-8")).hexdigest()[:24]


def _command(name: str) -> str | None:
    return shutil.which(name)


def _probe(path: str) -> tuple[float | None, bool, str | None]:
    executable = _command("ffprobe")
    if not executable:
        return None, False, "ffprobe_missing"
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
             "-of", "json", path],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if result.returncode:
            return None, False, "invalid_media"
        payload = json.loads(result.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration"))
        audio = any(stream.get("codec_type") == "audio" for stream in (payload.get("streams") or []))
        return duration, audio, None
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None, False, "invalid_media"


def finalize_recording(*, source: dict, original_path: str, canonical_path: str) -> dict:
    """Validate one finalized original and write a canonical PCM16 mono WAV.

    No media bytes or source URL leave this function.  A failure retains the
    original whenever it was already finalized, so the teacher can review it
    locally through existing evidence tools.
    """
    filename = str(source.get("filename") or "recording")
    item_id = str(source.get("media_id") or "")
    attempt = source.get("attempt", 1)
    if not os.path.isfile(original_path):
        return held("media_transfer_interrupted", "The recording transfer did not finish.", filename=filename, item_id=item_id, attempt=attempt)
    actual_size = os.path.getsize(original_path)
    if actual_size > MAX_MEDIA_BYTES:
        return held("media_size_exceeded", "The recording exceeds the 100 MiB per-student limit.", filename=filename, item_id=item_id, attempt=attempt)
    duration, has_audio, probe_error = _probe(original_path)
    if probe_error == "ffprobe_missing":
        return held("ffprobe_missing", "Local media validation is unavailable.", filename=filename, item_id=item_id, attempt=attempt)
    if probe_error:
        return held("media_container_invalid", "The recording could not be validated locally.", filename=filename, item_id=item_id, attempt=attempt)
    if not has_audio:
        return held("media_audio_missing", "The submitted video has no audio track.", filename=filename, item_id=item_id, attempt=attempt)
    if duration is None or duration <= 0 or duration > MAX_MEDIA_SECONDS:
        return held("media_duration_exceeded", "The recording exceeds the 15 minute limit.", filename=filename, item_id=item_id, attempt=attempt)
    executable = _command("ffmpeg")
    if not executable:
        return held("ffmpeg_missing", "Local audio conversion is unavailable.", filename=filename, item_id=item_id, attempt=attempt)
    partial = canonical_path + ".partial"
    try:
        Path(canonical_path).parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(partial):
            os.unlink(partial)
        result = subprocess.run(
            [executable, "-y", "-v", "error", "-i", original_path, "-map", "0:a:0", "-vn",
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-map_metadata", "-1", "-f", "wav", partial],
            capture_output=True, check=False, timeout=120,
        )
        if result.returncode or not os.path.isfile(partial) or not os.path.getsize(partial):
            return held("media_conversion_failed", "The recording audio could not be converted locally.", filename=filename, item_id=item_id, attempt=attempt)
        os.replace(partial, canonical_path)
    except (OSError, subprocess.TimeoutExpired):
        return held("media_conversion_failed", "The recording audio could not be converted locally.", filename=filename, item_id=item_id, attempt=attempt)
    finally:
        try:
            if os.path.exists(partial):
                os.unlink(partial)
        except OSError:
            pass
    return {
        "filename": filename,
        "item_id": item_id,
        "item_link": item_id,
        "attempt": attempt,
        "download_status": "downloaded",
        "extraction_status": "validated",
        "ai_eligible": False,
        "local_only": True,
        "media_recording": True,
        "analysis_unavailable": True,
        "declared_media_type": source.get("declared_media_type"),
        "actual_size": actual_size,
        "duration_seconds": round(duration, 3),
        "original_path": original_path,
        "canonical_path": canonical_path,
        "original_sha256": _sha256(original_path),
        "canonical_sha256": _sha256(canonical_path),
        "content_indicator": source.get("content_indicator") or {},
        "warnings": [],
    }


def review_metadata(record: dict) -> dict:
    """The only media projection permitted into a browser session."""
    allowed = ("filename", "attempt", "download_status", "extraction_status",
               "media_recording", "analysis_unavailable", "duration_seconds", "error_code", "error_message")
    projected = {key: record[key] for key in allowed if key in record}
    projected["stream_key"] = stream_key(record.get("item_id"))
    return projected
