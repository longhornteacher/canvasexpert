"""Feedback v2 shape and authorship laws."""
import json

import pytest

from api import feedback_results


def _result(*, score=7, feedback="A paragraph.\n\nA second paragraph."):
    return {"pseudonym": "Pikachu", "item_id": "essay", "score": score,
            "feedback": feedback}


ADA = {"pseudonym": "Ada", "item_id": "101", "score": 2, "feedback": "Clear structure."}
ALAN = {"pseudonym": "Alan", "item_id": "202", "score": 2, "feedback": "Clear argument."}


def _ok(parsed):
    return feedback_results.validate_results(parsed)["ok"]


def test_bare_array_parses():
    parsed = feedback_results.parse_results(json.dumps([ADA, ALAN]))
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


def test_fenced_array_with_surrounding_prose_parses():
    text = "Here are results:\n```json\n" + json.dumps([ADA, ALAN]) + "\n```"
    parsed = feedback_results.parse_results(text)
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


@pytest.mark.parametrize("wrapper", ["results", "scores", "students"])
def test_result_wrappers_parse(wrapper):
    parsed = feedback_results.parse_results(json.dumps({wrapper: [ADA, ALAN]}))
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


def test_single_bare_object_becomes_a_one_element_list():
    parsed = feedback_results.parse_results(json.dumps(ADA))
    assert parsed == [ADA]
    assert _ok(parsed)


def test_one_fenced_object_per_student_is_combined_into_a_list():
    text = "```json\n" + json.dumps(ADA) + "\n```\n```json\n" + json.dumps(ALAN) + "\n```"
    assert feedback_results.parse_results(text) == [ADA, ALAN]


def test_json_preamble_does_not_shadow_the_real_array():
    text = json.dumps({"note": "two rows"}) + "\n\n" + json.dumps([ADA, ALAN])
    assert feedback_results.parse_results(text) == [ADA, ALAN]


def test_empty_and_unparseable_replies_raise():
    for text in ("", "Sorry, no JSON here"):
        with pytest.raises(ValueError):
            feedback_results.parse_results(text)


def test_ambiguous_wrapper_keys_do_not_guess():
    text = json.dumps({"scores": [7, 8], "notes": ["a", "b"]})
    assert feedback_results.parse_results(text) == []


def test_single_list_key_with_non_result_elements_fails_validation():
    parsed = feedback_results.parse_results(json.dumps({"scores": [1, 2, 3]}))
    assert parsed == [1, 2, 3]
    assert not _ok(parsed)


def test_parse_results_accepts_wrapped_and_single_result_json():
    assert feedback_results.parse_results(json.dumps({"results": [_result()]})) == [_result()]
    assert feedback_results.parse_results(json.dumps(_result())) == [_result()]


def test_authored_feedback_and_markdown_are_preserved_exactly():
    feedback = "# Evidence & analysis\n\n**Strong claim.**\n\n— Ms. Teacher"
    row = feedback_results.render_results([_result(feedback=feedback)])[0]
    assert row["feedback"] == feedback
    assert row["authored_feedback"] == feedback


def test_feedback_only_prefix_is_generated_from_score_and_authored_feedback():
    feedback = "Draft score: my reflection\n\nScore: this is my heading."
    result = _result(score=7, feedback=feedback)
    item = feedback_results.render_results(
        [result], bundle={"students": [{"pseudonym": "Pikachu", "responses": [
            {"item_id": "essay", "possible": 10}]}]}, grade_mode="feedback_only",
    )[0]
    assert item["feedback"] == f"Draft score: 7/10\n\n{feedback}"
    assert feedback_results.item_feedback_for_mode(
        {"score": 7, "possible": 10, "feedback": feedback}, "post_score",
    ) == feedback


def test_numeric_score_with_empty_feedback_is_valid_score_only_work():
    assert feedback_results.validate_results([_result(feedback="")])["ok"]


def test_null_score_requires_non_empty_feedback():
    result = feedback_results.validate_results([_result(score=None, feedback="  ")])
    assert result["ok"] is False
    assert result["fields"] == ["feedback"]


def test_required_shape_rejects_old_structured_feedback_fields():
    old = {"pseudonym": "Pikachu", "item_id": "essay", "score": 7,
           "explanation": "Clear argument.", "glows": ["Clear thesis."],
           "grows": ["Add a source."], "fixes": ["Add detail."]}
    result = feedback_results.validate_results([old])
    assert result["ok"] is False
    assert "feedback" in result["fields"]


def test_score_must_be_a_number_or_null():
    result = feedback_results.validate_results([_result(score=True)])
    assert result["ok"] is False
    assert "score" in result["fields"]


def test_unknown_pseudonym_and_item_errors_always_name_their_field():
    """CONTRACT: every hard error from the vault and bundle cross-checks adds to
    ``fields``, so an invalid_results response is never left with a blank list."""
    class Vault:
        def reverse(self, pseudonym):
            return {"canvas_id": "1"} if pseudonym == "Ada" else None

    bundle = {"students": [{"pseudonym": "Ada", "responses": [{"item_id": "101", "possible": 4}]}]}
    stranger = dict(ADA, pseudonym="Nobody")
    wrong_item = dict(ADA, item_id="999")

    unknown = feedback_results.validate_results([stranger], bundle, Vault())
    off_bundle_item = feedback_results.validate_results([wrong_item], bundle, Vault())

    assert unknown["ok"] is False and unknown["fields"] == ["pseudonym"]
    assert off_bundle_item["ok"] is False and off_bundle_item["fields"] == ["result"]
    assert "not_in_packet" in off_bundle_item["errors"][0]


def test_held_key_reports_pseudonym_and_specific_reason_without_item_id_field():
    bundle = {"students": [{"pseudonym": "Ada", "responses": [{
        "item_id": "101", "response": "Partial readable text.",
        "_held": True, "_hold_reason": "file_not_read",
    }]}]}
    result = feedback_results.validate_results([ADA], bundle)

    assert result["ok"] is False
    assert result["fields"] == ["result"]
    assert "Ada/101: held: file_not_read" in result["errors"][0]


def test_empty_response_is_held_as_no_text_even_without_a_marker():
    bundle = {"students": [{"pseudonym": "Ada", "responses": [{
        "item_id": "101", "response": "  ",
    }]}]}
    result = feedback_results.validate_results([ADA], bundle)

    assert result["ok"] is False
    assert result["fields"] == ["result"]
    assert "held: no_text" in result["errors"][0]
