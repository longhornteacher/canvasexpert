"""Read safe, indexed attachment evidence for scoring packets."""
from __future__ import annotations

from dataclasses import dataclass, field

from api.mirror.evidence_paths import local_source_root, source_key_for_origin
from api.mirror.evidence_queries import EvidenceQueryService


@dataclass(frozen=True)
class StudentEvidence:
    pseudonym: str
    text: str
    evidence_complete: bool
    held: bool
    gaps: tuple[str, ...] = ()
    attempt: int | None = None
    submitted_at: str | None = None
    evidence_revision: str | None = None
    block_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssignmentEvidence:
    assignment_id: str
    students: tuple[StudentEvidence, ...] = ()
    evidence_revision: str | None = None
    available: bool = False
    gaps: tuple[str, ...] = field(default_factory=tuple)


def _extraction_text(payload: dict) -> str:
    return "\n".join(str(block.get("text") or "") for block in payload.get("blocks") or [])


def _read_all(service, view: str, *, source_key: str, course_id: str,
              assignment_id: str, revision: str | None = None) -> tuple[list[dict], str | None, dict]:
    """Read every bounded page, pinning later pages and retaining coverage."""
    records: list[dict] = []
    offset = 0
    expected_revision = revision
    coverage: dict | None = None
    while True:
        page = service.read(view, source_key=source_key, course_id=course_id,
                            assignment_id=assignment_id, limit=100, offset=offset,
                            revision=expected_revision)
        if expected_revision is None:
            expected_revision = page.get("revision")
            if expected_revision is None:
                raise ValueError("evidence revision unavailable")
        elif page.get("revision") != expected_revision:
            raise ValueError("evidence revision changed while paging")
        page_coverage = {
            "membership": page.get("membership") or {},
            "synchronization": page.get("synchronization") or {},
        }
        if coverage is None:
            coverage = page_coverage
        elif page_coverage != coverage:
            raise ValueError("evidence coverage changed while paging")
        records.extend(page.get("records") or [])
        next_offset = page.get("next_offset")
        if next_offset is None:
            break
        if type(next_offset) is not int or next_offset <= offset:
            raise ValueError("invalid evidence page cursor")
        offset = next_offset
    return records, expected_revision, coverage or {}


def _no_attachments_observed(submission_coverage: dict, attachment_coverage: dict) -> bool:
    """A fully enumerated submission scope with no attachment scope has no files.

    The mirror publishes an attachment scope only for assignments where it saw
    attachments, so a text-only assignment never gets one.
    """
    return (_scope_complete(submission_coverage)
            and (attachment_coverage.get("membership") or {}).get("state") == "unknown")


def _scope_complete(coverage: dict) -> bool:
    """Treat an unknown or pending attachment scope as insufficient for scoring."""
    membership = coverage.get("membership") or {}
    synchronization = coverage.get("synchronization") or {}
    return (membership.get("state") == "complete"
            and synchronization.get("state") == "ready"
            and not synchronization.get("pending_commits")
            and not synchronization.get("ambiguous_entities"))


