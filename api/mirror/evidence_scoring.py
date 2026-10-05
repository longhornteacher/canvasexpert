"""Read ready safe evidence for one assignment into scoring-ready rows.

This is the bridge from the durable CanvasMirror store to the existing SAFE
packet construction. It reads only published, scrubbed facts (never the vault,
originals, or Canvas) and reports, per pseudonym, the extracted text plus an
explicit evidence-completeness and hold marker. Read availability and scorable
completeness are distinct: a student with readable partial evidence is exposed
with a gap, and only a genuinely incomplete required file holds the item.
"""
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


def read_assignment_evidence(*, course_id: str, assignment_id: str,
                             workspace_root, canvas_base: str) -> AssignmentEvidence:
    """Read published attachment/extraction facts for one assignment.

    Returns an empty, unavailable result when the store or index is absent, so
    the caller can fall back to the existing held behavior rather than fail.
    """
    try:
        source_key = source_key_for_origin(canvas_base)
        index_path = local_source_root(source_key, workspace_root) / "query.sqlite3"
        if not index_path.exists():
            return AssignmentEvidence(assignment_id=str(assignment_id), available=False)
        service = EvidenceQueryService(index_path)
        attachments = service.read("attachment_associations", source_key=source_key,
                                   course_id=str(course_id), assignment_id=str(assignment_id),
                                   limit=100)
        extractions = service.read("attachment_extractions", source_key=source_key,
                                   course_id=str(course_id), assignment_id=str(assignment_id),
                                   limit=100)
    except Exception:
        return AssignmentEvidence(assignment_id=str(assignment_id), available=False)

    by_pseudonym: dict[str, dict] = {}
    for record in attachments["records"]:
        payload = record.get("payload") or {}
        pseudo = str(payload.get("pseudonym") or "")
        if not pseudo:
            continue
        entry = by_pseudonym.setdefault(pseudo, {"attachments": [], "extractions": [],
                                                 "attempt": payload.get("attempt")})
        entry["attachments"].append(payload)
    for record in extractions["records"]:
        payload = record.get("payload") or {}
        pseudo = str(payload.get("pseudonym") or "")
        if not pseudo:
            continue
        entry = by_pseudonym.setdefault(pseudo, {"attachments": [], "extractions": [],
                                                 "attempt": payload.get("attempt")})
        entry["extractions"].append(payload)

    students = []
    for pseudo, entry in sorted(by_pseudonym.items()):
        texts, gaps, block_refs = [], [], []
        complete = True
        for attachment in entry["attachments"]:
            if attachment.get("status") != "captured":
                complete = False
                gaps.append("original_pending")
        for extraction in entry["extractions"]:
            text = _extraction_text(extraction)
            if text.strip():
                texts.append(text)
            if extraction.get("availability") != "complete":
                complete = False
                gaps.extend(extraction.get("partial_reasons") or ["partial_extraction"])
            block_refs.extend(str(block.get("block_id") or "")
                              for block in extraction.get("blocks") or [])
        held = bool(entry["attachments"]) and not texts and not complete
        students.append(StudentEvidence(
            pseudonym=pseudo, text="\n\n".join(texts), evidence_complete=complete,
            held=held, gaps=tuple(sorted(set(gaps))), attempt=entry.get("attempt"),
            block_refs=tuple(block_refs),
        ))
    return AssignmentEvidence(
        assignment_id=str(assignment_id), students=tuple(students),
        evidence_revision=attachments.get("revision"), available=True,
    )


def merge_into_bundle(bundle: dict, *, course_id: str, assignment_id: str,
                      workspace_root, canvas_base: str) -> dict:
    """Merge durable extracted evidence into a pseudonymized SAFE bundle.

    For each student with published evidence, the extracted text replaces an
    empty response and the response carries an explicit ``_evidence_complete``
    and ``_held`` marker independent of nonempty text. Students without durable
    evidence keep their existing ``_mirror_unreadable`` behavior. Returns the
    same bundle object, mutated in place, plus a summary of what changed.
    """
    evidence = read_assignment_evidence(
        course_id=course_id, assignment_id=assignment_id,
        workspace_root=workspace_root, canvas_base=canvas_base)
    if not evidence.available:
        return {"available": False, "students": 0, "held": 0, "complete": 0}
    by_pseudonym = {student.pseudonym: student for student in evidence.students}
    merged = held = complete = 0
    for student in bundle.get("students") or []:
        record = by_pseudonym.get(str(student.get("pseudonym") or ""))
        if record is None:
            continue
        merged += 1
        if record.held:
            held += 1
        if record.evidence_complete:
            complete += 1
        for response in student.get("responses") or []:
            if record.text and not str(response.get("response") or "").strip():
                response["response"] = record.text
            response["_evidence_complete"] = record.evidence_complete
            response["_held"] = record.held
            if record.gaps:
                response["_evidence_gaps"] = list(record.gaps)
        if record.text:
            student.pop("_mirror_unreadable", None)
    return {"available": True, "students": merged, "held": held, "complete": complete,
            "evidence_revision": evidence.evidence_revision}
