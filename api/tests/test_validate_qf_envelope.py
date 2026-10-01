"""QuizForge envelope validation, at the level the Create page depends on.

The staged-drafts panel badges a draft "Valid" or "Needs fixes" purely from
``validate_qf.validate``, and only offers "Use this draft" for a valid one. So
anything this function waves through is something the teacher can push straight
to Canvas.
"""
from __future__ import annotations

import json
import re
import textwrap

import pytest

from api import validate_qf


def _envelope(tmp_path, payload, name="draft.txt"):
    """Write ``payload`` inside a QUIZFORGE_JSON envelope and return the path."""
    body = payload if isinstance(payload, str) else json.dumps(payload)
    path = tmp_path / name
    path.write_text(
        "<QUIZFORGE_JSON>\n" + body + "\n</QUIZFORGE_JSON>\n",
        encoding="utf-8",
        newline="\n",
    )
    return str(path)


ONE_GOOD_MC = {
    "version": "3.0-json",
    "title": "Cell Transport Check",
    "items": [
        {
            "id": "mc1",
            "type": "MC",
            "prompt": "<p>Why does dye spread evenly through still water?</p>",
            "points": 2,
            "choices": [
                {"id": "A", "text": "Diffusion down a concentration gradient", "correct": True},
                {"id": "B", "text": "Osmosis across a selectively permeable membrane", "correct": False},
            ],
        }
    ],
    "rationales": [
        {
            "item_id": "mc1",
            "choices": [
                {"id": "A", "correct": True, "rationale": "Particles spread from higher to lower concentration."},
                {"id": "B", "correct": False, "rationale": "Osmosis needs a membrane, and there is none here."},
            ],
        }
    ],
}


def test_a_well_formed_quiz_passes(tmp_path):
    assert validate_qf.validate(_envelope(tmp_path, ONE_GOOD_MC), set()) == []


@pytest.mark.parametrize(
    "payload, description",
    [
        ({"version": "3.0-json", "items": []}, "items present but empty"),
        ({"version": "3.0-json"}, "items key absent entirely"),
        ({"version": "3.0-json", "items": [], "rationales": []}, "empty items with empty rationales"),
    ],
)
def test_a_quiz_with_no_questions_is_rejected(tmp_path, payload, description):
    """An empty draft satisfies every per-item rule by having nothing to check.

    Without an explicit guard it reported as valid, so the staged-drafts panel
    badged it "Valid" and offered to push a quiz with zero questions to Canvas.
    """
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert problems, f"expected rejection for {description}"
    assert any("no questions" in p for p in problems), problems


def test_missing_envelope_is_rejected(tmp_path):
    path = tmp_path / "bare.txt"
    path.write_text(json.dumps(ONE_GOOD_MC), encoding="utf-8", newline="\n")
    problems = validate_qf.validate(str(path), set())
    assert any("envelope" in p for p in problems), problems


def test_invalid_json_is_rejected(tmp_path):
    problems = validate_qf.validate(_envelope(tmp_path, "{not json,,,}"), set())
    assert any("INVALID JSON" in p for p in problems), problems


def test_mc_without_a_correct_choice_is_rejected(tmp_path):
    payload = json.loads(json.dumps(ONE_GOOD_MC))
    for choice in payload["items"][0]["choices"]:
        choice["correct"] = False
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("has 0 correct" in p for p in problems), problems


def test_quiz_without_rationales_is_valid(tmp_path):
    payload = json.loads(json.dumps(ONE_GOOD_MC))
    payload.pop("rationales")
    assert validate_qf.validate(_envelope(tmp_path, payload), set()) == []


# --- Optional feedback shape checks: helpers ---
#
# These mirror the builder helpers from engine/tests/unit/test_rationale_rules.py
# (the orphaned rule set being deleted in the same batch), but build raw
# QuizForge envelopes instead of a domain Quiz, per decision D1: the gate reads
# the file the teacher actually wrote.

