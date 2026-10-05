"""Deterministic assignment-scoped comparison evidence over extracted blocks.

Reuses ``api.powergrader.overlap`` for wording overlap and adds chronological
attempt selection, exact-file equality, and successive-attempt additions/removals
with source locators. Results are evidence the agent interprets with the teacher:
no plagiarism probability, AI-authorship verdict, or penalty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from collections.abc import Mapping, Sequence

from api.mirror.evidence_schema import canonical_bytes
from api.powergrader import overlap

COMPARISON_ALGORITHM_VERSION = "comparison-1"
MAX_COMPARISON_ROWS = 500
MAX_SHARED_TERMS = 30
_COVERAGE_STATES = frozenset({"complete", "incomplete", "unknown"})


@dataclass(frozen=True)
class ComparisonResult:
    algorithm_version: str
    assignment_id: str
    exact_file_matches: tuple[dict, ...] = ()
    wording_overlaps: dict = field(default_factory=dict)
    attempt_changes: tuple[dict, ...] = ()
    excluded_shared_text: bool = False
    evidence_digests: tuple[str, ...] = ()
    coverage: dict = field(default_factory=dict)

    def digest(self) -> str:
        return hashlib.sha256(canonical_bytes({
            "algorithm_version": self.algorithm_version,
            "assignment_id": self.assignment_id,
            "exact_file_matches": list(self.exact_file_matches),
            "wording_overlaps": self.wording_overlaps,
            "attempt_changes": list(self.attempt_changes),
            "evidence_digests": list(self.evidence_digests),
        })).hexdigest()


def _attempt_sort_key(attempt):
    return (attempt.get("attempt") is None, attempt.get("attempt") or 0,
            attempt.get("submitted_at") or "")


def compare_assignment(*, assignment_id: str, students: list[dict],
                       shared_text: str = "") -> ComparisonResult:
    """Compare one assignment's students and their successive attempts.

    ``students`` are ``{pseudonym, attempts: [{attempt, submitted_at, blocks:
    [{block_id, text, locator}], original_digest}]}``. Default scope is students
    within one assignment and successive attempts of that assignment; no
    cross-course or multi-year search.
    """
    students = sorted(students, key=lambda student: str(student.get("pseudonym") or ""))
    exact: list[dict] = []
    digests: set[str] = set()
    by_digest: dict[str, list[dict]] = {}
    for student in students:
        for attempt in student.get("attempts") or []:
            digest = attempt.get("original_digest")
            if not digest:
                continue
            digests.add(digest)
            by_digest.setdefault(digest, []).append({
                "pseudonym": student.get("pseudonym"),
                "attempt": attempt.get("attempt"),
                "block_ids": sorted(str(block.get("block_id") or "")
                                    for block in attempt.get("blocks") or []),
            })
    for digest, holders in sorted(by_digest.items()):
        pseudonyms = {holder["pseudonym"] for holder in holders}
        if len(pseudonyms) > 1:
            exact.append({"original_digest": digest,
                          "holders": sorted(holders, key=lambda h: (h["pseudonym"],
                                                                     h["attempt"] or 0,
                                                                     h["block_ids"]))})

    responses = []
    for student in students:
        for attempt in student.get("attempts") or []:
            blocks = sorted(attempt.get("blocks") or [],
                            key=lambda block: (str(block.get("block_id") or ""),
                                               canonical_bytes(block)))
            text = "\n".join(block.get("text") or "" for block in blocks)
            responses.append({"pseudonym": student.get("pseudonym"),
                              "item_id": str(attempt.get("attempt")),
                              "text": text})
    wording = overlap.find_overlaps(responses, shared_text=shared_text)

    changes: list[dict] = []
    for student in students:
        ordered = sorted(student.get("attempts") or [],
                         key=lambda attempt: (*_attempt_sort_key(attempt),
                                              canonical_bytes(attempt)))
        for previous, current in zip(ordered, ordered[1:]):
            before = {block.get("block_id"): block.get("text") or ""
                      for block in sorted(previous.get("blocks") or [],
                                          key=lambda block: (str(block.get("block_id") or ""),
                                                             canonical_bytes(block)))}
            after = {block.get("block_id"): block.get("text") or ""
                     for block in sorted(current.get("blocks") or [],
                                         key=lambda block: (str(block.get("block_id") or ""),
                                                            canonical_bytes(block)))}
            added = sorted(set(after) - set(before))
            removed = sorted(set(before) - set(after))
            changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
            if added or removed or changed:
                changes.append({
                    "pseudonym": student.get("pseudonym"),
                    "from_attempt": previous.get("attempt"),
                    "to_attempt": current.get("attempt"),
                    "added_block_ids": added, "removed_block_ids": removed,
                    "changed_block_ids": changed,
                })
    return ComparisonResult(
        algorithm_version=COMPARISON_ALGORITHM_VERSION, assignment_id=assignment_id,
        exact_file_matches=tuple(exact), wording_overlaps=wording,
        attempt_changes=tuple(changes), excluded_shared_text=bool(shared_text),
        evidence_digests=tuple(sorted(digests)),
        coverage={"students": len(students),
                  "attempts": sum(len(s.get("attempts") or []) for s in students)},
    )


def _value(row: Mapping, name: str, default=None):
    """Read a field from either a named-view row or its safe payload."""
    if row.get(name) is not None:
        return row[name]
    payload = row.get("payload")
    return payload.get(name, default) if isinstance(payload, Mapping) else default


def _attempt_key(row: Mapping):
    attempt = _value(row, "attempt")
    return (attempt is None, attempt or 0, str(_value(row, "submitted_at") or ""),
            str(_value(row, "fact_ref") or ""))


def _row_ref(row: Mapping) -> str:
    return str(row.get("fact_ref") or row.get("entity_key") or "")


def _in_scope(row: Mapping, *, source_key: str, course_id: str,
              assignment_id: str) -> bool:
    """Reject explicitly out-of-scope indexed rows; omitted keys are prefiltered."""
    for key, expected in (("source_key", source_key), ("course_id", course_id),
                          ("assignment_id", assignment_id)):
        value = _value(row, key)
        if value is not None and str(value) != expected:
            return False
    return True


def _block_text(block_rows: Sequence[Mapping]) -> str:
    return "\n".join(str(_value(block, "text") or "") for block in block_rows)


def project_comparison_evidence(*, source_key: str, course_id: str,
                                assignment_id: str, input_revision: str,
                                coverage_state: str,
                                submission_rows: Sequence[Mapping] = (),
                                attempt_rows: Sequence[Mapping] = (),
                                attachment_rows: Sequence[Mapping] = (),
                                extraction_block_rows: Sequence[Mapping] = (),
                                shared_text: str = "",
                                max_rows: int = MAX_COMPARISON_ROWS) -> list[dict]:
    """Build deterministic bounded named-view rows using safe evidence only.

    Inputs are already validated/indexed rows. Text is consumed in memory for
    overlap and attempt deltas; output contains bounded term samples and opaque
    source fact refs, never attachment digests, filenames, or originals.
    """
    if coverage_state not in _COVERAGE_STATES:
        coverage_state = "unknown"
    if type(max_rows) is not int or max_rows < 0:
        raise ValueError("invalid_comparison_limit")
    cap = min(max_rows, MAX_COMPARISON_ROWS)
    if not cap:
        return []

    submissions = {}
    for priority, source_rows in enumerate((submission_rows, attempt_rows)):
        for row in source_rows:
            if not isinstance(row, Mapping):
                continue
            if not _in_scope(row, source_key=source_key, course_id=course_id,
                             assignment_id=assignment_id):
                continue
            p = str(_value(row, "pseudonym") or "")
            if not p:
                continue
            submissions.setdefault(p, []).append((priority, row))

    # Associate extracted blocks with their safe pseudonym/attempt identity.
    blocks_by_attempt: dict[tuple[str, object], dict[str, Mapping]] = {}

    def add_block(pseudonym, attempt, block):
        block_id = _value(block, "block_id")
        if not pseudonym or block_id is None:
            return
        key = (str(pseudonym), attempt)
        identity = str(block_id)
        bucket = blocks_by_attempt.setdefault(key, {})
        prior = bucket.get(identity)
        # Duplicate projections can arrive through both a container and the
        # flattened block view. Pick one canonically so input order never wins.
        if prior is None or canonical_bytes(dict(block)) < canonical_bytes(dict(prior)):
            bucket[identity] = block

    for row in extraction_block_rows:
        if not isinstance(row, Mapping):
            continue
        if not _in_scope(row, source_key=source_key, course_id=course_id,
                         assignment_id=assignment_id):
            continue
        payload = row.get("payload")
        # A named extraction container can contain many blocks. It is evidence
        # metadata, not a block itself; consume only the nested block objects.
        if isinstance(payload, Mapping) and isinstance(payload.get("blocks"), list):
            continue
        p = str(_value(row, "pseudonym") or "")
        attempt = _value(row, "attempt")
        if p and _value(row, "block_id") is not None and _value(row, "text") is not None:
            add_block(p, attempt, row)

    # Resolve extraction records that carry blocks as a nested safe array.
    for row in extraction_block_rows:
        payload = row.get("payload") if isinstance(row, Mapping) else None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("blocks"), list):
            continue
        if not _in_scope(row, source_key=source_key, course_id=course_id,
                         assignment_id=assignment_id):
            continue
        p, attempt = payload.get("pseudonym"), payload.get("attempt")
        if not p:
            continue
        for block in payload["blocks"]:
            if isinstance(block, Mapping):
                add_block(p, attempt, block)

    students = []
    for p in sorted(submissions):
        attempts = []
        unique = {}
        for priority, row in submissions[p]:
            number = _value(row, "attempt")
            # The current submission and a retained observation often describe
            # the same numbered attempt with different fact refs. Compare that
            # attempt once, preferring its current submission when present.
            key = (number is None, number if number is not None else _row_ref(row))
            prior = unique.get(key)
            candidate = (priority, canonical_bytes(dict(row)), row)
            if prior is None or candidate[:2] < prior[:2]:
                unique[key] = candidate
        for _, (_, _, row) in sorted(unique.items(), key=lambda pair: _attempt_key(pair[1][2])):
            number = _value(row, "attempt")
            blocks = [blocks_by_attempt.get((p, number), {})[key]
                      for key in sorted(blocks_by_attempt.get((p, number), {}))]
            attempts.append({"attempt": number,
                             "submitted_at": _value(row, "submitted_at"),
                             "fact_ref": _row_ref(row),
                             "blocks": [{"block_id": str(_value(b, "block_id") or _value(b, "fact_ref") or i),
                                         "text": _value(b, "text") or ""}
                                        for i, b in enumerate(blocks)]})
        students.append({"pseudonym": p, "attempts": attempts})

    # Digest identity is used only to compare captured files; never serialized.
    files: dict[str, list[dict]] = {}
    for row in attachment_rows:
        if not isinstance(row, Mapping):
            continue
        if not _in_scope(row, source_key=source_key, course_id=course_id,
                         assignment_id=assignment_id):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else row
        digest = payload.get("original_digest")
        p, attempt = payload.get("pseudonym"), payload.get("attempt")
        if (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                and payload.get("status", "captured") == "captured" and p):
            files.setdefault(digest, []).append({"pseudonym": str(p), "attempt": attempt,
                                                 "ref": _row_ref(row)})

    rows = []
    def append(kind: str, left: dict, right: dict | None, *, terms=(), change=None):
        if len(rows) >= cap:
            return
        payload = {
            "input_revision": input_revision,
            "coverage_state": coverage_state,
            "evidence_kind": kind,
            "left_pseudonym": left.get("pseudonym", ""),
            "right_pseudonym": right.get("pseudonym", "") if right else "",
            "left_attempt": left.get("attempt"),
            "right_attempt": right.get("attempt") if right else None,
            "shared_terms": list(terms)[:MAX_SHARED_TERMS],
            "shared_term_count": len(terms),
            "change_summary": change or {},
        }
        ref_kind = "attachment" if kind == "exact_file" else "submission"
        payload[f"left_{ref_kind}_ref"] = left.get("ref", "")
        payload[f"right_{ref_kind}_ref"] = right.get("ref", "") if right else ""
        identity = {"source_key": source_key, "course_id": course_id,
                    "assignment_id": assignment_id, "payload": payload}
        fact_ref = hashlib.sha256(canonical_bytes(identity)).hexdigest()
        rows.append({"source_key": source_key, "course_id": course_id,
                     "assignment_id": assignment_id, "fact_ref": fact_ref,
                     "payload": payload})

    for digest, holders in sorted(files.items()):
        holders = sorted(holders, key=lambda h: (h["pseudonym"], h["attempt"] or 0, h["ref"]))
        for i, left in enumerate(holders):
            for right in holders[i + 1:]:
                if left["pseudonym"] != right["pseudonym"]:
                    append("exact_file", left, right)

    # Compare each cross-student attempt pair within this assignment. The
    # overlap utility groups on item_id; using attempt number there would miss
    # matches whenever students submitted different attempt numbers.
    for left_index, left_student in enumerate(students):
        for right_student in students[left_index + 1:]:
            for left_attempt in left_student["attempts"]:
                for right_attempt in right_student["attempts"]:
                    pair = [
                        {"pseudonym": left_student["pseudonym"], "item_id": assignment_id,
                         "text": _block_text(left_attempt["blocks"])},
                        {"pseudonym": right_student["pseudonym"], "item_id": assignment_id,
                         "text": _block_text(right_attempt["blocks"])},
                    ]
                    match = overlap.find_overlaps(
                        pair, shared_text=shared_text, min_shared_words=8,
                        max_pairs=1, max_samples=1, sample_words=MAX_SHARED_TERMS)
                    left_matches = match.get(left_student["pseudonym"], [])
                    if left_matches:
                        terms = re.findall(r"[^\W_]+", (left_matches[0].get("samples") or [""])[0].lower())
                        append("wording_overlap",
                               {"pseudonym": left_student["pseudonym"],
                                "attempt": left_attempt["attempt"], "ref": left_attempt["fact_ref"]},
                               {"pseudonym": right_student["pseudonym"],
                                "attempt": right_attempt["attempt"], "ref": right_attempt["fact_ref"]},
                               terms=terms)

    for student in students:
        ordered = sorted(student["attempts"], key=lambda a: (a["attempt"] is None,
                                                              a["attempt"] or 0,
                                                              a["submitted_at"] or ""))
        for before, after in zip(ordered, ordered[1:]):
            left_blocks = {b["block_id"]: b["text"] for b in before["blocks"]}
            right_blocks = {b["block_id"]: b["text"] for b in after["blocks"]}
            added = sorted(right_blocks.keys() - left_blocks.keys())
            removed = sorted(left_blocks.keys() - right_blocks.keys())
            changed = sorted(k for k in left_blocks.keys() & right_blocks.keys()
                             if left_blocks[k] != right_blocks[k])
            if added or removed or changed:
                append("attempt_change",
                       {"pseudonym": student["pseudonym"], "attempt": before["attempt"],
                        "ref": before["fact_ref"]},
                       {"pseudonym": student["pseudonym"], "attempt": after["attempt"],
                        "ref": after["fact_ref"]},
                       change={"added_blocks": len(added), "removed_blocks": len(removed),
                               "changed_blocks": len(changed)})
    return sorted(rows, key=lambda row: (row["payload"]["evidence_kind"],
                                         row["payload"]["left_pseudonym"],
                                         row["payload"]["right_pseudonym"],
                                         row["payload"]["left_attempt"] or 0,
                                         row["payload"]["right_attempt"] or 0,
                                         row["fact_ref"]))
