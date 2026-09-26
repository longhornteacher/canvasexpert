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

ADA = {"pseudonym": "Ada", "item_id": "101", "score": 2,
       "explanation": "Nice structure.", "glows": ["Clear structure."], "grows": ["Add detail."]}
ALAN = {"pseudonym": "Alan", "item_id": "202", "score": 2,
        "explanation": "Clear argument.", "glows": ["Clear argument."], "grows": ["Add a source."]}


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


# --------------------------------------------------------------------------
# LAW: the rendered layout is exact and fixed. Score/explanation/Glows/Grows,
# an Extra credit section unless the row is at full marks, "Item n of m"
# headers for multi-item students, and a disclosure appended once, never by
# default. See docs/handoffs/consistent-scoring-feedback.md decision 5.
# --------------------------------------------------------------------------

_DIVIDER = "-" * 40


def _full_marks():
    return feedback_results.render_feedback_item(
        {"score": 10, "explanation": "Great work throughout.",
         "glows": ["Clear thesis.", "Strong evidence."],
         "grows": ["Vary sentence length."]},
        possible=10,
    )


def _below_full_model_exemplar():
    return feedback_results.render_feedback_item(
        {"score": 8, "explanation": "Good but incomplete.",
         "glows": ["Clear thesis."], "grows": ["Add more evidence."],
         "fixes": ["Add a quote.", "Fix the conclusion."]},
        possible=10, exemplar="A model paragraph a student could copy.",
    )


def _below_full_teacher_correction():
    return feedback_results.render_feedback_item(
        {"score": 8, "explanation": "Good but incomplete.",
         "glows": ["Clear thesis."], "grows": ["Add more evidence."],
         "fixes": ["Add a quote."]},
        possible=10, exemplar="Ignored because a correction covers this item.",
        correction={"answer": "Because X happens.", "why": "It follows the definition."},
    )


def _null_score():
    return feedback_results.render_feedback_item(
        {"score": None, "explanation": "Comment-only feedback.",
         "glows": ["Careful reading."], "grows": ["Write more."],
         "fixes": ["Add a topic sentence."]},
        possible=10, exemplar="A model paragraph.",
    )


def _unknown_possible():
    return feedback_results.render_feedback_item(
        {"score": 7, "explanation": "Solid attempt.",
         "glows": ["Good detail."], "grows": ["Tighten the ending."],
         "fixes": ["Rewrite the last sentence."]},
        possible=None, exemplar="A model paragraph.",
    )


def _multi_item():
    rows = [
        {"resolved": True, "canvas_id": "1", "item_id": "essay", "score": 4,
         "feedback": "Score: 4/5\n\nStrong opening."},
        {"resolved": True, "canvas_id": "1", "item_id": "photo", "score": 5,
         "feedback": "Score: 5/5\n\nComplete and accurate."},
    ]
    return feedback_results.merge_rows_by_uid(rows)["1"]["feedback"]


def _with_disclosure():
    rows = [{"resolved": True, "canvas_id": "1", "item_id": "essay", "score": 9,
             "feedback": "Score: 9/10\n\nWell done."}]
    return feedback_results.merge_rows_by_uid(
        rows, disclosure="Drafted by AI, reviewed by your teacher.")["1"]["feedback"]


def _without_disclosure():
    rows = [{"resolved": True, "canvas_id": "1", "item_id": "essay", "score": 9,
             "feedback": "Score: 9/10\n\nWell done."}]
    return feedback_results.merge_rows_by_uid(rows)["1"]["feedback"]


