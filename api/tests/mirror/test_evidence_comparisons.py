"""Assignment-scoped comparison evidence laws."""
from __future__ import annotations

from api.mirror.evidence_comparisons import compare_assignment, project_comparison_evidence


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


def test_projection_emits_bounded_deterministic_exact_overlap_and_attempt_rows():
    source = "a" * 64
    left = {"source_key": source, "course_id": "c1", "assignment_id": "a1",
            "pseudonym": "P-111111", "attempt": 1, "fact_ref": "b" * 64,
            "payload": {"assignment_id": "a1", "pseudonym": "P-111111", "attempt": 1}}
    right = {"source_key": source, "course_id": "c1", "assignment_id": "a1",
             "pseudonym": "P-222222", "attempt": 1, "fact_ref": "c" * 64,
             "payload": {"assignment_id": "a1", "pseudonym": "P-222222", "attempt": 1}}
    shared = "Write about the theme and explain the author's purpose in the passage."
    phrase = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    blocks = [
        {"pseudonym": "P-111111", "attempt": 1, "block_id": "p:1", "text": phrase},
        {"pseudonym": "P-222222", "attempt": 1, "block_id": "p:1", "text": phrase},
        {"pseudonym": "P-111111", "attempt": 2, "block_id": "p:1", "text": phrase + " revised"},
    ]
    attachments = [
        {"fact_ref": "d" * 64, "payload": {"pseudonym": "P-111111", "attempt": 1,
                                               "original_digest": "e" * 64, "status": "captured"}},
        {"fact_ref": "f" * 64, "payload": {"pseudonym": "P-222222", "attempt": 1,
                                               "original_digest": "e" * 64, "status": "captured"}},
    ]
    kwargs = dict(source_key=source, course_id="c1", assignment_id="a1",
                  input_revision="revision-1", coverage_state="complete",
                  submission_rows=[left, right], attempt_rows=[
                      {"pseudonym": "P-111111", "attempt": 2, "submitted_at": "2026-01-02T00:00:00Z",
                       "fact_ref": "1" * 64, "payload": {"pseudonym": "P-111111", "attempt": 2,
                                                           "assignment_id": "a1"}},
                  ], attachment_rows=attachments, extraction_block_rows=blocks, shared_text=shared)
    first = project_comparison_evidence(**kwargs)
    second = project_comparison_evidence(**kwargs)
    assert first == second
    kinds = {row["payload"]["evidence_kind"] for row in first}
    assert kinds == {"exact_file", "wording_overlap", "attempt_change"}
    assert all(row["payload"]["input_revision"] == "revision-1" for row in first)
    assert all(row["payload"]["coverage_state"] == "complete" for row in first)
    assert all("e" * 64 not in str(row) for row in first)
    assert all("Write about the theme" not in str(row) for row in first)
    exact = next(row["payload"] for row in first if row["payload"]["evidence_kind"] == "exact_file")
    assert {exact["left_attachment_ref"], exact["right_attachment_ref"]} == {"d" * 64, "f" * 64}
    assert "left_submission_ref" not in exact
    assert len(project_comparison_evidence(**kwargs, max_rows=1)) == 1


def test_projection_excludes_shared_prompt_and_invalid_digest_and_bounds_coverage():
    source = "a" * 64
    prompt = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    rows = project_comparison_evidence(
        source_key=source, course_id="c1", assignment_id="a1", input_revision="rev",
        coverage_state="invented", submission_rows=[
            {"fact_ref": "b" * 64, "payload": {"assignment_id": "a1", "pseudonym": "P-111111", "attempt": 1}},
            {"fact_ref": "c" * 64, "payload": {"assignment_id": "a1", "pseudonym": "P-222222", "attempt": 1}},
        ], extraction_block_rows=[
            {"pseudonym": "P-111111", "attempt": 1, "text": prompt},
            {"pseudonym": "P-222222", "attempt": 1, "text": prompt},
        ], attachment_rows=[
            {"fact_ref": "d" * 64, "payload": {"pseudonym": "P-111111", "attempt": 1,
                                                   "original_digest": "not-a-digest", "status": "captured"}},
            {"fact_ref": "f" * 64, "payload": {"pseudonym": "P-222222", "attempt": 1,
                                                   "original_digest": "not-a-digest", "status": "captured"}},
        ], shared_text=prompt)
    assert rows == []


