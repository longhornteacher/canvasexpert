"""Examples for the auto-graded Canvas New Quiz transforms."""
from api.transform import build_item, t_fitb


def test_new_quiz_student_payload_contains_no_em_dashes():
    item = {
        "id": "q1",
        "type": "MC",
        "prompt": "<p>Which path \u2014 if any \u2014 is correct?</p>",
        "choices": [
            {"id": "A", "text": "This one \u2014 yes", "correct": True},
            {"id": "B", "text": "That one", "correct": False},
        ],
        "_rationale": {
            "item_id": "q1",
            "choices": [
                {"id": "A", "correct": True, "rationale": "It fits \u2014 exactly."},
                {"id": "B", "correct": False, "rationale": "It does not fit."},
            ],
        },
    }

    assert "\u2014" not in repr(build_item(item, 1))


def test_mc_feedback_maps_only_supplied_choice_text_without_generated_prose():
    from api.transform import t_mc

    authored = "One sentence in my chosen voice."
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
                {"id": "A", "correct": True, "rationale": authored},
            ],
        },
    }

    entry = t_mc(item, 1)["item"]["entry"]
    answer_feedback = entry["answer_feedback"]
    choice_id = entry["interaction_data"]["choices"][0]["id"]

    assert answer_feedback == {choice_id: f"<p>{authored}</p>"}
    assert entry["scoring_data"]["value"] == choice_id


def test_mc_without_authored_feedback_emits_no_feedback_prose():
    from api.transform import t_mc

    item = {"id": "q1", "type": "MC", "prompt": "<p>Pick one.</p>",
            "choices": [{"id": "A", "text": "Alpha", "correct": True},
                        {"id": "B", "text": "Beta", "correct": False}]}
    entry = t_mc(item, 1)["item"]["entry"]

    assert entry["answer_feedback"] == {}
    assert entry["scoring_data"]["value"]


def test_ma_feedback_maps_authored_choices_without_recombining_them():
    from api.transform import t_ma

    authored = "This is incorrect. Ask the teacher, or explain your own approach."
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
                {"id": "C", "correct": False, "rationale": authored},
            ],
        },
    }

    built = t_ma(item, 1)["item"]["entry"]
    labels = {c["id"]: c["item_body"] for c in built["interaction_data"]["choices"]}
    gamma_id = next(cid for cid, label in labels.items() if "gamma" in label)

    assert built["answer_feedback"] == {gamma_id: f"<p>{authored}</p>"}


def test_one_sentence_rationale_survives_intact():
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

    entry = t_mc(item, 1)["item"]["entry"]
    assert all(feedback == f"<p>{shared}</p>" for feedback in entry["answer_feedback"].values())


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
