"""Read safe attachment evidence and decide scoring readiness per submission."""
from __future__ import annotations

from dataclasses import dataclass

from api.mirror.evidence_acquisition import iter_attachment_descriptors
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
    reason: str | None = None
    attachments: tuple[dict, ...] = ()
    extractions: tuple[dict, ...] = ()

    @property
    def scorable(self) -> bool:
        return not self.held


@dataclass(frozen=True)
class AssignmentEvidence:
    assignment_id: str
    students: tuple[StudentEvidence, ...] = ()
    evidence_revision: str | None = None
    available: bool = False
    gaps: tuple[str, ...] = ()


def _extraction_text(payload: dict) -> str:
    return "\n".join(str(block.get("text") or "") for block in payload.get("blocks") or [])


def _same_attempt(left, right) -> bool:
    return type(left) is type(right) and left == right


def _read_all(service, view: str, *, source_key: str, course_id: str,
              assignment_id: str, revision: str | None = None) -> tuple[list[dict], str | None]:
    """Read all pages from one pinned evidence-index revision."""
    records: list[dict] = []
    offset = 0
    expected_revision = revision
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
        records.extend(page.get("records") or [])
        next_offset = page.get("next_offset")
        if next_offset is None:
            break
        if type(next_offset) is not int or next_offset <= offset:
            raise ValueError("invalid evidence page cursor")
        offset = next_offset
    return records, expected_revision


def decide_submission_scoring(*, pseudonym: str, body_text: str = "",
                              attempt: int | None = None,
                              submitted_at: str | None = None,
                              attachments: list[dict] | None = None,
                              extractions: list[dict] | None = None,
                              evidence_available: bool = True,
                              media_recording: bool = False,
                              needs_speedgrader: bool = False,
                              evidence_revision: str | None = None) -> StudentEvidence:
    """Make the sole scorable/hold decision for one submission.

    Only complete extraction records for the exact current attempt and exact
    attachment key/digest pair contribute text. Mirror scope metadata is not
    accepted here, so coverage bookkeeping cannot hold a submission.
    """
    attachments = attachments or []
    extractions = extractions or []
    texts = [str(body_text or "").strip()] if str(body_text or "").strip() else []
    refs: list[str] = []
    gaps: list[str] = []
    incomplete_file = False
    by_key: dict[tuple[object, object], list[dict]] = {}
    for extraction in extractions:
        if not _same_attempt(extraction.get("attempt"), attempt):
            continue
        key = (extraction.get("attachment_key"), extraction.get("original_digest"))
        if not all(isinstance(value, str) and value.strip() for value in key):
            continue
        by_key.setdefault(key, []).append(extraction)

    for attachment in attachments:
        # Evidence from an older/newer attempt can never satisfy this file.
        if not _same_attempt(attachment.get("attempt"), attempt):
            incomplete_file = True
            continue
        if not evidence_available or attachment.get("status") != "captured":
            incomplete_file = True
            continue
        key = (attachment.get("attachment_key"), attachment.get("original_digest"))
        if not all(isinstance(value, str) and value.strip() for value in key):
            incomplete_file = True
            continue
        candidates = by_key.get(key, [])
        matching = next((item for item in candidates
                         if (item.get("availability") == "complete"
                             or (item.get("availability") in (None, "")
                                 and item.get("status") == "complete"))), None)
        if matching is None:
            incomplete_file = True
            continue
        text = _extraction_text(matching).strip()
        if text:
            texts.append(text)
        refs.extend(str(block.get("block_id") or "")
                    for block in matching.get("blocks") or [] if block.get("block_id"))

    text = "\n\n".join(texts)
    if needs_speedgrader:
        reason = "needs_speedgrader"
    elif media_recording:
        reason = "media_recording"
    elif incomplete_file:
        reason = "file_not_read"
    elif not text.strip():
        reason = "no_text"
    else:
        reason = None
    if reason:
        gaps.append(reason)
    return StudentEvidence(
        pseudonym=pseudonym, text=text, evidence_complete=not incomplete_file,
        held=reason is not None, gaps=tuple(sorted(set(gaps))), attempt=attempt,
        submitted_at=submitted_at, evidence_revision=evidence_revision,
        block_refs=tuple(refs), reason=reason)


