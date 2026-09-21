"""Response and membership arithmetic laws for SAFE packet projections."""
import pytest

from api import feedback_contract
from api.powergrader.scoring_packet import build_packet, validate_safe_bundle


def test_default_packet_persona_is_teacher_assistant_not_your_assistant():
    contract = feedback_contract.build_contract_text()
    assert contract.startswith("You are a teaching assistant helping a real teacher")
    assert "your teaching assistant" not in contract


def test_safe_bundle_validation_rejects_missing_or_identity_bearing_packets():
    assert validate_safe_bundle({})["code"] == "packet_invalid"
    result = validate_safe_bundle({"students": [{"pseudonym": "A", "user_id": "u1", "responses": []}]})
    assert result == {"ok": False, "code": "packet_invalid", "reason": "identity_field_present"}


def test_safe_bundle_validation_accepts_complete_identity_free_bundle():
    result = validate_safe_bundle({"students": [{"pseudonym": "A", "responses": [
        {"item_id": "q1", "response": "answer"},
    ]}]})
    assert result["ok"] is True


@pytest.mark.parametrize("response_texts, empty_students, expected_rows, expected_held", [
    ([""], 0, 0, 16),
    (["A scorable response.", ""], 1, 15, 15),
])
def test_packet_membership_counts_are_distinct_and_independent_of_response_paging(
    response_texts, empty_students, expected_rows, expected_held,
):
    session = {"session_id": "synthetic", "students": [{"user_id": i} for i in range(19)]}
    session["students"].append({"user_id": 0})
    students = [{"pseudonym": f"Learner {i}", "responses": [
        {"item_id": str(item), "response": text} for item, text in enumerate(response_texts)
    ] if i >= empty_students else []} for i in range(16)]
    # A duplicate membership with no responses must not inflate people or empty-person counts.
    students.append({"pseudonym": "Learner 15", "responses": []})
    bundle = {"students": students}
    offset = 0
    seen = []
    while offset is not None:
        result = build_packet(session, bundle, offset=offset, limit=3, include_context=False)
        assert result["session_student_count"] == 19
        assert result["bundle_student_count"] == 16
        assert result["excluded_student_count"] == 3
        assert result["students_without_responses"] == empty_students
        assert result["total"] == expected_rows
        assert result["held"] == expected_held
        assert "user_id" not in str(result)
        seen.extend(result["students"])
        offset = result.get("next_offset")
    assert len(seen) == expected_rows
