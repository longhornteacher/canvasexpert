"""Turn captured originals into published, scrubbed extraction evidence.

Extraction is content-addressed: the same original digest with the same
extractor/schema/privacy versions is extracted once and reused. A stale
extractor version reprocesses only the affected files. Block text is scrubbed
through the publisher's privacy boundary before any safe byte is written.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
from pathlib import Path

from api.mirror.evidence_schema import canonical_bytes
from api.mirror.extraction.schema import (
    EXTRACTION_SCHEMA_VERSION, PRIVACY_POLICY_REVISION, ExtractionError,
    ExtractionResult, validate_result,
)


def extraction_entity_key(assignment_id: str, original_digest: str,
                          extractor_version: str, privacy_revision: int) -> str:
    return (f"extraction:{assignment_id}:{original_digest}:"
            f"{extractor_version}:{privacy_revision}")


def extraction_cache_key(original_digest: str, extractor_version: str,
                         privacy_revision: int) -> str:
    return hashlib.sha256(canonical_bytes({
        "original_digest": original_digest, "extractor_version": extractor_version,
        "privacy_revision": privacy_revision,
        "schema_version": EXTRACTION_SCHEMA_VERSION})).hexdigest()


class ExtractionCache:
    """Machine-local private cache so unchanged originals are not re-extracted."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=2000")
        db.execute("CREATE TABLE IF NOT EXISTS extraction_cache("
                   "cache_key TEXT PRIMARY KEY, original_digest TEXT NOT NULL, "
                   "extractor_version TEXT NOT NULL, privacy_revision INTEGER NOT NULL, "
                   "availability TEXT NOT NULL, block_count INTEGER NOT NULL)")
        db.commit()
        return db

    def has(self, key: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM extraction_cache WHERE cache_key=?",
                              (key,)).fetchone() is not None

    def record(self, key: str, *, original_digest: str, extractor_version: str,
               privacy_revision: int, availability: str, block_count: int) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO extraction_cache VALUES (?,?,?,?,?,?)",
                       (key, original_digest, extractor_version, privacy_revision,
                        availability, block_count))
            db.commit()


def _scrub_result(publisher, result: ExtractionResult) -> ExtractionResult:
    """Scrub every block's text through the publisher's privacy boundary."""
    from api.mirror.extraction.schema import Block
    blocks = tuple(
        Block(block_id=block.block_id, kind=block.kind,
              text=publisher._scrub_text(block.text),
              locator=block.locator, confidence=block.confidence,
              method=block.method, formatting=block.formatting)
        for block in result.blocks
    )
    return ExtractionResult(
        input_digest=result.input_digest, detected_format=result.detected_format,
        method=result.method, availability=result.availability, blocks=blocks,
        extractor_version=result.extractor_version,
        schema_version=result.schema_version,
        privacy_policy_revision=result.privacy_policy_revision,
        partial_reasons=result.partial_reasons, warnings=result.warnings,
        processed_units=result.processed_units, total_units=result.total_units,
        format_metadata=result.format_metadata, source_revision=result.source_revision,
    )


