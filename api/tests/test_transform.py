"""Examples for the auto-graded Canvas New Quiz transforms."""
from api.transform import t_fitb


def test_per_choice_feedback_reads_as_sentences_not_a_because_clause():
    """A two-sentence rationale must not be spliced after "because".

    The rationale shape is a concept sentence followed by a sentence tying it to
    this choice, so the verdict has to stand as its own sentence. Splicing gives
    "is correct because Each HTML element...", which is not English.
    """
    from api.transform import t_mc

    concept = "Each HTML element has one specific job."
    item = {
        "id": "q1",
        "type": "MC",
        "prompt": "<p>Which element links to another page?</p>",
        "choices": [
            {"id": "A", "text": "anchor", "correct": True},
            {"id": "B", "text": "paragraph", "correct": False},
        ],
        "_rationale": {
            "item_id": "q1",
            "choices": [
                {"id": "A", "correct": True, "rationale": f"{concept} The anchor links, so it fits."},
                {"id": "B", "correct": False, "rationale": f"{concept} A paragraph holds text, so it does not fit."},
            ],
        },
    }

    feedback = " ".join(t_mc(item, 1)["item"]["entry"]["answer_feedback"].values())

    assert "because" not in feedback
    assert '"anchor" is correct.' in feedback
    assert '"paragraph" is wrong.' in feedback
    assert concept in feedback


def test_repeated_concept_sentence_is_said_once_per_feedback_box():
    """One box can show several rows; the shared concept sentence belongs once.

    Rationales repeat their concept sentence across an item's choices on purpose,
    so whichever row a student lands on teaches on its own. A single feedback box
    shows the correct answer plus the choice they picked, and there the repeat is
    noise. MA is the sharp case: every correct choice contributes a row.
    """
    from api.transform import t_ma

    concept = "A multi-answer item needs every true option chosen."
    item = {
        "id": "m1",
        "type": "MA",
        "prompt": "<p>Pick the true ones.</p>",
        "choices": [
            {"id": "A", "text": "alpha", "correct": True},
            {"id": "B", "text": "beta", "correct": True},
            {"id": "C", "text": "gamma", "correct": False},
        ],
        "_rationale": {
            "item_id": "m1",
            "choices": [
                {"id": "A", "correct": True, "rationale": f"{concept} Alpha is true, so it fits."},
                {"id": "B", "correct": True, "rationale": f"{concept} Beta is true, so it fits."},
                {"id": "C", "correct": False, "rationale": f"{concept} Gamma is false, so it does not fit."},
            ],
        },
    }

    built = t_ma(item, 1)["item"]["entry"]
    labels = {c["id"]: c["item_body"] for c in built["interaction_data"]["choices"]}
    wrong_box = next(fb for cid, fb in built["answer_feedback"].items()
                     if "gamma" in labels[cid])

    assert wrong_box.count(concept) == 1
    # Every row's own applied sentence survives the dedupe.
    for tail in ("Alpha is true", "Beta is true", "Gamma is false"):
        assert tail in wrong_box


def test_one_sentence_rationale_survives_dedupe_intact():
    """Dropping the lead must never empty a rationale that is only a lead."""
    from api.transform import t_mc

    shared = "Same single sentence."
    item = {
        "id": "q1",
        "type": "MC",
        "prompt": "<p>p</p>",
        "choices": [
            {"id": "A", "text": "right", "correct": True},
            {"id": "B", "text": "wrong", "correct": False},
        ],
        "_rationale": {
            "item_id": "q1",
            "choices": [
                {"id": "A", "correct": True, "rationale": shared},
                {"id": "B", "correct": False, "rationale": shared},
            ],
        },
    }

    wrong_box = next(fb for cid, fb in t_mc(item, 1)["item"]["entry"]["answer_feedback"].items()
                     if "wrong" in fb)

    assert '"wrong" is wrong. Same single sentence.' in wrong_box


def test_fitb_wordbank_uses_canvas_choice_id_scoring():
    built = t_fitb({
        "id": "fitb1",
        "type": "FITB",
        "prompt": "The powerhouse is the [blank].",
        "answer_mode": "wordbank",
        "accept": ["mitochondria"],
        "options": ["mitochondria", "nucleus"],
    }, 1)["item"]["entry"]

    blank = built["interaction_data"]["blanks"][0]
    choices = built["interaction_data"]["word_bank_choices"]
    score = built["scoring_data"]["value"][0]
    selected = next(choice for choice in choices if choice["item_body"] == "mitochondria")
    assert blank["answer_type"] == "wordbank"
    assert blank["choices"] is None
    assert score["scoring_algorithm"] == "TextEquivalence"
    assert score["scoring_data"]["choice_id"] == selected["id"]


def test_fitb_multi_blank_builds_one_open_entry_per_blank():
    built = t_fitb({
        "id": "fitb2",
        "type": "FITB",
        "prompt": "The [blank1] is near the [blank2].",
        "accept": [["school"], ["park", "the park"]],
    }, 1)["item"]["entry"]

    assert len(built["interaction_data"]["blanks"]) == 2
    assert len(built["scoring_data"]["value"]) == 2
    assert all(row["scoring_algorithm"] == "TextCloseEnough"
               for row in built["scoring_data"]["value"])


def test_fitb_multi_blank_rejects_more_than_three_blanks():
    item = {
        "id": "fitb3",
        "type": "FITB",
        "prompt": "[blank1] [blank2] [blank3] [blank4]",
        "accept": [["a"], ["b"], ["c"], ["d"]],
    }
    import pytest
    with pytest.raises(ValueError, match="at most 3 blanks"):
        t_fitb(item, 1)
