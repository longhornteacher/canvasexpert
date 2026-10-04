"""Private, content-addressed preservation of exact Canvas submission bytes."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path

from api.mirror.evidence_schema import canonical_bytes, validate_digest
from api.platform_services import workspace


MAX_ORIGINAL_BYTES = 100 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


class ArchiveError(ValueError):
    """A private original is missing, inconsistent, or outside the archive bounds."""


def _root(workspace_root: str | Path) -> Path:
    value = workspace.canvas_mirror_originals_root(workspace_root)
    if value is None:
        raise ArchiveError("workspace_unconfigured")
    return Path(value).resolve()


def blob_path(workspace_root: str | Path, digest: str) -> Path:
    validate_digest(digest)
    return _root(workspace_root) / "blobs" / digest[:2] / f"{digest}.zip"


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            size += len(chunk)
            if size > MAX_ORIGINAL_BYTES:
                raise ArchiveError("original_too_large")
            digest.update(chunk)
    return digest.hexdigest(), size


def recover_original(workspace_root: str | Path, digest: str) -> bytes:
    """Validate ZIP structure, CRC, byte bound, and recovered SHA-256."""
    path = blob_path(workspace_root, digest)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != "payload":
                raise ArchiveError("invalid_archive_members")
            member = members[0]
            if (member.is_dir() or member.compress_type != zipfile.ZIP_STORED
                    or member.flag_bits & 1 or member.file_size > MAX_ORIGINAL_BYTES
                    or member.compress_size != member.file_size):
                raise ArchiveError("invalid_archive_member")
            output = io.BytesIO()
            recovered = hashlib.sha256()
            count = 0
            with archive.open(member, "r") as payload:
                for chunk in iter(lambda: payload.read(CHUNK_BYTES), b""):
                    count += len(chunk)
                    if count > MAX_ORIGINAL_BYTES:
                        raise ArchiveError("original_too_large")
                    recovered.update(chunk)
                    output.write(chunk)
            if count != member.file_size or recovered.hexdigest() != digest:
                raise ArchiveError("original_digest_mismatch")
            return output.getvalue()
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ArchiveError("invalid_archive") from exc


def archive_original(workspace_root: str | Path, source_path: str | Path,
                     *, expected_digest: str | None = None) -> str:
    """Publish one deterministic ZIP without replacing a divergent existing blob."""
    source = Path(source_path).resolve(strict=True)
    digest, size = _hash_file(source)
    if expected_digest is not None and validate_digest(expected_digest) != digest:
        raise ArchiveError("source_digest_mismatch")
    destination = blob_path(workspace_root, digest)
    if destination.exists():
        recover_original(workspace_root, digest)
        return digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".archive-",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        member = zipfile.ZipInfo("payload", date_time=(1980, 1, 1, 0, 0, 0))
        member.compress_type = zipfile.ZIP_STORED
        member.external_attr = 0o600 << 16
        member.create_system = 3
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED,
                             allowZip64=True) as archive, source.open("rb") as original:
            with archive.open(member, "w", force_zip64=True) as payload:
                for chunk in iter(lambda: original.read(CHUNK_BYTES), b""):
                    payload.write(chunk)
        if _hash_file(source) != (digest, size):
            raise ArchiveError("source_changed")
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
        recover_original(workspace_root, digest)
        return digest
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def associate_original(workspace_root: str | Path, *, original_digest: str,
                       source_key: str, course_id: str, assignment_id: str,
                       pseudonym: str, attempt: int | None, filename: str,
                       media_type: str, source_digest: str) -> str:
    """Preserve private source/filename relations separately from the ZIP."""
    recover_original(workspace_root, original_digest)
    validate_digest(source_key)
    validate_digest(source_digest)
    if not all(isinstance(value, str) and value for value in
               (course_id, assignment_id, pseudonym, filename, media_type)):
        raise ArchiveError("invalid_association")
    if attempt is not None and (type(attempt) is not int or attempt < 1):
        raise ArchiveError("invalid_association")
    record = {
        "schema_version": 1, "original_digest": original_digest,
        "source_key": source_key, "course_id": course_id,
        "assignment_id": assignment_id, "pseudonym": pseudonym,
        "attempt": attempt, "filename": filename, "media_type": media_type,
        "source_digest": source_digest,
    }
    payload = canonical_bytes(record)
    digest = hashlib.sha256(payload).hexdigest()
    destination = _root(workspace_root) / "associations" / digest[:2] / f"{digest}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".association-",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
        if destination.read_bytes() != payload:
            raise ArchiveError("association_corrupt") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return digest
