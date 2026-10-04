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


@pytest.mark.parametrize("kind,scope,payload", [
    ("group", "course.groups", {"group_id": "20", "title": "Workshop", "student_pseudonyms": ["Pikachu", "Eevee"]}),
    ("module", "course.modules", {"module_id": "30", "title": "Unit", "position": 1, "items": [{"item_id": "31", "title": "Task", "type": "Assignment", "position": 1, "content_id": "10"}]}),
    ("page", "course.pages", {"page_id": "40", "title": "Directions", "body": "Read carefully", "published": False, "front_page": False, "updated_at": None}),
    ("assignment_group", "course.assignment_groups", {"assignment_group_id": "50", "title": "Writing", "position": 1, "group_weight": 30.5}),
])
def test_structure_fact_and_scope_contract(evidence_factory, kind, scope, payload):
    from api.mirror.evidence_schema import SCOPE_KINDS
    record = evidence_factory["fact"](kind, f"{kind}:10", payload)
    assert validate_fact(record) == record
    assert SCOPE_KINDS[scope] == frozenset({kind})
    assert validate_commit(evidence_factory["commit"](scope=scope, scope_id="course"))["scope"] == scope
    record["payload"]["student_ids"] = ["123456"]
    with pytest.raises(EvidenceValidationError, match="unexpected_fields"):
        validate_fact(record)


@pytest.mark.parametrize("payload", [
    {"module_id": "30", "title": "Unit", "position": 1, "items": [{"item_id": "31", "title": "Task", "type": "Assignment", "position": 1, "url": "https://example.invalid"}]},
    {"module_id": "30", "title": "Unit", "position": 1, "items": [{"item_id": "31", "title": "Task", "type": "Unknown", "position": 1}]},
    {"module_id": "30", "title": "Unit", "position": True, "items": []},
    {"module_id": "30", "title": "Unit", "position": 1, "items": [{"item_id": "31", "title": "Task", "type": "Page", "position": 1, "page_id": "student-private-slug"}]},
    {"module_id": "30", "title": "Unit", "position": 1, "items": [{"item_id": "31", "title": "Task", "type": "Quiz", "position": 1}] * 2},
])
def test_module_nested_structure_refuses_unapproved_metadata(evidence_factory, payload):
    with pytest.raises(EvidenceValidationError):
        validate_fact(evidence_factory["fact"]("module", "module:30", payload))


def test_canvas_date_context_preserves_instants_without_entitlement(evidence_factory):
    dates = {"base": False, "override_id": "12", "title": "Section", "due_at": "2026-01-02T00:00:00Z", "unlock_at": None, "lock_at": None}
    assignment = evidence_factory["fact"]("assignment", "assignment:10", assignment_id="10", title="Task", unlock_at=None, lock_at=None, all_dates=[dates])
    assert validate_fact(assignment) == assignment
    current = evidence_factory["fact"](effective_due_at=dates["due_at"])
    assert validate_fact(current)["payload"]["effective_due_at"] == dates["due_at"]
    course = evidence_factory["fact"]("course", "course:1", title="Course", workflow_state="completed", start_at=None, end_at=dates["due_at"], restrict_enrollments_to_course_dates=True)
    assert validate_fact(course) == course
    dates["student_ids"] = ["123456"]
    with pytest.raises(EvidenceValidationError, match="unexpected_fields"):
        validate_fact(assignment)


@pytest.mark.parametrize("changes", [{"base": 1}, {"due_at": "2026-01-01T00:00:00-06:00"}, {"override_id": []}, {"title": {"name": "private"}}])
def test_all_dates_nested_values_are_strict(evidence_factory, changes):
    record = evidence_factory["fact"]("assignment", "assignment:10", assignment_id="10", title="Task", all_dates=[{"base": False, **changes}])
    with pytest.raises(EvidenceValidationError):
        validate_fact(record)
