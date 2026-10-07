"""Publish scrubbed extraction evidence keyed to each attachment association."""
from __future__ import annotations

from dataclasses import dataclass
import re

from api import operational_log
from api.mirror.evidence_publish import PublicationRefused
from api.mirror.evidence_schema import EvidenceValidationError
from api.mirror.extraction.schema import (
    EXTRACTION_SCHEMA_VERSION, PRIVACY_POLICY_REVISION, Block, ExtractionError,
    ExtractionResult, PARTIAL_REASONS, validate_result,
)


def extraction_entity_key(assignment_id: str, pseudonym: str,
                          attempt: int | None, attachment_key: str) -> str:
    """Stable extraction identity for one student/attempt/attachment association."""
    attempt_key = attempt if attempt is not None else "none"
    return f"extraction:{assignment_id}:{pseudonym}:{attempt_key}:{attachment_key}"


def _scrub_result(publisher, result: ExtractionResult) -> ExtractionResult:
    """Scrub every block's text through the publisher's privacy boundary."""
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
    """Publish one scrubbed extraction fact, replacing this association's prior fact."""
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
    key = extraction_entity_key(assignment_id, pseudonym, attempt, attachment_key)
    _, digest = publisher._fact("attachment_extraction", key, payload)
    snapshot = publisher.store.scan()
    scope_key = (publisher.source_key, publisher.course_id, "assignment.extractions",
                 assignment_id)
    state = snapshot.scopes.get(scope_key)
    current_refs = set(state.current_refs if state else ())
    # An already-current identical extraction needs no redundant scope commit.
    if digest in current_refs:
        return state.heads[0] if state and state.heads else digest
    refs = set()
    members = set()
    for ref in current_refs:
        fact = snapshot.facts.get(ref, {})
        old_payload = fact.get("payload", {})
        if fact.get("kind") == "attachment_extraction" and (
                old_payload.get("pseudonym"), old_payload.get("attempt"),
                old_payload.get("attachment_key")) == (pseudonym, attempt, attachment_key):
            continue
        refs.add(ref)
        if fact.get("entity_key"):
            members.add(fact["entity_key"])
    refs.add(digest)
    members.add(key)
    record = {
        "schema_version": 1, "source_key": publisher.source_key,
        "course_id": publisher.course_id, "scope": "assignment.extractions",
        "scope_id": assignment_id, "writer_key": writer_key, "run_id": run_id,
        "parents": list(state.heads if state else ()),
        "acquisition_started_at": _utc_now(), "acquisition_finished_at": _utc_now(),
        # This scope enumerates published extraction facts, not all attachment
        # associations. The full current set is locally available above.
        "mode": "snapshot", "membership_complete": True,
        "record_refs": sorted(refs), "member_keys": sorted(members),
        "gaps": [], "watermarks": {},
    }
    return publisher.store.publish_commit(record)


@dataclass(frozen=True)
class ExtractionOutcome:
    processed: int
    published: int
    gaps: tuple[str, ...]


def _failure_reason(code: str | None, *, recognition: bool = False) -> str:
    if recognition or code in {"recognition_failed", "recognition_error"}:
        return "recognition_gap"
    return code if code in PARTIAL_REASONS else "corruption"


def _unavailable_result(digest: str, version: str, reason: str) -> ExtractionResult:
    return ExtractionResult(
        input_digest=digest, detected_format="unknown", method="none",
        availability="unavailable", extractor_version=version,
        schema_version=EXTRACTION_SCHEMA_VERSION,
        privacy_policy_revision=PRIVACY_POLICY_REVISION,
        partial_reasons=(reason,),
    )


def _version_string(version: str) -> str:
    return f"{version}:{PRIVACY_POLICY_REVISION}"


