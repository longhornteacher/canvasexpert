"""Durable, bounded, resumable attachment-capture jobs.

The synchronized safe store holds only pseudonymized attachment *associations*
(an opaque key, a status, and — once captured — the original content digest).
The live queue is machine-local private control state: it is never synced, and
it can be rebuilt on another computer from the published association facts.

This module owns the queue state machine only. It performs no Canvas I/O and
writes no safe record itself: the caller injects a fresh URL resolver (so an
expired signed URL is reacquired through CE rather than persisted), the
coordinated stream transport, and the archive/publication callbacks. That keeps
the download path on the existing coordinated transport and the privacy
boundary on the existing publisher.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Callable

from api.mirror.evidence_schema import ATTACHMENT_STATUSES, canonical_bytes


# Resumable chunk bounds, not a permanent first-N cap. Remaining work persists.
MAX_DOWNLOADS_PER_CHUNK = 20
MAX_BYTES_PER_CHUNK = 200 * 1024 * 1024
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (30, 120, 600, 1800)
TERMINAL_STATUSES = frozenset({"captured", "too_large", "unavailable", "foreign_origin"})
RETRYABLE_STATUSES = frozenset({"pending", "failed"})
CHUNK_BYTES = 64 * 1024


class JobError(ValueError):
    """A value-free job-queue refusal."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def job_id(source_key: str, course_id: str, assignment_id: str, pseudonym: str,
           attempt: int | None, attachment_key: str) -> str:
    """Deterministic identity so another computer rebuilds the same job row."""
    identity = {"source_key": source_key, "course_id": str(course_id),
                "assignment_id": str(assignment_id), "pseudonym": pseudonym,
                "attempt": attempt, "attachment_key": attachment_key}
    return hashlib.sha256(canonical_bytes(identity)).hexdigest()


@dataclass(frozen=True)
class AttachmentJob:
    job_id: str
    source_key: str
    course_id: str
    assignment_id: str
    pseudonym: str
    attempt: int | None
    attachment_key: str
    media_type: str
    size: int
    status: str
    digest: str | None
    attempts: int
    next_attempt_at: str | None
    last_error: str | None
    filename: str = ""
    file_id: str = ""


def _row_to_job(row: sqlite3.Row) -> AttachmentJob:
    return AttachmentJob(
        job_id=row["job_id"], source_key=row["source_key"], course_id=row["course_id"],
        assignment_id=row["assignment_id"], pseudonym=row["pseudonym"],
        attempt=row["attempt"], attachment_key=row["attachment_key"],
        media_type=row["media_type"], size=row["size"], status=row["status"],
        digest=row["digest"], attempts=row["attempts"],
        next_attempt_at=row["next_attempt_at"], last_error=row["last_error"],
        filename=row["filename"], file_id=row["file_id"],
    )


