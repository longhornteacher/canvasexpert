"""Laws and boundary contracts for strict safe evidence JSON."""
import math

import pytest

from api.mirror.evidence_schema import (
    EvidenceValidationError, canonical_bytes, digest_record,
    validate_commit, validate_fact,
)


def test_canonical_json_has_stable_digest_and_preserves_text():
    first = {"z": "A  draft\nwith — punctuation", "a": {"two": 2, "one": 1}}
    second = {"a": {"one": 1, "two": 2}, "z": first["z"]}
    assert canonical_bytes(first) == canonical_bytes(second)
    assert digest_record(first) == digest_record(second)
    assert "—".encode() in canonical_bytes(first)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, {1: "value"}, (1, 2), b"raw"])
def test_non_json_or_nonfinite_values_are_refused(value):
    with pytest.raises(EvidenceValidationError, match="non_json_value"):
        canonical_bytes({"nested": [value]})


@pytest.mark.parametrize("location", ["top", "payload", "criterion", "rating"])
def test_nested_allowlist_rejects_unknown_fields(evidence_factory, location):
    record = evidence_factory["fact"](kind="assignment", entity_key="assignment:10", assignment_id="10", title="Writing", rubric=[{
        "criterion_id": "ideas", "description": "Ideas", "points": 4,
        "ratings": [{"rating_id": "strong", "description": "Strong", "points": 4}],
    }])
    target = {"top": record, "payload": record["payload"], "criterion": record["payload"]["rubric"][0], "rating": record["payload"]["rubric"][0]["ratings"][0]}[location]
    target["creator_name"] = "Synthetic Name"
    with pytest.raises(EvidenceValidationError, match="unexpected_fields"):
        validate_fact(record)


def test_unknown_attempt_is_not_fabricated(evidence_factory):
    record = evidence_factory["fact"](kind="attempt_observation", attempt=None)
    assert validate_fact(record)["payload"]["attempt"] is None
    for invalid in (0, True, "1"):
        record["payload"]["attempt"] = invalid
        with pytest.raises(EvidenceValidationError):
            validate_fact(record)


@pytest.mark.parametrize("component", ["../escape", "a/b", "C:\\private", "CON", "..", "a?secret"])
def test_record_components_cannot_escape_root(evidence_factory, component):
    record = evidence_factory["fact"]()
    record["course_id"] = component
    with pytest.raises(EvidenceValidationError):
        validate_fact(record)


@pytest.mark.parametrize("changes", [{"mode": "delta"}, {"mode": "import"}, {"gaps": [{"code": "pagination_failed"}]}])
def test_only_successful_complete_snapshot_can_assert_membership(evidence_factory, changes):
    record = evidence_factory["commit"](**changes)
    with pytest.raises(EvidenceValidationError, match="invalid_completeness"):
        validate_commit(record)


def test_commit_watermarks_and_gaps_have_nested_allowlists(evidence_factory):
    record = evidence_factory["commit"](complete=False, gaps=[{"code": "missing_file", "entity_key": "submission:10:Pikachu"}], watermarks={"submitted_since": "2026-01-01T00:00:00Z"})
    assert validate_commit(record) == record
    record["gaps"][0]["traceback"] = "private path"
    with pytest.raises(EvidenceValidationError, match="unexpected_fields"):
        validate_commit(record)