def _mc_item(item_id, n_choices):
    return {
        "id": item_id,
        "type": "MC",
        "prompt": "<p>Q?</p>",
        "points": 1,
        "choices": [
            {"id": chr(65 + i), "text": f"c{i}", "correct": i == 0}
            for i in range(n_choices)
        ],
    }


def _tf_item(item_id):
    return {"id": item_id, "type": "TF", "prompt": "<p>Sky is blue?</p>", "answer": True, "points": 1}


def _writing_item(item_id, item_type):
    return {"id": item_id, "type": item_type, "prompt": "<p>Discuss.</p>", "points": 10}


def _per_choice_rationale(item_id, rationale_texts):
    return {
        "item_id": item_id,
        "choices": [
            {"id": chr(65 + i), "correct": i == 0, "rationale": t}
            for i, t in enumerate(rationale_texts)
        ],
    }


def _single_rationale(item_id, text):
    return {"item_id": item_id, "rationale": text}


def _quiz(items, rationales):
    return {"version": "3.0-json", "title": "T", "items": items, "rationales": rationales}


# --- Optional feedback shape checks ---

def test_ported_mc_full_coverage_passes(tmp_path):
    payload = _quiz([_mc_item("q1", 4)], [_per_choice_rationale("q1", ["a", "b", "c", "d"])])
    assert validate_qf.validate(_envelope(tmp_path, payload), set()) == []


def test_mc_partial_arbitrary_length_feedback_is_valid(tmp_path):
    text = "Ask the teacher. This is incorrect. " + ("Additional authored context. " * 40)
    payload = _quiz([_mc_item("q1", 4)], [_per_choice_rationale("q1", [text])])
    assert validate_qf.validate(_envelope(tmp_path, payload), set()) == []


@pytest.mark.parametrize("item_type", ["ESSAY", "FILEUPLOAD"])
def test_writing_items_are_routed_to_separate_assignments(tmp_path, item_type):
    payload = _quiz([_writing_item("writing-1", item_type)], [])
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    [problem] = problems
    assert item_type in problem
    assert "separate AssignmentForge assignment" in problem
    assert "teacher-chosen points" in problem


# --- Depth checks: new coverage not in the ported set ---

def test_mc_rationale_missing_choices_array_is_rejected(tmp_path):
    payload = _quiz([_mc_item("q1", 2)], [{"item_id": "q1", "rationale": "wrong shape for MC"}])
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("needs a choices array" in p for p in problems), problems


def test_per_choice_rationale_rejects_unmapped_top_level_text(tmp_path):
    rationale = _per_choice_rationale("q1", ["Choice A context"])
    rationale["rationale"] = "This extra text has no choice mapping."
    payload = _quiz([_mc_item("q1", 4)], [rationale])
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("cannot include a single rationale field" in p for p in problems), problems


def test_mc_rationale_choice_id_mismatch_is_rejected(tmp_path):
    payload = _quiz([_mc_item("q1", 2)], [_per_choice_rationale("q1", ["a", "b"])])
    payload["rationales"][0]["choices"][1]["id"] = "Z"
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("does not match a choice" in p for p in problems), problems


def test_mc_rationale_choice_id_duplicate_is_rejected(tmp_path):
    payload = _quiz([_mc_item("q1", 4)], [_per_choice_rationale("q1", ["a", "b"])])
    payload["rationales"][0]["choices"][1]["id"] = "A"
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("duplicates choice id" in p for p in problems), problems


def test_mc_item_non_contiguous_choice_ids_are_still_rejected(tmp_path):
    payload = _quiz([_mc_item("q1", 4)], [])
    payload["items"][0]["choices"][3]["id"] = "E"
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("choice ids must be contiguous" in p for p in problems), problems


def test_supplied_single_rationale_whitespace_only_is_rejected(tmp_path):
    payload = _quiz([_tf_item("tf1")], [_single_rationale("tf1", "   ")])
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("non-empty rationale text" in p for p in problems), problems