_LAYOUT_CASES = {
    "full_marks": (_full_marks, (
        "Score: 10/10\n\n"
        "Great work throughout.\n\n"
        "Glows\n- Clear thesis.\n- Strong evidence.\n\n"
        "Grows\n- Vary sentence length."
    )),
    "below_full_model_exemplar": (_below_full_model_exemplar, (
        "Score: 8/10\n\n"
        "Good but incomplete.\n\n"
        "Glows\n- Clear thesis.\n\n"
        "Grows\n- Add more evidence.\n\n"
        f"{_DIVIDER}\n"
        "Extra credit Part 1: Fix these in a handwritten second draft\n"
        "1. Add a quote.\n2. Fix the conclusion.\n\n"
        "Extra credit Part 2: Hand copy this exemplar\n"
        "A model paragraph a student could copy."
    )),
    "below_full_teacher_correction": (_below_full_teacher_correction, (
        "Score: 8/10\n\n"
        "Good but incomplete.\n\n"
        "Glows\n- Clear thesis.\n\n"
        "Grows\n- Add more evidence.\n\n"
        f"{_DIVIDER}\n"
        "Extra credit Part 1: Fix these in a handwritten second draft\n"
        "1. Add a quote.\n\n"
        "Extra credit Part 2: Hand copy this exemplar\n"
        "Because X happens.\n\nWhy: It follows the definition."
    )),
    "null_score": (_null_score, (
        "Comment-only feedback.\n\n"
        "Glows\n- Careful reading.\n\n"
        "Grows\n- Write more.\n\n"
        f"{_DIVIDER}\n"
        "Extra credit Part 1: Fix these in a handwritten second draft\n"
        "1. Add a topic sentence.\n\n"
        "Extra credit Part 2: Hand copy this exemplar\n"
        "A model paragraph."
    )),
    "unknown_possible": (_unknown_possible, (
        "Score: 7\n\n"
        "Solid attempt.\n\n"
        "Glows\n- Good detail.\n\n"
        "Grows\n- Tighten the ending.\n\n"
        f"{_DIVIDER}\n"
        "Extra credit Part 1: Fix these in a handwritten second draft\n"
        "1. Rewrite the last sentence.\n\n"
        "Extra credit Part 2: Hand copy this exemplar\n"
        "A model paragraph."
    )),
    "multi_item": (_multi_item, (
        "Item 1 of 2\nScore: 4/5\n\nStrong opening.\n\n"
        "Item 2 of 2\nScore: 5/5\n\nComplete and accurate."
    )),
    "with_disclosure": (_with_disclosure,
        "Score: 9/10\n\nWell done.\n\nDrafted by AI, reviewed by your teacher."),
    "without_disclosure": (_without_disclosure, "Score: 9/10\n\nWell done."),
}


@pytest.mark.parametrize("case", sorted(_LAYOUT_CASES))
def test_render_layout_law(case):
    """LAW: exact rendered text for every case in decision 5's fixed layout."""
    build, expected = _LAYOUT_CASES[case]
    assert build() == expected


# --------------------------------------------------------------------------
# LAW: flatten strips markdown decoration and signatures from model-supplied
# fields; paragraph breaks survive with blank runs capped at one.
# --------------------------------------------------------------------------

def test_flatten_text_law_strips_markdown_and_signatures_but_keeps_paragraphs():
    # markdown emphasis and a heading marker are removed
    assert feedback_results.flatten_text(
        "`code` and **bold** and __also bold__ and *italic*.\n# Heading"
    ) == "code and bold and also bold and italic.\nHeading"

    # a code fence's delimiters are removed, its content kept
    assert feedback_results.flatten_text("```\nkept\n```") == "kept"

    # a markdown rule line is removed; the paragraph break it sat in survives
    assert feedback_results.flatten_text("Before.\n---\nAfter.") == "Before.\n\nAfter."

    # a markdown link becomes its text; the URL is gone
    assert feedback_results.flatten_text(
        "See [the guide](https://example.com) for steps."
    ) == "See the guide for steps."

    # ampersands, literal or entity-encoded, become the word "and"
    assert feedback_results.flatten_text("Tom & Jerry & Spike.") == "Tom and Jerry and Spike."
    assert feedback_results.flatten_text("Tom &amp; Jerry.") == "Tom and Jerry."

    # a "Drafted by ..." line and a trailing "- Name" signature are dropped
    assert feedback_results.flatten_text(
        "Good work.\nDrafted by Sage (AI), reviewed by your teacher.\n- Sage"
    ) == "Good work."

    # the existing AI signature line is dropped
    assert feedback_results.flatten_text(
        "Good work.\nCoach Vale (AI teaching assistant)"
    ) == "Good work."

    # list-item markers are stripped and internal newlines collapse to spaces
    assert feedback_results.flatten_text(
        "- first point\nstill the same item", list_item=True
    ) == "first point still the same item"
    assert feedback_results.flatten_text("1. numbered point", list_item=True) == "numbered point"
    assert feedback_results.flatten_text("* starred point", list_item=True) == "starred point"
    assert feedback_results.flatten_text("• bulleted point", list_item=True) == "bulleted point"

    # paragraph breaks survive; blank runs cap at one
    assert feedback_results.flatten_text("First.\n\n\n\nSecond.") == "First.\n\nSecond."
