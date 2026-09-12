"""Tests for api/feedback_results.py's parse_results output-shape tolerance.

Covers eight reply shapes a teacher's AI chat might paste back, three of which
already parsed correctly and five of which used to parse to zero rows while
still reporting success downstream. None of these tests touch a session or the
Canvas; persistent-session safety is covered at the current Scoring Session
write boundary.
"""
import json

import pytest

from api import feedback_results

ADA = {"pseudonym": "Ada", "item_id": "101", "score": 2, "feedback": "Nice structure."}
ALAN = {"pseudonym": "Alan", "item_id": "202", "score": 2, "feedback": "Clear argument."}


def _ok(parsed):
    """Structural validate_results check, no bundle/vault cross-check needed here."""
    return feedback_results.validate_results(parsed)["ok"]


# --------------------------------------------------------------------------
# The three shapes that already worked. They must keep working.
# --------------------------------------------------------------------------

def test_bare_array_parses():
    parsed = feedback_results.parse_results(json.dumps([ADA, ALAN]))
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


def test_fenced_array_with_surrounding_prose_parses():
    text = (
        "Here are the results you asked for:\n\n"
        "```json\n" + json.dumps([ADA, ALAN]) + "\n```\n\n"
        "Let me know if you need anything else!"
    )
    parsed = feedback_results.parse_results(text)
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


def test_results_wrapper_parses():
    parsed = feedback_results.parse_results(json.dumps({"results": [ADA, ALAN]}))
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


# --------------------------------------------------------------------------
# The five shapes that used to silently parse to zero rows while
# validate_results still reported ok: True. Each must now either parse
# correctly or fail loudly; none may resolve to an empty list.
# --------------------------------------------------------------------------

def test_scores_wrapper_key_is_unwrapped():
    parsed = feedback_results.parse_results(json.dumps({"scores": [ADA, ALAN]}))
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


def test_students_wrapper_key_is_unwrapped():
    parsed = feedback_results.parse_results(json.dumps({"students": [ADA, ALAN]}))
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


def test_single_bare_object_becomes_a_one_element_list():
    """The one-student-batch case: _split_batches can hand back a lone object
    instead of an array of one, and that used to disappear entirely."""
    parsed = feedback_results.parse_results(json.dumps(ADA))
    assert parsed == [ADA]
    assert _ok(parsed)


def test_one_fenced_object_per_student_is_combined_into_a_list():
    text = (
        "```json\n" + json.dumps(ADA) + "\n```\n\n"
        "```json\n" + json.dumps(ALAN) + "\n```"
    )
    parsed = feedback_results.parse_results(text)
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


def test_json_preamble_does_not_shadow_the_real_array():
    """A short leading note object used to be the *first* decodable block and
    win outright, hiding the array that actually carried the results."""
    text = json.dumps({"note": "I scored 2 students"}) + "\n\n" + json.dumps([ADA, ALAN])
    parsed = feedback_results.parse_results(text)
    assert parsed == [ADA, ALAN]
    assert _ok(parsed)


# --------------------------------------------------------------------------
# Edge cases around the new tolerance: still loud, still no blind guessing.
# --------------------------------------------------------------------------

def test_empty_reply_raises():
    with pytest.raises(ValueError):
        feedback_results.parse_results("")


def test_unparseable_reply_raises():
    with pytest.raises(ValueError):
        feedback_results.parse_results("Sorry, I can't do that right now.")


def test_ambiguous_wrapper_keys_do_not_guess():
    """Two list-valued keys, neither shaped like results: refuse rather than
    pick one arbitrarily. Downstream fail-closed handling turns this into a
    visible error instead of a silent partial import."""
    text = json.dumps({"scores": [7, 8, 9], "notes": ["a", "b"]})
    assert feedback_results.parse_results(text) == []


def test_single_list_key_with_non_result_elements_is_returned_but_fails_validation():
    """With only one list-valued key there is nothing to choose between, so it
    is returned as-is; validate_results (unchanged) is the backstop that
    rejects elements that are not result objects."""
    parsed = feedback_results.parse_results(json.dumps({"scores": [1, 2, 3]}))
    assert parsed == [1, 2, 3]
    assert not _ok(parsed)


def test_note_object_alone_with_no_results_anywhere_parses_to_empty():
    """No array, no pseudonym-bearing object: nothing to salvage. This is the
    shape the persistent storage privacy guard must reject."""
    parsed = feedback_results.parse_results(json.dumps({"note": "nothing else here"}))
    assert parsed == []
