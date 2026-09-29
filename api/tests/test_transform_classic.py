"""Classic question builders: every QuizForge type maps to its classic type or is refused.

Driven from the validator's own type set, so a new QuizForge type without a case here
fails ``test_every_validator_type_has_a_case`` rather than slipping through unmapped.
"""
import pytest

from api import validate_qf
from api.transform_classic import build_question

CHOICES = [{"id": "A", "text": "Alpha", "correct": True}, {"id": "B", "text": "Beta", "correct": False}]


def _item(item_type, **fields):
    return {"id": f"{item_type.lower()}1", "type": item_type, "prompt": "<p>Prompt [blank].</p>", **fields}


CASES = {
    "MC": (_item("MC", choices=CHOICES), "multiple_choice_question", None),
    "MA": (_item("MA", choices=[*CHOICES, {"id": "C", "text": "Gamma", "correct": True}]),
           "multiple_answers_question", None),
    "TF": (_item("TF", answer=False), "true_false_question", None),
    "MATCHING": (_item("MATCHING", pairs=[{"left": "a", "right": "1"}, {"left": "b", "right": "2"}],
                       distractors=["3"]), "matching_question", None),
    "FITB open single": (_item("FITB", accept=["mitochondria"]), "short_answer_question", None),
    "FITB open multi": (_item("FITB", prompt="[blank1] near [blank2]", accept=[["a"], ["b"]]),
                        "fill_in_multiple_blanks_question", None),
    "FITB dropdown": (_item("FITB", answer_mode="dropdown", options=["x", "y"], accept=["x"]),
                      "multiple_dropdowns_question", None),
    "FITB wordbank": (_item("FITB", answer_mode="wordbank", options=["x"], accept=["x"]), None, "word bank"),
    "FITB fuzzy": (_item("FITB", accept=["x"], fuzzy_match=True), None, "fuzzy_match"),
    "FITB case sensitive": (_item("FITB", accept=["x"], case_sensitive=True), None, "case_sensitive"),
    "NUMERICAL exact": (_item("NUMERICAL", answer=4), "numerical_question", None),
    "NUMERICAL absolute margin": (_item("NUMERICAL", answer=4, evaluation={"mode": "absolute_margin", "value": 0.5}),
                                  "numerical_question", None),
    "NUMERICAL range": (_item("NUMERICAL", answer=4, evaluation={"mode": "range", "min": 3, "max": 5}),
                        "numerical_question", None),
    "NUMERICAL significant digits": (_item("NUMERICAL", answer=4, evaluation={"mode": "significant_digits", "value": 2}),
                                     "numerical_question", None),
    "NUMERICAL percent margin": (_item("NUMERICAL", answer=4, evaluation={"mode": "percent_margin", "value": 5}),
                                 None, "percent_margin"),
    "NUMERICAL decimal places": (_item("NUMERICAL", answer=4, evaluation={"mode": "decimal_places", "value": 2}),
                                 None, "decimal_places"),
    "ESSAY": (_item("ESSAY", points=10), "essay_question", None),
    "FILEUPLOAD": (_item("FILEUPLOAD", points=10), "file_upload_question", None),
    "ORDERING": (_item("ORDERING", items=["a", "b"]), None, "MATCHING or MC"),
    "CATEGORIZATION": (_item("CATEGORIZATION", categories=["a", "b"], items=[]), None, "MATCHING or MC"),
}
NUMERIC_ANSWER_TYPES = {
    "NUMERICAL exact": "exact_answer", "NUMERICAL absolute margin": "exact_answer",
    "NUMERICAL range": "range_answer", "NUMERICAL significant digits": "precision_answer",
}


def test_every_validator_type_has_a_case():
    scored = (validate_qf.ALL_TYPES - {"STIMULUS", "STIMULUS_END"}) | validate_qf.WRITING_TYPES
    assert scored <= {case.split()[0] for case in CASES}


@pytest.mark.parametrize("label", CASES)
def test_each_case_maps_to_its_classic_type_or_raises_the_classic_refusal(label):
    item, question_type, refusal = CASES[label]
    if refusal:
        with pytest.raises(ValueError, match=refusal):
            build_question(item, 1)
        return
    question = build_question(item, 3)["question"]
    assert question["question_type"] == question_type
    assert question["position"] == 3
    if label in NUMERIC_ANSWER_TYPES:
        assert question["answers"][0]["numerical_answer_type"] == NUMERIC_ANSWER_TYPES[label]
