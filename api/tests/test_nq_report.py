"""Offline tests for the New Quizzes student_analysis parser.

Runs in CI — no live Canvas, no PII (fixture is fully synthetic). Validates the
positional parse, prompt<->response pairing, choice-vs-essay handling, multi-line
quoted-HTML integrity, entity decoding, and summary fields.
"""
import os

from api.nq_report import (
    constructed_responses,
    html_to_text,
    parse_student_analysis_file,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "student_analysis_sample.csv")


def _parsed():
    return parse_student_analysis_file(FIXTURE)


def test_structure_and_prompts():
    data = _parsed()
    assert len(data["students"]) == 3
    prompts = data["quiz"]["item_prompts"]
    assert prompts == [
        "What is the capital of France?",
        "Which gas do plants absorb?",
        "Explain why the sky appears blue. Use evidence.",
        "Describe your favorite season and why.",
    ]
    assert data["quiz"]["points_possible"] == 28.0


def test_prompt_response_pairing_and_types():
    ada = _parsed()["students"][0]
    assert ada["name"] == "Ada Lovelace"
    assert ada["canvas_id"] == "9001"
    assert len(ada["items"]) == 4
    # choice cell holds the chosen option TEXT
    q1 = ada["items"][0]
    assert q1["type"] == "choice"
    assert q1["prompt"] == "What is the capital of France?"
    assert q1["response"] == "Paris"
    assert q1["earned_points"] == 4.0
    # essay item carries HTML
    assert ada["items"][2]["type"] == "essay"


def test_multiline_html_and_entities_preserved():
    alan = _parsed()["students"][1]
    # The sky essay spanned two physical lines + an &amp; entity inside a quoted field.
    sky = alan["items"][2]
    assert sky["response"].count("<p>") == 2          # both paragraphs captured as one cell
    text = html_to_text(sky["response"])
    assert "shorter wavelength & scatters more" in text   # entity decoded, paragraphs joined
    assert "\n" in text                                   # paragraph break preserved


def test_choice_with_comma_and_blank_response():
    grace = _parsed()["students"][2]
    # blank essay response -> empty string, status still present
    blank = grace["items"][2]
    assert blank["response"] == ""
    assert grace["no_response"] == 1
    assert grace["num_correct"] == 1 and grace["num_incorrect"] == 1
    # Alan's "Winter, because I like snow." has a comma inside a quoted field
    winter = _parsed()["students"][1]["items"][3]
    assert winter["response"] == "<p>Winter, because I like snow.</p>"


def test_constructed_responses_filters_choice():
    ada = _parsed()["students"][0]
    written = constructed_responses(ada)
    assert len(written) == 2
    assert all(it["type"] != "choice" for it in written)


def test_item_points_possible_inferred_from_cohort():
    data = _parsed()
    # Cohort maxima: c1 Paris=4, c2 CO2=4, essay1 max=10 (Alan), essay2 max=8 (Ada).
    assert data["quiz"]["item_points_possible"] == [4.0, 4.0, 10.0, 8.0]
    # And attached per-item on each student.
    ada = data["students"][0]
    assert ada["items"][2]["points_possible_est"] == 10.0