def publish_extraction(*, publisher, assignment_id: str, pseudonym: str,
                       attempt: int | None, attachment_key: str,
                       original_digest: str, result: ExtractionResult,
                       writer_key: str, run_id: str) -> str:
    """Publish one scrubbed extraction fact and its scope commit."""
    from api.mirror.evidence_publish import _utc_now
    scrubbed = _scrub_result(publisher, validate_result(result))
    payload = {
        "assignment_id": assignment_id, "pseudonym": pseudonym, "attempt": attempt,
        "attachment_key": attachment_key, "original_digest": original_digest,
        "extractor_version": scrubbed.extractor_version,
        "extraction_schema_version": scrubbed.schema_version,
        "privacy_policy_revision": scrubbed.privacy_policy_revision,
        "availability": scrubbed.availability, "method": scrubbed.method,
        "blocks": [
            {"block_id": block.block_id, "kind": block.kind, "text": block.text,
             "locator": block.locator, "confidence": block.confidence,
             "method": block.method, "formatting": block.formatting}
            for block in scrubbed.blocks
        ],
        "partial_reasons": list(scrubbed.partial_reasons),
        "processed_units": scrubbed.processed_units, "total_units": scrubbed.total_units,
    }
    key = extraction_entity_key(assignment_id, original_digest,
                                scrubbed.extractor_version,
                                scrubbed.privacy_policy_revision)
    _, digest = publisher._fact("attachment_extraction", key, payload)
    snapshot = publisher.store.scan()
    scope_key = (publisher.source_key, publisher.course_id, "assignment.extractions",
                 assignment_id)
    state = snapshot.scopes.get(scope_key)
    refs = sorted({ref for ref in (state.current_refs if state else ())
                   if snapshot.facts.get(ref, {}).get("entity_key") != key} | {digest})
    members = sorted(set(state.member_keys if state else ()) | {key})
    record = {
        "schema_version": 1, "source_key": publisher.source_key,
        "course_id": publisher.course_id, "scope": "assignment.extractions",
        "scope_id": assignment_id, "writer_key": writer_key, "run_id": run_id,
        "parents": list(state.heads if state else ()),
        "acquisition_started_at": _utc_now(), "acquisition_finished_at": _utc_now(),
        "mode": "snapshot", "membership_complete": bool(state and state.membership_complete),
        "record_refs": refs, "member_keys": members, "gaps": [], "watermarks": {},
    }
    return publisher.store.publish_commit(record)


@dataclass(frozen=True)
class ExtractionOutcome:
    processed: int
    published: int
    cached: int
    failed: int
    gaps: tuple[str, ...]


def extract_captured_attachments(*, publisher_for, jobs, cache: ExtractionCache,
                                 recover_original, run_adapter, writer_key: str,
                                 run_id: str, limit: int = 20) -> ExtractionOutcome:
    """Extract captured originals lacking a current extraction; one failure is isolated.

    ``jobs`` is the private job store (it holds the original filename, which the
    safe association deliberately omits). ``publisher_for(course_id)`` returns an
    :class:`EvidencePublisher` for that course. ``recover_original(digest)``
    returns exact bytes from the private archive. ``run_adapter(adapter_name,
    data, filename)`` runs the supervised adapter. A missing dependency or
    timeout marks that file's gap and continues siblings.
    """
    from api.mirror.extraction import registry
    processed = published = cached = failed = 0
    gaps: list[str] = []
    for job in jobs.captured_jobs(limit=limit):
        processed += 1
        digest = job.digest
        adapter_name = registry.adapter_name(job.filename)
        if adapter_name is None:
            gaps.append("unsupported_type")
            failed += 1
            continue
        try:
            adapter = registry.load_adapter(adapter_name)
        except ExtractionError as exc:
            gaps.append(exc.code)
            failed += 1
            continue
        version = getattr(adapter, "EXTRACTOR_VERSION", adapter_name)
        cache_key = extraction_cache_key(digest, version, PRIVACY_POLICY_REVISION)
        if cache.has(cache_key):
            cached += 1
            continue
        try:
            data = recover_original(digest)
            result = validate_result(run_adapter(adapter_name, data, job.filename))
        except ExtractionError as exc:
            gaps.append(exc.code)
            failed += 1
            continue
        except Exception:
            gaps.append("recognition_failed")
            failed += 1
            continue
        publish_extraction(
            publisher=publisher_for(job.course_id), assignment_id=job.assignment_id,
            pseudonym=job.pseudonym, attempt=job.attempt,
            attachment_key=job.attachment_key, original_digest=digest,
            result=result, writer_key=writer_key, run_id=run_id)
        cache.record(cache_key, original_digest=digest, extractor_version=version,
                     privacy_revision=PRIVACY_POLICY_REVISION,
                     availability=result.availability, block_count=len(result.blocks))
        published += 1
    return ExtractionOutcome(processed, published, cached, failed, tuple(sorted(set(gaps))))