class AttachmentJobStore:
    """One machine-local private queue; never a synchronized authority."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=2000")
        self._initialize(db)
        return db

    @staticmethod
    def _initialize(db: sqlite3.Connection) -> None:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS attachment_jobs(
            job_id TEXT PRIMARY KEY,
            source_key TEXT NOT NULL,
            course_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            pseudonym TEXT NOT NULL,
            attempt INTEGER,
            attachment_key TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size INTEGER NOT NULL,
            status TEXT NOT NULL,
            digest TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            last_error TEXT,
            filename TEXT NOT NULL DEFAULT '',
            file_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS job_claimable
            ON attachment_jobs(status, next_attempt_at, created_at, job_id);
        """)
        db.commit()

    def ensure(self, *, source_key: str, course_id: str, assignment_id: str,
               pseudonym: str, attempt: int | None, attachment_key: str,
               media_type: str, size: int, status: str = "pending",
               digest: str | None = None, filename: str = "", file_id: str = "",
               now: str | None = None) -> AttachmentJob:
        """Insert a job if absent; never downgrade a terminal or captured row."""
        if status not in ATTACHMENT_STATUSES:
            raise JobError("invalid_status")
        if type(size) is not int or size < 0:
            raise JobError("invalid_size")
        if attempt is not None and (type(attempt) is not int or attempt < 1):
            raise JobError("invalid_attempt")
        identifier = job_id(source_key, course_id, assignment_id, pseudonym,
                            attempt, attachment_key)
        stamp = now or _now()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM attachment_jobs WHERE job_id=?",
                                  (identifier,)).fetchone()
            if existing is not None:
                return _row_to_job(existing)
            db.execute(
                "INSERT INTO attachment_jobs(job_id,source_key,course_id,assignment_id,"
                "pseudonym,attempt,attachment_key,media_type,size,status,digest,attempts,"
                "next_attempt_at,last_error,filename,file_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,0,NULL,NULL,?,?,?,?)",
                (identifier, source_key, str(course_id), str(assignment_id), pseudonym,
                 attempt, attachment_key, media_type, size, status, digest,
                 str(filename or ""), str(file_id or ""), stamp, stamp),
            )
            db.commit()
            return _row_to_job(db.execute("SELECT * FROM attachment_jobs WHERE job_id=?",
                                          (identifier,)).fetchone())

    def reconstruct_from_facts(self, snapshot, *, now: str | None = None) -> int:
        """Rebuild queue rows from published association facts on another machine.

        Only facts are authoritative; a live queue database is never synced.
        Existing rows are preserved, so a local capture is not reset by a peer.
        """
        created = 0
        for fact in snapshot.facts.values():
            if fact.get("kind") != "attachment":
                continue
            payload = fact["payload"]
            before = self._exists(job_id(fact["source_key"], fact["course_id"],
                                         payload["assignment_id"], payload["pseudonym"],
                                         payload.get("attempt"), payload["attachment_key"]))
            self.ensure(
                source_key=fact["source_key"], course_id=fact["course_id"],
                assignment_id=payload["assignment_id"], pseudonym=payload["pseudonym"],
                attempt=payload.get("attempt"), attachment_key=payload["attachment_key"],
                media_type=payload.get("media_type") or "application/octet-stream",
                size=int(payload.get("size") or 0), status=payload.get("status") or "pending",
                digest=payload.get("original_digest"), now=now,
            )
            created += 0 if before else 1
        return created

    def _exists(self, identifier: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM attachment_jobs WHERE job_id=?",
                              (identifier,)).fetchone() is not None

    def get(self, identifier: str) -> AttachmentJob | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM attachment_jobs WHERE job_id=?",
                             (identifier,)).fetchone()
            return _row_to_job(row) if row else None

    def claim(self, *, now: str | None = None, limit: int = MAX_DOWNLOADS_PER_CHUNK,
              max_bytes: int = MAX_BYTES_PER_CHUNK) -> tuple[AttachmentJob, ...]:
        """Return a bounded, fair chunk of retryable jobs whose backoff elapsed."""
        if type(limit) is not int or not 1 <= limit <= 256:
            raise JobError("invalid_limit")
        if type(max_bytes) is not int or max_bytes < 0:
            raise JobError("invalid_bytes")
        moment = _parse(now or _now())
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM attachment_jobs WHERE status IN ('pending','failed') "
                "ORDER BY created_at, job_id").fetchall()
        selected, total = [], 0
        for row in rows:
            job = _row_to_job(row)
            due = _parse(job.next_attempt_at)
            if due is not None and moment is not None and due > moment:
                continue
            if len(selected) >= limit:
                break
            if job.size > max_bytes and selected:
                continue
            selected.append(job)
            total += job.size
            if total >= max_bytes:
                break
        return tuple(selected)

    def record(self, identifier: str, *, status: str, digest: str | None = None,
               error: str | None = None, now: str | None = None) -> AttachmentJob:
        """Persist one durable step; terminal rows never regress to retryable."""
        if status not in ATTACHMENT_STATUSES:
            raise JobError("invalid_status")
        stamp = now or _now()
        with self._connect() as db:
            row = db.execute("SELECT * FROM attachment_jobs WHERE job_id=?",
                             (identifier,)).fetchone()
            if row is None:
                raise JobError("unknown_job")
            current = _row_to_job(row)
            if current.status in TERMINAL_STATUSES and status not in TERMINAL_STATUSES:
                return current
            attempts = current.attempts
            next_attempt = None
            if status == "failed":
                attempts += 1
                if attempts >= MAX_ATTEMPTS:
                    status = "failed"
                    next_attempt = None
                else:
                    delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
                    next_attempt = (datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                                    + timedelta(seconds=delay)).strftime("%Y-%m-%dT%H:%M:%SZ")
            db.execute(
                "UPDATE attachment_jobs SET status=?,digest=COALESCE(?,digest),attempts=?,"
                "next_attempt_at=?,last_error=?,updated_at=? WHERE job_id=?",
                (status, digest, attempts, next_attempt, error, stamp, identifier),
            )
            db.commit()
            return _row_to_job(db.execute("SELECT * FROM attachment_jobs WHERE job_id=?",
                                          (identifier,)).fetchone())

    def summary(self) -> dict:
        with self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) n FROM attachment_jobs "
                              "GROUP BY status").fetchall()
        counts = {row["status"]: row["n"] for row in rows}
        return {"total": sum(counts.values()), "by_status": counts,
                "pending": counts.get("pending", 0) + counts.get("failed", 0),
                "captured": counts.get("captured", 0)}