def extract_captured_attachments(*, publisher_scope, jobs, recover_original,
                                 run_adapter, writer_key: str, run_id: str,
                                 limit: int = 20, stop_event=None) -> ExtractionOutcome:
    """Extract one bounded queue chunk; publish facts before recording completion.

    ``publisher_scope(course_id)`` is a context manager yielding a publisher.
    Original recovery and adapter work run outside the publisher/vault scope.
    """
    from api.mirror.evidence_acquisition import publish_attachment_status
    from api.mirror.extraction import registry

    processed = published = 0
    gaps: list[str] = []
    for job in jobs.extraction_candidates(limit=limit):
        if stop_event is not None and stop_event.is_set():
            break
        processed += 1
        adapter_name = registry.adapter_name(job.filename)
        version = "unsupported" if adapter_name is None else adapter_name
        adapter = None
        if adapter_name is not None:
            try:
                adapter = registry.load_adapter(adapter_name)
                version = registry.extractor_version(job.filename) or adapter_name
            except ExtractionError as exc:
                reason = _failure_reason(exc.code)
                result = _unavailable_result(job.digest, version, reason)
                try:
                    with publisher_scope(job.course_id) as publisher:
                        publish_extraction(
                            publisher=publisher, assignment_id=job.assignment_id,
                            pseudonym=job.pseudonym, attempt=job.attempt,
                            attachment_key=job.attachment_key, original_digest=job.digest,
                            result=result, writer_key=writer_key, run_id=run_id)
                    published += 1
                    jobs.record_extraction(job.job_id, state="gap", error=reason,
                                           extracted_with=_version_string(version))
                except Exception:
                    gaps.append("publication_failed")
                    continue
                gaps.append(reason)
                continue

        if adapter_name is None:
            reason = "unsupported_type"
            result = _unavailable_result(job.digest, "unsupported", reason)
        else:
            try:
                data = recover_original(job.digest)
            except Exception:
                try:
                    with publisher_scope(job.course_id) as publisher:
                        publish_attachment_status(
                            publisher=publisher, job=job, status="unavailable",
                            writer_key=writer_key, run_id=run_id)
                    published += 1
                    jobs.record_extraction(job.job_id, state="gap", error="original_missing",
                                           extracted_with=_version_string(version))
                except Exception:
                    gaps.append("publication_failed")
                    continue
                gaps.append("original_missing")
                continue
            try:
                result = validate_result(run_adapter(adapter_name, data, job.filename))
            except ExtractionError as exc:
                reason = _failure_reason(
                    exc.code, recognition=str(exc.code).startswith("recognition"))
                result = _unavailable_result(job.digest, version, reason)
            except Exception:
                reason = "corruption"
                result = _unavailable_result(job.digest, version, reason)
        reason = result.partial_reasons[0] if result.availability == "unavailable" and result.partial_reasons else None
        try:
            with publisher_scope(job.course_id) as publisher:
                publish_extraction(
                    publisher=publisher, assignment_id=job.assignment_id,
                    pseudonym=job.pseudonym, attempt=job.attempt,
                    attachment_key=job.attachment_key, original_digest=job.digest,
                    result=result, writer_key=writer_key, run_id=run_id)
            published += 1
            state = "done" if result.availability in {"complete", "empty"} else "gap"
            jobs.record_extraction(job.job_id, state=state,
                                   error=reason or ("partial_result" if state == "gap" else None),
                                   extracted_with=_version_string(result.extractor_version or version))
            if state == "gap":
                gaps.extend(result.partial_reasons or ("partial_result",))
        except (EvidenceValidationError, PublicationRefused) as exc:
            # The same result is refused the same way on every retry, so settle
            # it as a text-free gap (a version change or explicit refresh retries it).
            operational_log.emit("mirror.extraction_publication", "refused",
                                 error_class=type(exc), scope=_refusal_code(exc))
            refused_version = result.extractor_version or version
            try:
                with publisher_scope(job.course_id) as publisher:
                    publish_extraction(
                        publisher=publisher, assignment_id=job.assignment_id,
                        pseudonym=job.pseudonym, attempt=job.attempt,
                        attachment_key=job.attachment_key, original_digest=job.digest,
                        result=_unavailable_result(job.digest, refused_version, "recognition_gap"),
                        writer_key=writer_key, run_id=run_id)
                published += 1
                jobs.record_extraction(job.job_id, state="gap", error="publication_refused",
                                       extracted_with=_version_string(refused_version))
            except Exception as gap_exc:
                operational_log.emit("mirror.extraction_publication", "failed",
                                     error_class=type(gap_exc))
                gaps.append("publication_failed")
                continue
            gaps.append("publication_refused")
        except Exception as exc:
            # A fact may have landed before a local write failed; retrying is
            # idempotent and must remain eligible until the job says complete.
            operational_log.emit("mirror.extraction_publication", "failed", error_class=type(exc))
            gaps.append("publication_failed")
    return ExtractionOutcome(processed, published, tuple(sorted(set(gaps))))


def _refusal_code(exc: Exception) -> str | None:
    code = str(exc)
    return code if re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", code) else None