def test_projection_compares_different_attempt_numbers_and_reads_nested_blocks_once():
    phrase = "one two three four five six seven eight nine ten eleven twelve"
    rows = project_comparison_evidence(
        source_key="a" * 64, course_id="c1", assignment_id="a1", input_revision="rev",
        coverage_state="complete", submission_rows=[
            {"fact_ref": "b" * 64, "payload": {"assignment_id": "a1", "pseudonym": "P-111111", "attempt": 1}},
            {"fact_ref": "c" * 64, "payload": {"assignment_id": "a1", "pseudonym": "P-222222", "attempt": 3}},
        ], extraction_block_rows=[
            {"fact_ref": "d" * 64, "payload": {"pseudonym": "P-111111", "attempt": 1,
                                                  "blocks": [{"block_id": "p:1", "text": phrase}]}},
            {"pseudonym": "P-222222", "attempt": 3, "block_id": "p:1", "text": phrase},
        ])
    overlaps = [row for row in rows if row["payload"]["evidence_kind"] == "wording_overlap"]
    assert len(overlaps) == 1
    assert overlaps[0]["payload"]["left_attempt"] == 1
    assert overlaps[0]["payload"]["right_attempt"] == 3


def test_projection_is_stable_under_input_order_and_revision_is_part_of_identity():
    phrase = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    submissions = [
        {"source_key": "a" * 64, "course_id": "c1", "assignment_id": "a1",
         "fact_ref": "b" * 64, "payload": {"assignment_id": "a1", "pseudonym": "P-111111", "attempt": 1}},
        {"source_key": "a" * 64, "course_id": "c1", "assignment_id": "a1",
         "fact_ref": "c" * 64, "payload": {"assignment_id": "a1", "pseudonym": "P-222222", "attempt": 1}},
    ]
    blocks = [
        {"source_key": "a" * 64, "course_id": "c1", "assignment_id": "a1",
         "pseudonym": "P-111111", "attempt": 1, "block_id": "p:2", "text": "second"},
        {"source_key": "a" * 64, "course_id": "c1", "assignment_id": "a1",
         "pseudonym": "P-111111", "attempt": 1, "block_id": "p:1", "text": phrase},
        {"source_key": "a" * 64, "course_id": "c1", "assignment_id": "a1",
         "pseudonym": "P-222222", "attempt": 1, "block_id": "p:1", "text": phrase},
        # Explicitly out of course scope; equal wording must not leak into this view.
        {"source_key": "a" * 64, "course_id": "c2", "assignment_id": "a1",
         "pseudonym": "P-333333", "attempt": 1, "block_id": "p:1", "text": phrase},
    ]
    base = dict(source_key="a" * 64, course_id="c1", assignment_id="a1",
                input_revision="rev-1", coverage_state="incomplete",
                submission_rows=submissions, extraction_block_rows=blocks)
    first = project_comparison_evidence(**base)
    reordered = project_comparison_evidence(
        **{**base, "submission_rows": list(reversed(submissions)),
           "extraction_block_rows": list(reversed(blocks))})
    assert first == reordered
    assert {row["payload"]["coverage_state"] for row in first} == {"incomplete"}
    revised = project_comparison_evidence(**{**base, "input_revision": "rev-2"})
    assert [row["fact_ref"] for row in first] != [row["fact_ref"] for row in revised]
    assert all(row["payload"]["input_revision"] == "rev-2" for row in revised)


def test_projection_empty_evidence_is_empty_and_keeps_no_private_material():
    assert project_comparison_evidence(
        source_key="a" * 64, course_id="c1", assignment_id="a1",
        input_revision="rev", coverage_state="incomplete",
        submission_rows=[], attempt_rows=[], attachment_rows=[],
        extraction_block_rows=[], shared_text="private prompt") == []


def test_current_and_history_fact_for_same_attempt_compare_once():
    phrase = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    current = [
        {"fact_ref": "b" * 64, "payload": {"pseudonym": "P-111111", "attempt": 1}},
        {"fact_ref": "c" * 64, "payload": {"pseudonym": "P-222222", "attempt": 1}},
    ]
    history = [
        {"fact_ref": "d" * 64, "payload": {"pseudonym": "P-111111", "attempt": 1}},
        {"fact_ref": "e" * 64, "payload": {"pseudonym": "P-222222", "attempt": 1}},
    ]
    blocks = [
        {"pseudonym": "P-111111", "attempt": 1, "block_id": "p:1", "text": phrase},
        {"pseudonym": "P-222222", "attempt": 1, "block_id": "p:1", "text": phrase},
    ]
    rows = project_comparison_evidence(
        source_key="a" * 64, course_id="c1", assignment_id="a1", input_revision="rev",
        coverage_state="complete", submission_rows=current, attempt_rows=history,
        extraction_block_rows=blocks)
    overlaps = [row["payload"] for row in rows if row["payload"]["evidence_kind"] == "wording_overlap"]
    assert len(overlaps) == 1
    assert overlaps[0]["left_submission_ref"] == "b" * 64
    assert overlaps[0]["right_submission_ref"] == "c" * 64
    assert not any(row["payload"]["evidence_kind"] == "attempt_change" for row in rows)
