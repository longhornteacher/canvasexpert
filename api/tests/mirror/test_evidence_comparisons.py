"""Assignment-scoped comparison evidence laws."""
from __future__ import annotations

from api.mirror.evidence_comparisons import compare_assignment


def _student(pseudonym, attempts):
    return {"pseudonym": pseudonym, "attempts": attempts}


def _attempt(number, text, *, digest=None, submitted="2026-01-01T00:00:00Z"):
    return {"attempt": number, "submitted_at": submitted, "original_digest": digest,
            "blocks": [{"block_id": f"p:{number}", "text": text, "locator": {"paragraph": number}}]}


def test_exact_file_equality_across_students():
    result = compare_assignment(assignment_id="10", students=[
        _student("Pikachu", [_attempt(1, "same", digest="a" * 64)]),
        _student("Eevee", [_attempt(1, "same", digest="a" * 64)]),
    ])
    assert len(result.exact_file_matches) == 1
    assert result.exact_file_matches[0]["original_digest"] == "a" * 64
    assert {holder["pseudonym"] for holder in result.exact_file_matches[0]["holders"]} == {"Pikachu", "Eevee"}


def test_shared_prompt_wording_is_excluded():
    prompt = "Read the passage and explain the central idea in your own words clearly"
    result = compare_assignment(assignment_id="10", students=[
        _student("Pikachu", [_attempt(1, prompt)]),
        _student("Eevee", [_attempt(1, prompt)]),
    ], shared_text=prompt)
    assert result.excluded_shared_text is True
    assert result.wording_overlaps == {}


def test_successive_attempt_additions_and_removals():
    result = compare_assignment(assignment_id="10", students=[
        _student("Pikachu", [
            {"attempt": 1, "submitted_at": "2026-01-01T00:00:00Z",
             "blocks": [{"block_id": "p:1", "text": "first"}, {"block_id": "p:2", "text": "second"}]},
            {"attempt": 2, "submitted_at": "2026-01-02T00:00:00Z",
             "blocks": [{"block_id": "p:1", "text": "first revised"}, {"block_id": "p:3", "text": "third"}]},
        ]),
    ])
    assert len(result.attempt_changes) == 1
    change = result.attempt_changes[0]
    assert change["from_attempt"] == 1 and change["to_attempt"] == 2
    assert change["added_block_ids"] == ["p:3"]
    assert change["removed_block_ids"] == ["p:2"]
    assert change["changed_block_ids"] == ["p:1"]


def test_no_cross_student_wording_verdict_only_evidence():
    result = compare_assignment(assignment_id="10", students=[
        _student("Pikachu", [_attempt(1, "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega")]),
        _student("Eevee", [_attempt(1, "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega")]),
    ])
    # Overlap is reported as evidence, never a probability or verdict.
    assert "probability" not in str(result.wording_overlaps).lower()
    assert result.algorithm_version == "comparison-1"


def test_digest_is_deterministic():
    students = [_student("Pikachu", [_attempt(1, "text", digest="a" * 64)])]
    first = compare_assignment(assignment_id="10", students=students)
    second = compare_assignment(assignment_id="10", students=students)
    assert first.digest() == second.digest()