def test_fitb_wordbank_requires_options_before_push(tmp_path):
    payload = _quiz([{
        "id": "fitb-wordbank",
        "type": "FITB",
        "points": 1,
        "prompt": "The powerhouse is the [blank].",
        "answer_mode": "wordbank",
        "accept": ["mitochondria"],
    }], [_single_rationale("fitb-wordbank", "The answer names the organelle that produces cellular energy.")])
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("wordbank requires" in p and "options" in p for p in problems), problems


def test_fitb_multi_blank_shape_is_validated_by_item(tmp_path):
    payload = _quiz([{
        "id": "fitb-multi",
        "type": "FITB",
        "points": 1,
        "prompt": "The [blank1] is near the [blank2].",
        "accept": [["school"], ["park"]],
    }], [_single_rationale("fitb-multi", "Each answer completes its linked blank in the sentence.")])
    assert validate_qf.validate(_envelope(tmp_path, payload), set()) == []


def test_fitb_single_blank_accepts_nested_accept_shape(tmp_path):
    payload = _quiz([{
        "id": "fitb-nested",
        "type": "FITB",
        "points": 1,
        "prompt": "The powerhouse is the [blank1].",
        "accept": [["mitochondria", "the mitochondria"]],
    }], [_single_rationale("fitb-nested", "The accepted terms identify the organelle that produces cellular energy.")])
    assert validate_qf.validate(_envelope(tmp_path, payload), set()) == []


def test_fitb_multi_blank_rejects_more_than_three_blanks(tmp_path):
    payload = _quiz([{
        "id": "fitb-too-many",
        "type": "FITB",
        "points": 1,
        "prompt": "[blank1] [blank2] [blank3] [blank4]",
        "accept": [["a"], ["b"], ["c"], ["d"]],
    }], [_single_rationale("fitb-too-many", "Each answer would complete a linked blank in the sentence.")])
    problems = validate_qf.validate(_envelope(tmp_path, payload), set())
    assert any("at most 3 linked blanks" in p for p in problems), problems


def test_advise_does_not_apply_automatic_feedback_style_rules(tmp_path):
    payload = _quiz([_tf_item("tf1")], [_single_rationale("tf1", "Ask the teacher.")])
    _, data, _ = validate_qf._load(_envelope(tmp_path, payload))
    assert validate_qf.advise(data) == []


# --- Declared engine: quiz_engine, classic refusals, Hub tier rules ---
#
# One table drives both the staging validator and the planner, because a refusal
# staging accepts but preview rejects (or the reverse) sends the teacher in circles.

def _classic_quiz(items=None, rationales=None, **extra):
    items = [_tf_item("tf1")] if items is None else items
    rationales = [_single_rationale("tf1", "True because the sky scatters blue light.")] \
        if rationales is None else rationales
    return {**_quiz(items, rationales), "quiz_engine": "classic", **extra}


def _hub(tiers, **extra):
    return _classic_quiz(differentiation="hub", tiers=tiers, **extra)


def _essay(item_id="w1", **fields):
    return {"id": item_id, "type": "ESSAY", "prompt": "<p>Discuss.</p>", "points": 20, **fields}


def _plain(item_type, **fields):
    return {"id": "x1", "type": item_type, "prompt": "<p>Q [blank]</p>", "points": 1, **fields}