def read_assignment_evidence(*, course_id: str, assignment_id: str,
                             workspace_root, canvas_base: str) -> AssignmentEvidence:
    """Read complete safe evidence; readable partial text remains visible but held."""
    try:
        source_key = source_key_for_origin(canvas_base)
        index_path = local_source_root(source_key, workspace_root) / "query.sqlite3"
        if not index_path.exists():
            return AssignmentEvidence(assignment_id=str(assignment_id), available=False)
        service = EvidenceQueryService(index_path)
        submissions, revision, submission_coverage = _read_all(
            service, "current_submissions", source_key=source_key,
            course_id=str(course_id), assignment_id=str(assignment_id))
        attachments, revision, attachment_coverage = _read_all(
            service, "attachment_associations", source_key=source_key,
            course_id=str(course_id), assignment_id=str(assignment_id), revision=revision)
        extractions, _, _ = _read_all(
            service, "attachment_extractions", source_key=source_key,
            course_id=str(course_id), assignment_id=str(assignment_id), revision=revision)
    except Exception:
        return AssignmentEvidence(assignment_id=str(assignment_id), available=False)

    current_attempts: dict[str, object] = {}
    for record in submissions:
        payload = record.get("payload") or {}
        pseudo = str(record.get("pseudonym") or payload.get("pseudonym") or "")
        if pseudo:
            attempt = record.get("attempt")
            current_attempts[pseudo] = payload.get("attempt") if attempt is None else attempt
    grouped: dict[tuple[str, object], dict] = {}
    unresolvable: dict[str, list[dict]] = {}
    for record in attachments:
        payload = record.get("payload") or {}
        pseudo = str(payload.get("pseudonym") or "")
        if not pseudo:
            continue
        attempt = payload.get("attempt")
        if pseudo not in current_attempts or current_attempts[pseudo] in (None, ""):
            unresolvable.setdefault(pseudo, []).append(payload)
            continue
        if attempt != current_attempts[pseudo]:
            continue
        entry = grouped.setdefault((pseudo, attempt), {"attachments": [], "extractions": []})
        entry["attachments"].append(payload)
    for record in extractions:
        payload = record.get("payload") or {}
        pseudo = str(payload.get("pseudonym") or "")
        if not pseudo:
            continue
        attempt = payload.get("attempt")
        if pseudo not in current_attempts or attempt != current_attempts[pseudo]:
            continue
        entry = grouped.get((pseudo, attempt))
        if entry is not None and any(
                attachment.get("attachment_key") == payload.get("attachment_key")
                and attachment.get("original_digest") == payload.get("original_digest")
                for attachment in entry["attachments"]):
            entry["extractions"].append(payload)

    for pseudo, records in unresolvable.items():
        grouped[(pseudo, None)] = {"attachments": records, "extractions": [],
                                  "gaps": ["current_attempt_unavailable"]}

    # A missing association/extraction scope can hide a required file. Hold every
    # current response until those scopes are known complete, even if no rows arrived.
    scope_gaps = []
    if not (_scope_complete(attachment_coverage)
            or _no_attachments_observed(submission_coverage, attachment_coverage)):
        scope_gaps.append("attachment_scope_incomplete")
    if scope_gaps:
        for pseudo, attempt in current_attempts.items():
            if attempt in (None, ""):
                grouped.setdefault((pseudo, None), {"attachments": [], "extractions": [],
                                                    "gaps": ["current_attempt_unavailable"]})
            else:
                grouped.setdefault((pseudo, attempt), {"attachments": [], "extractions": [],
                                                       "gaps": []})
            grouped[(pseudo, attempt)].setdefault("gaps", []).extend(scope_gaps)

    students = []
    for (pseudo, attempt), entry in sorted(grouped.items(), key=lambda item: (item[0][0], str(item[0][1]))):
        texts: list[str] = []
        gaps: list[str] = list(entry.get("gaps") or [])
        block_refs: list[str] = []
        complete = bool(entry["attachments"]) and attempt is not None and not gaps
        if entry["attachments"] and attempt is None and not gaps:
            gaps.append("current_attempt_unavailable")
        extractions_by_key = {}
        for extraction in entry["extractions"]:
            key = (extraction.get("attachment_key"), extraction.get("original_digest"))
            extractions_by_key.setdefault(key, []).append(extraction)
        for attachment in entry["attachments"]:
            key = (attachment.get("attachment_key"), attachment.get("original_digest"))
            candidates = extractions_by_key.get(key, [])
            matching = candidates[0] if candidates else None
            if attachment.get("status") != "captured":
                complete = False
                gaps.append("original_pending")
            if matching is None:
                complete = False
                gaps.append("extraction_missing")
                continue
            text = _extraction_text(matching)
            if text.strip():
                texts.append(text)
            if matching.get("availability") != "complete":
                complete = False
                gaps.extend(matching.get("partial_reasons") or ["partial_extraction"])
            block_refs.extend(str(block.get("block_id") or "")
                              for block in matching.get("blocks") or [])
        # Orphan or stale extraction records are never used as scoring evidence.
        held = (bool(entry["attachments"]) and not complete) or bool(scope_gaps)
        students.append(StudentEvidence(
            pseudonym=pseudo, text="\n\n".join(texts), evidence_complete=complete,
            held=held, gaps=tuple(sorted(set(gaps))), attempt=attempt,
            block_refs=tuple(block_refs)))
    return AssignmentEvidence(assignment_id=str(assignment_id), students=tuple(students),
                             evidence_revision=revision, available=True)


def merge_into_bundle(bundle: dict, *, course_id: str, assignment_id: str,
                      workspace_root, canvas_base: str) -> dict:
    """Merge attempt-matched extracted evidence into the SAFE bundle."""
    evidence = read_assignment_evidence(
        course_id=course_id, assignment_id=assignment_id,
        workspace_root=workspace_root, canvas_base=canvas_base)
    if not evidence.available:
        return {"available": False, "students": 0, "held": 0, "complete": 0}
    by_key = {(student.pseudonym, student.attempt): student for student in evidence.students}
    by_pseudonym: dict[str, list[StudentEvidence]] = {}
    for student in evidence.students:
        by_pseudonym.setdefault(student.pseudonym, []).append(student)
    merged = held = complete = 0
    for student in bundle.get("students") or []:
        pseudo = str(student.get("pseudonym") or "")
        for response in student.get("responses") or []:
            record = by_key.get((pseudo, response.get("attempt")))
            if record is None and response.get("attempt") in (None, ""):
                candidates = by_pseudonym.get(pseudo, [])
                if len(candidates) == 1:
                    record = candidates[0]
            if record is None:
                # A SAFE response from a prior attempt must never be scored
                # against the indexed current attempt's attachment evidence.
                if by_pseudonym.get(pseudo):
                    response["_held"] = True
                    response["_evidence_complete"] = False
                    response["_evidence_gaps"] = ["evidence_attempt_mismatch"]
                    held += 1
                continue
            merged += 1
            held += int(record.held)
            complete += int(record.evidence_complete)
            if record.text:
                body = str(response.get("response") or "").strip()
                response["response"] = f"{body}\n\n{record.text}" if body else record.text
            response["_evidence_complete"] = record.evidence_complete
            response["_held"] = record.held
            if record.gaps:
                response["_evidence_gaps"] = list(record.gaps)
        # Keep _mirror_unreadable on mirror-sourced rows: SAFE attachment
        # preparation uses it to discard private download placeholders. The
        # response-level _held marker below carries durable evidence readiness.
    return {"available": True, "students": merged, "held": held, "complete": complete,
            "evidence_revision": evidence.evidence_revision}