def read_assignment_evidence(*, course_id: str, assignment_id: str,
                             workspace_root, canvas_base: str) -> AssignmentEvidence:
    """Read evidence rows; unavailable index state is reported without scope policy."""
    try:
        source_key = source_key_for_origin(canvas_base)
        index_path = local_source_root(source_key, workspace_root) / "query.sqlite3"
        if not index_path.exists():
            return AssignmentEvidence(assignment_id=str(assignment_id), available=False)
        service = EvidenceQueryService(index_path)
        submissions, revision = _read_all(
            service, "current_submissions", source_key=source_key,
            course_id=str(course_id), assignment_id=str(assignment_id))
        attachments, revision = _read_all(
            service, "attachment_associations", source_key=source_key,
            course_id=str(course_id), assignment_id=str(assignment_id), revision=revision)
        extractions, _ = _read_all(
            service, "attachment_extractions", source_key=source_key,
            course_id=str(course_id), assignment_id=str(assignment_id), revision=revision)
    except Exception:
        return AssignmentEvidence(assignment_id=str(assignment_id), available=False)

    current: dict[str, dict] = {}
    for record in submissions:
        payload = record.get("payload") or {}
        pseudo = str(record.get("pseudonym") or payload.get("pseudonym") or "")
        if pseudo:
            attempt = record.get("attempt")
            current[pseudo] = {"attempt": payload.get("attempt") if attempt is None else attempt,
                               "submitted_at": payload.get("submitted_at"),
                               "body": payload.get("body") or ""}
    attachments_by_student: dict[str, list[dict]] = {}
    for record in attachments:
        payload = dict(record.get("payload") or {})
        pseudo = str(payload.get("pseudonym") or record.get("pseudonym") or "")
        if (pseudo and pseudo in current
                and _same_attempt(payload.get("attempt"), current[pseudo]["attempt"])):
            payload.setdefault("attempt", record.get("attempt"))
            attachments_by_student.setdefault(pseudo, []).append(payload)
    extractions_by_student: dict[str, list[dict]] = {}
    for record in extractions:
        payload = dict(record.get("payload") or {})
        pseudo = str(payload.get("pseudonym") or record.get("pseudonym") or "")
        if pseudo and pseudo in current:
            payload.setdefault("attempt", record.get("attempt"))
            extractions_by_student.setdefault(pseudo, []).append(payload)

    students = []
    for pseudo, submission in sorted(current.items()):
        student_attachments = attachments_by_student.get(pseudo, [])
        student_extractions = extractions_by_student.get(pseudo, [])
        decision = decide_submission_scoring(
            pseudonym=pseudo, body_text=submission["body"],
            attempt=submission["attempt"], submitted_at=submission["submitted_at"],
            attachments=student_attachments,
            extractions=student_extractions, evidence_revision=revision)
        decision = StudentEvidence(**{**decision.__dict__,
                                      "attachments": tuple(student_attachments),
                                      "extractions": tuple(student_extractions)})
        students.append(decision)
    return AssignmentEvidence(assignment_id=str(assignment_id), students=tuple(students),
                             evidence_revision=revision, available=True)