_SUPPORTS = {"word_bank": ["claim"]}
ENGINE_CASES = {
    "engine absent": ({**ONE_GOOD_MC}, None),
    "engine new": ({**ONE_GOOD_MC, "quiz_engine": "new"}, None),
    "engine classic": (_classic_quiz(), None),
    "engine unknown": ({**ONE_GOOD_MC, "quiz_engine": "bogus"}, 'quiz_engine must be "new" or "classic"'),
    "engine null": ({**ONE_GOOD_MC, "quiz_engine": None}, 'quiz_engine must be "new" or "classic"'),
    "differentiation without classic": (
        {**ONE_GOOD_MC, "differentiation": "hub", "tiers": []}, 'only allowed when quiz_engine is "classic"'),
    "tiers under new": (
        {**ONE_GOOD_MC, "quiz_engine": "new", "tiers": [{"label": "Core", "supports": _SUPPORTS}]},
        'only allowed when quiz_engine is "classic"'),
    "classic essay": (_classic_quiz([_tf_item("tf1"), _essay()]), None),
    "essay needs points": (_classic_quiz([_tf_item("tf1"), _essay(points=None)]), "points"),
    "essay zero points": (_classic_quiz([_tf_item("tf1"), _essay(points=0)]), None),
    "essay needs prompt": (_classic_quiz([_tf_item("tf1"), _essay(prompt="  ")]), "needs a prompt"),
    "essay takes no rationale": (
        _classic_quiz([_tf_item("tf1"), _essay()],
                      [_single_rationale("tf1", "True because."), _single_rationale("w1", "No.")]),
        "does not support rationale feedback"),
    "ordering refused": (_classic_quiz([_plain("ORDERING", items=["a", "b"])], []), "MATCHING or MC"),
    "categorization refused": (_classic_quiz([_plain("CATEGORIZATION", categories=["a", "b"], items=[])], []),
                               "MATCHING or MC"),
    "wordbank refused": (
        _classic_quiz([_plain("FITB", answer_mode="wordbank", options=["x"], accept=["x"])], []), "use answer_mode dropdown"),
    "fuzzy refused": (_classic_quiz([_plain("FITB", accept=["x"], fuzzy_match=True)], []), "fuzzy_match"),
    "case sensitive refused": (_classic_quiz([_plain("FITB", accept=["x"], case_sensitive=True)], []), "case_sensitive"),
    "percent margin refused": (
        _classic_quiz([_plain("NUMERICAL", answer=1, evaluation={"mode": "percent_margin", "value": 5})], []),
        "percent_margin"),
    "decimal places refused": (
        _classic_quiz([_plain("NUMERICAL", answer=1, evaluation={"mode": "decimal_places", "value": 2})], []),
        "decimal_places"),
    "writing keeps its authored points": (_classic_quiz([_tf_item("tf1"), _essay(points=100)]), None),
    "hub one tier": (_hub([{"label": "Support", "supports": _SUPPORTS}]), None),
    "hub two tiers": (_hub([{"label": "support", "supports": _SUPPORTS},
                            {"label": "Core", "supports": {"html": "<p>Hint</p>"}}]), None),
    "bridge refused": (_classic_quiz(differentiation="bridge", tiers=[]), "not available in QuizForge"),
    "tiers need a style": (_classic_quiz(tiers=[{"label": "Core", "supports": _SUPPORTS}]), 'must be "hub"'),
    "hub needs tiers": (_classic_quiz(differentiation="hub"), "requires tiers"),
    "hub needs a tier": (_hub([]), "at least 1 tier"),
    "hub duplicate label": (_hub([{"label": "Core", "supports": _SUPPORTS},
                                  {"label": "core", "supports": _SUPPORTS}]), "is duplicated"),
    "hub unknown label": (_hub([{"label": "Extend", "supports": _SUPPORTS}]), "must be Support, Core, or Accelerate"),
    "hub tier carries a full variant": (
        _hub([{"label": "Core", "supports": _SUPPORTS, "overview": "<p>Hi</p>"}]), "unknown fields"),
    "hub tier supports required": (_hub([{"label": "Core"}]), "supports is required"),
    "hub supports non-empty": (_hub([{"label": "Core", "supports": {"word_bank": []}}]),
                               "must contain sentence_frames, word_bank, or html"),
}


@pytest.mark.parametrize("label", ENGINE_CASES)
def test_the_declared_engine_is_validated_and_the_planner_agrees(tmp_path, label):
    from api import qf_pusher

    payload, refusal = ENGINE_CASES[label]
    path = _envelope(tmp_path, payload)

    problems = validate_qf.validate(path, set())
    if refusal is None:
        assert problems == []
        assert qf_pusher.build_push_plan(path)["version"] == 1
        return
    assert any(refusal in problem for problem in problems), problems
    with pytest.raises(ValueError, match=re.escape(refusal)):
        qf_pusher.build_push_plan(path)
