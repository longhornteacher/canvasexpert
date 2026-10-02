"""Durable, private evidence captured from ordinary assignment submissions.

The disposable CanvasMirror remains the source for current membership and grades.
This archive only records observed drafts and original upload bytes for review.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import copy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from api import feedback_scrub, storage_support
from api.platform_services import workspace


VERSION = 1
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_PASS_BYTES = 200 * 1024 * 1024
MAX_PASS_DOWNLOADS = 20
CHUNK_BYTES = 64 * 1024
_HEX = re.compile(r"^[0-9a-f]{64}$")


class CaptureBudget:
    def __init__(self):
        self.bytes_streamed = 0
        self.download_attempts = 0


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _directory(root, course_id, assignment_id):
    archive = workspace.archive_dir(root)
    if not archive:
        raise ValueError("workspace unavailable")
    path = workspace.bounded_join(
        archive, "Submission History", workspace.safe_id(course_id),
        workspace.safe_id(assignment_id),
    )
    resolved_root = os.path.abspath(workspace.extended_path(archive))
    resolved_path = os.path.abspath(workspace.extended_path(path))
    if os.path.commonpath([resolved_root, resolved_path]) != resolved_root:
        raise ValueError("archive path escaped workspace")
    return path


def _manifest_path(root, course_id, assignment_id):
    return os.path.join(_directory(root, course_id, assignment_id), "history.v1.json")


def _manifest_lock(root, course_id, assignment_id):
    return Path(_manifest_path(root, course_id, assignment_id) + ".lock")


def _empty(course_id, assignment_id):
    return {"schema_version": VERSION, "course_id": str(course_id),
            "assignment_id": str(assignment_id), "revision": 0,
            "updated_at": "", "attempts": {}}


def _read(root, course_id, assignment_id):
    path = _manifest_path(root, course_id, assignment_id)
    if not os.path.exists(workspace.extended_path(path)):
        return _empty(course_id, assignment_id)
    try:
        with open(workspace.extended_path(path), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        raise ValueError("history manifest unreadable") from exc
    if (not isinstance(data, dict) or data.get("schema_version") != VERSION
            or data.get("course_id") != str(course_id)
            or data.get("assignment_id") != str(assignment_id)
            or not isinstance(data.get("attempts"), dict)
            or not isinstance(data.get("revision"), int)):
        raise ValueError("history manifest invalid")
    for key, attempt in data["attempts"].items():
        if not isinstance(attempt, dict) or not isinstance(attempt.get("observations"), list):
            raise ValueError("history manifest invalid")
        for observation in attempt["observations"]:
            if not isinstance(observation, dict) or not _HEX.fullmatch(str(observation.get("digest") or "")):
                raise ValueError("history manifest invalid")
    return data


def _write(root, course_id, assignment_id, manifest):
    manifest["revision"] = int(manifest.get("revision") or 0) + 1
    manifest["updated_at"] = _now()
    storage_support.atomic_write_json(
        Path(_manifest_path(root, course_id, assignment_id)), manifest)


def _safe_filename(name):
    value = os.path.basename(str(name or "upload"))
    return workspace.safe_component(value, max_len=120, fallback="upload")


def _origin_matches(url, origin):
    try:
        target = urlsplit(str(url))
        expected = urlsplit(str(origin))
        return (target.scheme.lower() == "https" and expected.scheme.lower() == "https"
                and bool(target.hostname) and not target.username and not target.password
                and target.hostname.lower() == (expected.hostname or "").lower()
                and (target.port or 443) == (expected.port or 443))
    except (TypeError, ValueError):
        return False


def _file_descriptor(item):
    if not isinstance(item, dict):
        return None
    file_id = str(item.get("id") or "")
    url = item.get("url") if isinstance(item.get("url"), str) else ""
    filename = _safe_filename(item.get("filename") or item.get("display_name") or "upload")
    try:
        size = max(0, int(item.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    content_type = str(item.get("content-type") or item.get("content_type") or "")[:160]
    stable_key = file_id or _json_digest({"filename": filename, "size": size,
                                          "content_type": content_type})
    return {"file_id": file_id, "key": stable_key, "filename": filename,
            "size": size, "content_type": content_type, "url": url}


def _download(root, course_id, assignment_id, pseudonym, attempt_key, descriptor,
              file_entry, *, stream_get, canvas_origin, budget):
    if file_entry.get("status") == "captured" and _blob_valid(root, course_id, assignment_id, file_entry):
        return
    if descriptor["size"] > MAX_FILE_BYTES:
        file_entry.update(status="too_large", digest="", artifact_ref="")
        return
    if not descriptor["url"] or not descriptor["file_id"]:
        file_entry.update(status="unavailable", digest="", artifact_ref="")
        return
    if stream_get is None:
        if not file_entry.get("status"):
            file_entry.update(status="pending", digest="", artifact_ref="")
        return
    if not _origin_matches(descriptor["url"], canvas_origin):
        file_entry.update(status="foreign_origin", digest="", artifact_ref="")
        return
    if budget.download_attempts >= MAX_PASS_DOWNLOADS:
        file_entry.update(status="pending", digest="", artifact_ref="")
        return
    if budget.bytes_streamed >= MAX_PASS_BYTES:
        file_entry.update(status="pending", digest="", artifact_ref="")
        return
    budget.download_attempts += 1
    response = None
    temporary = None
    try:
        response, error = stream_get(descriptor["url"])
        if error or response is None:
            if str(error or "").casefold() == "cancelled":
                raise InterruptedError("submission evidence capture cancelled")
            file_entry.update(status="failed", digest="", artifact_ref="")
            return
        headers = getattr(response, "headers", {}) or {}
        length = headers.get("Content-Length") or headers.get("content-length")
        try:
            response_declared = int(length) if length is not None else 0
        except (TypeError, ValueError):
            response_declared = 0
        declared_values = [size for size in (descriptor["size"], response_declared) if size > 0]
        declared = min(declared_values) if declared_values else 0
        if any(size > MAX_FILE_BYTES for size in declared_values):
            file_entry.update(status="too_large", digest="", artifact_ref="")
            return
        directory = _directory(root, course_id, assignment_id)
        blob_dir = os.path.join(directory, "files")
        os.makedirs(workspace.extended_path(blob_dir), exist_ok=True)
        suffix = Path(descriptor["filename"]).suffix[:16]
        descriptor_fd, temporary = tempfile.mkstemp(
            prefix=".capture-", suffix=".tmp", dir=workspace.extended_path(blob_dir))
        digest = hashlib.sha256()
        count = 0
        with os.fdopen(descriptor_fd, "wb") as handle:
            chunks = response.iter_content(chunk_size=CHUNK_BYTES)
            for chunk in chunks:
                if not chunk:
                    continue
                count += len(chunk)
                budget.bytes_streamed += len(chunk)
                if count > MAX_FILE_BYTES:
                    file_entry.update(status="too_large", digest="", artifact_ref="")
                    return
                if declared and count > declared:
                    file_entry.update(status="failed", digest="", artifact_ref="")
                    return
                if budget.bytes_streamed > MAX_PASS_BYTES:
                    file_entry.update(status="pending", digest="", artifact_ref="")
                    return
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if declared and count != declared:
            file_entry.update(status="failed", digest="", artifact_ref="")
            return
        digest_hex = digest.hexdigest()
        final_path = os.path.join(blob_dir, f"{digest_hex}{suffix}")
        if (os.path.exists(workspace.extended_path(final_path))
                and _blob_valid(root, course_id, assignment_id,
                                {"digest": digest_hex})):
            os.unlink(temporary)
            temporary = None
            file_entry.update(status="captured", digest=digest_hex,
                              artifact_ref=digest_hex)
            return
        os.replace(temporary, workspace.extended_path(final_path))
        temporary = None
        file_entry.update(status="captured", digest=digest_hex,
                          artifact_ref=digest_hex)
    except (KeyboardInterrupt, SystemExit, InterruptedError):
        raise
    except Exception:
        file_entry.update(status="failed", digest="", artifact_ref="")
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _blob_valid(root, course_id, assignment_id, file_entry):
    digest = str(file_entry.get("digest") or "")
    if not _HEX.fullmatch(digest):
        return False
    directory = os.path.join(_directory(root, course_id, assignment_id), "files")
    try:
        names = os.listdir(workspace.extended_path(directory))
    except OSError:
        return False
    for name in names:
        if name.startswith(digest):
            path = os.path.join(directory, name)
            h = hashlib.sha256()
            try:
                with open(workspace.extended_path(path), "rb") as handle:
                    for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                        h.update(chunk)
                if h.hexdigest() == digest:
                    return True
            except OSError:
                continue
    return False


def _record_observation(manifest, pseudonym, attempt, submitted_at, body, files,
                        *, captured_at, root, course_id, assignment_id,
                        stream_get=None, canvas_origin="", budget=None):
    budget = budget or CaptureBudget()
    nonempty_body = body if isinstance(body, str) and body.strip() else ""
    if not nonempty_body and not files:
        return False
    attempt_key = f"{pseudonym}|{int(attempt)}|{submitted_at}"
    attempt_doc = manifest["attempts"].setdefault(attempt_key, {
        "pseudonym": str(pseudonym), "attempt": int(attempt),
        "submitted_at": str(submitted_at), "observations": [], "conflict": False,
    })
    file_entries = []
    status_snapshot = _json_digest(attempt_doc.get("files", []))
    for descriptor in files:
        stable = {key: descriptor[key] for key in ("key", "filename", "size", "content_type")}
        existing = next((item for item in attempt_doc.get("files", [])
                         if item.get("key") == descriptor["key"]), None)
        if existing is None and descriptor.get("file_id"):
            # A cache-seeded filename placeholder has no stable Canvas file
            # identity. Upgrade it in place when a later observation provides
            # that identity, and rebase its prior associations/digests.
            existing = next((item for item in attempt_doc.get("files", [])
                             if item.get("filename", "").casefold() ==
                             descriptor["filename"].casefold()
                             and item.get("status") == "unavailable"
                             and not item.get("digest")), None)
            if existing is not None:
                old_key = existing["key"]
                existing.update(stable)
                for observation in attempt_doc.get("observations", []):
                    if old_key in observation.get("file_keys", []):
                        observation["file_keys"] = [
                            descriptor["key"] if key == old_key else key
                            for key in observation["file_keys"]]
                        associated = [
                            {field: entry[field] for field in
                             ("key", "filename", "size", "content_type")}
                            for key in observation["file_keys"]
                            for entry in attempt_doc.get("files", [])
                            if entry.get("key") == key]
                        observation["digest"] = _json_digest({
                            "body": observation.get("body") or "",
                            "files": associated,
                        })
        if existing is None:
            existing = {**stable, "status": "pending", "digest": "", "artifact_ref": ""}
            attempt_doc.setdefault("files", []).append(existing)
        else:
            for field, value in stable.items():
                if value and not existing.get(field):
                    existing[field] = value
        _download(root, course_id, assignment_id, pseudonym, attempt_key,
                  descriptor, existing, stream_get=stream_get,
                  canvas_origin=canvas_origin, budget=budget)
        file_entries.append(stable)
    digest = _json_digest({"body": nonempty_body, "files": file_entries})
    if any(item.get("digest") == digest for item in attempt_doc["observations"]):
        return status_snapshot != _json_digest(attempt_doc.get("files", []))
    conflict = bool(attempt_doc["observations"] and (
        (nonempty_body and any(item.get("body") and item.get("body") != nonempty_body
                               for item in attempt_doc["observations"]))
        or (file_entries and any(item.get("file_keys") and item.get("file_keys") !=
                                 [file["key"] for file in files]
                                 for item in attempt_doc["observations"]))
    ))
    attempt_doc["conflict"] = bool(attempt_doc.get("conflict") or conflict)
    attempt_doc["observations"].append({
        "digest": digest, "captured_at": captured_at, "body": nonempty_body,
        "file_keys": [item["key"] for item in files], "conflict": conflict,
    })
    if nonempty_body and not attempt_doc.get("body"):
        attempt_doc["body"] = nonempty_body
    attempt_doc["captured_at"] = attempt_doc.get("captured_at") or captured_at
    return True


def capture_rows(course_id, assignment_id, rows, *, vault, root=None,
                 stream_get=None, canvas_origin="", budget=None):
    """Append evidence from raw Canvas rows before the disposable merge."""
    if not rows:
        return {"attempts": 0, "files": 0, "captured": 0, "pending": 0, "failed": 0}
    path = _manifest_path(root, course_id, assignment_id)
    os.makedirs(workspace.extended_path(os.path.dirname(path)), exist_ok=True)
    budget = budget or CaptureBudget()
    replacement_map = feedback_scrub.build_replacement_map(vault.entries(), set())
    prepared = []
    for row in rows:
        if not isinstance(row, dict) or row.get("user_id") in (None, ""):
            continue
        pseudonym = vault.get_or_assign(str(row["user_id"]))
        entries = [row] + list(row.get("submission_history") or [])
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("attempt") or not entry.get("submitted_at"):
                continue
            try:
                number = int(entry["attempt"])
            except (TypeError, ValueError):
                continue
            if number < 1:
                continue
            body = entry.get("body")
            if isinstance(body, str):
                body = feedback_scrub.scrub_text(body, replacement_map)
            else:
                body = ""
            files = [d for d in (_file_descriptor(item) for item in (entry.get("attachments") or [])) if d]
            prepared.append((pseudonym, number, str(entry["submitted_at"]), body, files))

    changed = False
    with storage_support.interprocess_lock(_manifest_lock(root, course_id, assignment_id)):
        manifest = _read(root, course_id, assignment_id)
        for pseudonym, number, submitted_at, body, files in prepared:
            added = _record_observation(
                manifest, pseudonym, number, submitted_at, body, files,
                captured_at=_now(), root=root, course_id=course_id,
                assignment_id=assignment_id, stream_get=None,
            )
            changed = changed or added
        if changed:
            _write(root, course_id, assignment_id, manifest)

    # Never hold the CanvasMirror or archive manifest lock during transport.
    # Re-read and rebase each status update after a download so concurrent
    # captures cannot replace observations written while this stream ran.
    if stream_get is not None:
        for pseudonym, number, submitted_at, _body, files in prepared:
            attempt_key = f"{pseudonym}|{number}|{submitted_at}"
            for descriptor in files:
                with storage_support.interprocess_lock(_manifest_lock(root, course_id, assignment_id)):
                    latest = _read(root, course_id, assignment_id)
                    attempt_doc = latest["attempts"].get(attempt_key) or {}
                    current = next((item for item in attempt_doc.get("files", [])
                                    if item.get("key") == descriptor["key"]), None)
                    if current is None or (current.get("status") == "captured"
                                           and _blob_valid(root, course_id, assignment_id, current)):
                        continue
                    working = copy.deepcopy(current)
                _download(root, course_id, assignment_id, pseudonym, attempt_key,
                          descriptor, working, stream_get=stream_get,
                          canvas_origin=canvas_origin, budget=budget)
                with storage_support.interprocess_lock(_manifest_lock(root, course_id, assignment_id)):
                    latest = _read(root, course_id, assignment_id)
                    attempt_doc = latest["attempts"].get(attempt_key) or {}
                    current = next((item for item in attempt_doc.get("files", [])
                                    if item.get("key") == descriptor["key"]), None)
                    if current is None:
                        continue
                    if current.get("status") == "captured" and _blob_valid(
                            root, course_id, assignment_id, current):
                        continue
                    updated = {**current}
                    updated.update({key: working.get(key) for key in
                                    ("status", "digest", "artifact_ref")})
                    if updated != current:
                        attempt_doc["files"][attempt_doc["files"].index(current)] = updated
                        _write(root, course_id, assignment_id, latest)
    return capture_summary(course_id, assignment_id, root=root)


def capture_cached_document(course_id, assignment_id, document, *, root=None):
    """Seed durable history from a validated existing mirror document."""
    path = _manifest_path(root, course_id, assignment_id)
    os.makedirs(workspace.extended_path(os.path.dirname(path)), exist_ok=True)
    changed = False
    with storage_support.interprocess_lock(_manifest_lock(root, course_id, assignment_id)):
        manifest = _read(root, course_id, assignment_id)
        for pseudonym, submission in (document.get("submissions") or {}).items():
            for attempt in (submission.get("attempts") or {}).values():
                if not isinstance(attempt, dict) or not attempt.get("submitted_at"):
                    continue
                try:
                    number = int(attempt.get("attempt"))
                except (TypeError, ValueError):
                    continue
                attempt_key = f"{pseudonym}|{number}|{attempt['submitted_at']}"
                archived_attempt = manifest["attempts"].get(attempt_key)
                names = [str(name) for name in attempt.get("attachment_names") or [] if name]
                descriptors = []
                for name in names:
                    known = next((item for item in (archived_attempt or {}).get("files", [])
                                  if item.get("filename", "").casefold() == name.casefold()), None)
                    if known:
                        descriptors.append({
                            "file_id": "", "url": "", "key": known["key"],
                            "filename": known["filename"], "size": known["size"],
                            "content_type": known["content_type"],
                        })
                    else:
                        descriptors.append(_file_descriptor({"filename": name}))
                added = _record_observation(
                    manifest, pseudonym, number, str(attempt["submitted_at"]),
                    attempt.get("body") or "", descriptors, captured_at=_now(),
                    root=root, course_id=course_id, assignment_id=assignment_id,
                )
                # Cached filenames have no stable Canvas file metadata or bytes.
                for descriptor in descriptors:
                    entry = next(item for item in manifest["attempts"][
                        f"{pseudonym}|{number}|{attempt['submitted_at']}"].get("files", [])
                        if item["key"] == descriptor["key"])
                    if entry.get("status") == "pending":
                        entry["status"] = "unavailable"
                changed = changed or added
        if changed:
            _write(root, course_id, assignment_id, manifest)
    return changed


def read_history(course_id, assignment_id, *, root=None):
    with storage_support.interprocess_lock(_manifest_lock(root, course_id, assignment_id)):
        return _read(root, course_id, assignment_id)


def capture_summary(course_id, assignment_id, *, root=None):
    manifest = read_history(course_id, assignment_id, root=root)
    files = [item for attempt in manifest["attempts"].values()
             for item in attempt.get("files", [])]
    return {"attempts": sum(len(attempt.get("observations", []))
                            for attempt in manifest["attempts"].values()),
            "files": len(files),
            "captured": sum(item.get("status") == "captured" for item in files),
            "pending": sum(item.get("status") == "pending" for item in files),
            "failed": sum(item.get("status") in {"failed", "foreign_origin", "too_large"}
                          for item in files)}


def file_path(course_id, assignment_id, artifact_ref, *, root=None):
    """Resolve a digest reference for trusted local text extraction only."""
    digest = str(artifact_ref or "")
    if not _HEX.fullmatch(digest):
        return None
    manifest = _read(root, course_id, assignment_id)
    if not any(item.get("artifact_ref") == digest and item.get("status") == "captured"
               for attempt in manifest["attempts"].values()
               for item in attempt.get("files", [])):
        return None
    directory = os.path.join(_directory(root, course_id, assignment_id), "files")
    try:
        for name in os.listdir(workspace.extended_path(directory)):
            if name.startswith(digest):
                path = os.path.join(directory, name)
                if _blob_valid(root, course_id, assignment_id,
                               {"digest": digest}):
                    return path
    except OSError:
        pass
    return None