def _origin_matches(url: str, origin: str) -> bool:
    from urllib.parse import urlsplit
    try:
        target = urlsplit(str(url))
        expected = urlsplit(str(origin))
        return (target.scheme.lower() == "https" and expected.scheme.lower() == "https"
                and bool(target.hostname) and not target.username and not target.password
                and target.hostname.lower() == (expected.hostname or "").lower()
                and (target.port or 443) == (expected.port or 443))
    except (TypeError, ValueError):
        return False


def run_attachment_chunk(
    store: AttachmentJobStore,
    *,
    resolve_url: Callable[[AttachmentJob], str | None],
    stream_get: Callable[[str], tuple],
    canvas_origin: str,
    original_exists: Callable[[str], bool],
    store_original: Callable[[AttachmentJob, str, str], None],
    staging_dir: str | Path,
    now: str | None = None,
    limit: int = MAX_DOWNLOADS_PER_CHUNK,
    max_bytes: int = MAX_BYTES_PER_CHUNK,
) -> dict:
    """Process one bounded chunk; one failure never stops sibling jobs.

    ``resolve_url`` reacquires a fresh URL through CE (never a persisted one).
    ``store_original(job, digest, temp_path)`` archives the exact bytes, records
    the private association, and publishes the safe association fact. A job whose
    digest is already archived is reused without a second download.
    """
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    jobs = store.claim(now=now, limit=limit, max_bytes=max_bytes)
    processed = captured = failed = skipped = 0
    for job in jobs:
        processed += 1
        # A completed, validated original is reused after any restart.
        if job.digest and original_exists(job.digest):
            try:
                store_original(job, job.digest, "")
            except Exception:
                store.record(job.job_id, status="failed", error="publication_failed", now=now)
                failed += 1
                continue
            store.record(job.job_id, status="captured", digest=job.digest, now=now)
            captured += 1
            continue
        if job.size > MAX_FILE_BYTES:
            store.record(job.job_id, status="too_large", error="over_file_limit", now=now)
            skipped += 1
            continue
        url = resolve_url(job)
        if not url:
            store.record(job.job_id, status="unavailable", error="url_unavailable", now=now)
            skipped += 1
            continue
        if not _origin_matches(url, canvas_origin):
            store.record(job.job_id, status="foreign_origin", error="foreign_origin", now=now)
            skipped += 1
            continue
        response = None
        temporary = None
        try:
            response, error = stream_get(url)
            if error or response is None:
                if str(error or "").casefold() == "cancelled":
                    raise InterruptedError("attachment capture cancelled")
                store.record(job.job_id, status="failed", error="transport_failed", now=now)
                failed += 1
                continue
            headers = getattr(response, "headers", {}) or {}
            length = headers.get("Content-Length") or headers.get("content-length")
            try:
                declared = int(length) if length is not None else 0
            except (TypeError, ValueError):
                declared = 0
            if declared > MAX_FILE_BYTES:
                store.record(job.job_id, status="too_large", error="over_file_limit", now=now)
                skipped += 1
                continue
            descriptor, temporary = _temp_file(staging)
            digest = hashlib.sha256()
            count = 0
            with os.fdopen(descriptor, "wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                    if not chunk:
                        continue
                    count += len(chunk)
                    if count > MAX_FILE_BYTES:
                        raise JobError("over_file_limit")
                    if declared and count > declared:
                        raise JobError("size_mismatch")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if declared and count != declared:
                raise JobError("size_mismatch")
            digest_hex = digest.hexdigest()
            # Record the digest before archiving so a crash after archive reuses it.
            store.record(job.job_id, status="pending", digest=digest_hex, now=now)
            store_original(job, digest_hex, temporary)
            store.record(job.job_id, status="captured", digest=digest_hex, now=now)
            captured += 1
        except (KeyboardInterrupt, SystemExit, InterruptedError):
            raise
        except JobError as exc:
            store.record(job.job_id, status="too_large" if exc.args and exc.args[0] == "over_file_limit"
                         else "failed", error=str(exc.args[0]) if exc.args else "invalid", now=now)
            failed += 1
        except Exception:
            store.record(job.job_id, status="failed", error="capture_failed", now=now)
            failed += 1
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
    return {"processed": processed, "captured": captured, "failed": failed,
            "skipped": skipped, "remaining": store.summary()["pending"]}


def _temp_file(staging: Path) -> tuple[int, str]:
    import tempfile
    descriptor, name = tempfile.mkstemp(prefix=".attachment-", suffix=".tmp",
                                        dir=str(staging))
    return descriptor, name


def resolve_canvas_file_url(job: AttachmentJob, *, canvas_get) -> str | None:
    """Reacquire a fresh download URL for a Canvas file id through CE.

    Never persist or reuse an expiring signed URL; the stable file id is the
    only durable locator, and CE remains the sole Canvas client.
    """
    if not job.file_id or not str(job.file_id).isdecimal():
        return None
    data, error = canvas_get(f"/api/v1/files/{job.file_id}")
    if error or not isinstance(data, dict):
        return None
    url = data.get("url")
    return url if isinstance(url, str) and url else None


def make_original_sink(*, workspace_root, publisher, writer_key: str, run_id: str):
    """Return a ``store_original`` callback: archive, associate, then publish.

    The private association record keeps the filename/media-type relation; the
    safe fact carries only the opaque key and digest. A crash between archive and
    publication leaves a reusable blob, not loss.
    """
    from api.mirror.original_archive import archive_original, associate_original
    from api.mirror.evidence_acquisition import publish_captured_attachment

    def store_original(job: AttachmentJob, digest: str, temp_path: str) -> None:
        if temp_path:
            archive_original(workspace_root, temp_path, expected_digest=digest)
        associate_original(
            workspace_root, original_digest=digest, source_key=job.source_key,
            course_id=job.course_id, assignment_id=job.assignment_id,
            pseudonym=job.pseudonym, attempt=job.attempt,
            filename=job.filename or "upload", media_type=job.media_type,
            source_digest=job.attachment_key)
        publish_captured_attachment(publisher=publisher, job=job, digest=digest,
                                    writer_key=writer_key, run_id=run_id)

    return store_original


def enqueue_from_receipt(store: AttachmentJobStore, receipt, *, source_key: str,
                         pseudonym_for, now: str | None = None) -> int:
    """Ensure one job per observed attachment in a private acquisition receipt.

    ``pseudonym_for(raw_user_id)`` resolves the stable pseudonym through the
    vault; unresolved identities are skipped (their association fact is refused
    by the publisher too). Returns the number of newly created jobs.
    """
    from api.mirror.evidence_acquisition import iter_attachment_descriptors
    created = 0
    for scope in receipt.scopes:
        if scope.scope != "assignment.submissions":
            continue
        for row in scope.rows:
            if not isinstance(row, dict):
                continue
            try:
                pseudo = pseudonym_for(row.get("user_id"))
            except Exception:
                continue
            if not pseudo:
                continue
            for observation in (row, *(row.get("submission_history") or [])):
                for attempt, key, media_type, size, filename, file_id in iter_attachment_descriptors(observation):
                    identifier = job_id(source_key, receipt.course_id, scope.scope_id,
                                        pseudo, attempt, key)
                    if store.get(identifier) is not None:
                        continue
                    store.ensure(source_key=source_key, course_id=receipt.course_id,
                                 assignment_id=scope.scope_id, pseudonym=pseudo,
                                 attempt=attempt, attachment_key=key,
                                 media_type=media_type, size=size, filename=filename,
                                 file_id=file_id, now=now)
                    created += 1
    return created
