"""Deterministic assignment-scoped comparison evidence over extracted blocks.

Reuses ``api.powergrader.overlap`` for wording overlap and adds chronological
attempt selection, exact-file equality, and successive-attempt additions/removals
with source locators. Results are evidence the agent interprets with the teacher:
no plagiarism probability, AI-authorship verdict, or penalty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

from api.mirror.evidence_schema import canonical_bytes
from api.powergrader import overlap

COMPARISON_ALGORITHM_VERSION = "comparison-1"


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
                "block_ids": [block.get("block_id") for block in attempt.get("blocks") or []],
            })
    for digest, holders in sorted(by_digest.items()):
        pseudonyms = {holder["pseudonym"] for holder in holders}
        if len(pseudonyms) > 1:
            exact.append({"original_digest": digest,
                          "holders": sorted(holders, key=lambda h: (h["pseudonym"], h["attempt"] or 0))})

    responses = []
    for student in students:
        for attempt in student.get("attempts") or []:
            text = "\n".join(block.get("text") or "" for block in attempt.get("blocks") or [])
            responses.append({"pseudonym": student.get("pseudonym"),
                              "item_id": str(attempt.get("attempt")),
                              "text": text})
    wording = overlap.find_overlaps(responses, shared_text=shared_text)

    changes: list[dict] = []
    for student in students:
        ordered = sorted(student.get("attempts") or [], key=_attempt_sort_key)
        for previous, current in zip(ordered, ordered[1:]):
            before = {block.get("block_id"): block.get("text") or ""
                      for block in previous.get("blocks") or []}
            after = {block.get("block_id"): block.get("text") or ""
                     for block in current.get("blocks") or []}
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