def merge_into_bundle(bundle: dict, *, course_id: str, assignment_id: str,
                      workspace_root, canvas_base: str,
                      submission_attachments: dict[tuple[str, object], list[dict]] | None = None) -> dict:
    """Apply the per-submission decision to matching SAFE response rows."""
    evidence = read_assignment_evidence(
        course_id=course_id, assignment_id=assignment_id,
        workspace_root=workspace_root, canvas_base=canvas_base)
    by_key = {(student.pseudonym, student.attempt): student for student in evidence.students}
    merged = held = complete = 0
    for student in bundle.get("students") or []:
        pseudo = str(student.get("pseudonym") or "")
        for response in student.get("responses") or []:
            response_attempt = response.get("attempt")
            record = next((candidate for (candidate_pseudo, candidate_attempt), candidate
                           in by_key.items() if candidate_pseudo == pseudo
                           and response_attempt not in (None, "")
                           and _same_attempt(candidate_attempt, response_attempt)), None)
            merged += 1
            decision = decide_submission_scoring(
                # If the frozen source row supplied its attachment list, that
                # list is the authority. Resolve each descriptor to its opaque
                # evidence association; a missing association stays unread.
                pseudonym=pseudo, body_text=str(response.get("response") or ""),
                attempt=response_attempt, submitted_at=response.get("submitted_at"),
                attachments=_expected_attachments(
                    pseudo, response_attempt, record.attachments if record else (),
                    submission_attachments),
                extractions=list(record.extractions) if record else [],
                evidence_available=evidence.available,
                evidence_revision=evidence.evidence_revision,
                media_recording=response.get("_media_recording") is True,
                needs_speedgrader=response.get("_needs_speedgrader") is True)
            # Missing attempt identity is handled by the session owner as an
            # unverified row. Never attach a newer index record to it.
            if response_attempt not in (None, "") or decision.reason:
                held += int(not decision.scorable)
                complete += int(decision.evidence_complete)
                response["response"] = decision.text
                response["_evidence_complete"] = decision.evidence_complete
                response["_scorable"] = decision.scorable
                response["_held"] = not decision.scorable
                if decision.reason:
                    response["_hold_reason"] = decision.reason
                else:
                    response.pop("_hold_reason", None)
                if decision.gaps:
                    response["_evidence_gaps"] = list(decision.gaps)
                else:
                    response.pop("_evidence_gaps", None)
                response["_evidence_revision"] = decision.evidence_revision
                response["_evidence_block_refs"] = list(decision.block_refs)
            response.pop("_media_recording", None)
            response.pop("_needs_speedgrader", None)
    return {"available": evidence.available, "students": merged, "held": held,
            "complete": complete, "evidence_revision": evidence.evidence_revision}


def _expected_attachments(pseudonym: str, attempt, indexed: tuple[dict, ...],
                          source_rows: dict[tuple[str, object], list[dict]] | None) -> list[dict]:
    """Return indexed rows for exact frozen descriptors, including missing sentinels."""
    source_rows = source_rows or {}
    matching_keys = [(number, items) for (pseudo, number), items in source_rows.items()
                     if pseudo == pseudonym and _same_attempt(number, attempt)]
    if attempt in (None, "") and not matching_keys:
        # Identity may be incomplete while file presence is known. Carry the
        # descriptors as unread sentinels; none can satisfy extraction matching.
        matching_keys = [(number, items) for (pseudo, number), items in source_rows.items()
                         if pseudo == pseudonym]
    descriptors = matching_keys[0][1] if len(matching_keys) == 1 else None
    if descriptors is None:
        if matching_keys:
            return [_unread_sentinel(attempt, f"unknown-{index}")
                    for index, _items in enumerate(matching_keys)]
        return list(indexed)
    if attempt in (None, ""):
        return [_unread_sentinel(attempt, f"unknown-{index}")
                for index, _item in enumerate(descriptors)]
    indexed_by_key = {row.get("attachment_key"): row for row in indexed}
    observation = {"attempt": attempt, "attachments": descriptors}
    expected = []
    descriptors_found = list(iter_attachment_descriptors(observation))
    for found_attempt, key, _media_type, _size, _filename, _file_id in descriptors_found:
        association = indexed_by_key.get(key)
        if association is None:
            association = {"attempt": found_attempt, "attachment_key": key,
                           "original_digest": None, "status": "pending"}
        expected.append(association)
    if len(descriptors_found) < len(descriptors):
        expected.extend(_unread_sentinel(attempt, f"malformed-{index}")
                        for index in range(len(descriptors) - len(descriptors_found)))
    return expected


def _unread_sentinel(attempt, label: str) -> dict:
    return {"attempt": attempt, "attachment_key": f"unresolved:{label}",
            "original_digest": None, "status": "pending"}
