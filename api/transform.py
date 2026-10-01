"""QuizForge item -> Canvas New Quizzes API item.

One builder per QF type. Shapes and scoring_algorithm values were verified live
against the sandbox (see qf_materials/qf quiz examples/README.md). Rationales are
expected to be pre-merged onto each item as item["_rationale"] by the pusher.

STIMULUS / STIMULUS_END are NOT handled here — the pusher strips them and inlines
their HTML into the attached items' bodies before transform runs.
"""
import re
import uuid

from api.student_text import normalize_student_text


def _u():
    return str(uuid.uuid4())


def _p(text):
    """Wrap bare text in <p>; leave existing HTML alone."""
    text = str(text)
    return text if text.strip().startswith("<") else f"<p>{text}</p>"


def _wrap(entry, position, points=1):
    return {"item": {"entry_type": "Item", "position": position,
                     "points_possible": points, "entry": entry}}


def _neutral_feedback(item):
    """For single-rationale types: rationale string -> feedback.neutral."""
    rat = item.get("_rationale") or {}
    text = rat.get("rationale")
    return {"neutral": _p(text)} if text else {}


# --------------------------------------------------------------------------- #
def t_mc(item, pos):
    rat = {c["id"]: c.get("rationale", "")
           for c in (item.get("_rationale") or {}).get("choices", [])}
    choices, idmap, correct_qid = [], {}, None
    for i, c in enumerate(item["choices"], 1):
        cu = _u()
        idmap[c["id"]] = cu
        choices.append({"id": cu, "position": i, "item_body": _p(c["text"])})
        if c.get("correct"):
            correct_qid = c["id"]
    answer_feedback = {}
    for c in item["choices"]:
        cu = idmap[c["id"]]
        rationale = rat.get(c["id"])
        if rationale:
            answer_feedback[cu] = _p(rationale)
    entry = {
        "title": item.get("id", "MC"),
        "item_body": item["prompt"],
        "interaction_type_slug": "choice",
        "interaction_data": {"choices": choices},
        "scoring_data": {"value": idmap[correct_qid]},
        "scoring_algorithm": "Equivalence",
        "answer_feedback": answer_feedback,
    }
    return _wrap(entry, pos)


def t_ma(item, pos):
    rat = {c["id"]: c.get("rationale", "")
           for c in (item.get("_rationale") or {}).get("choices", [])}
    choices, idmap, correct = [], {}, []
    for i, c in enumerate(item["choices"], 1):
        cu = _u()
        idmap[c["id"]] = cu
        choices.append({"id": cu, "position": i, "item_body": _p(c["text"])})
        if c.get("correct"):
            correct.append(cu)
    answer_feedback = {}
    for c in item["choices"]:
        cu = idmap[c["id"]]
        rationale = rat.get(c["id"])
        if rationale:
            answer_feedback[cu] = _p(rationale)
    entry = {
        "title": item.get("id", "MA"),
        "item_body": item["prompt"],
        "interaction_type_slug": "multi-answer",
        "interaction_data": {"choices": choices},
        "scoring_data": {"value": correct},
        "scoring_algorithm": "AllOrNothing",
        "answer_feedback": answer_feedback,
    }
    return _wrap(entry, pos)


def t_tf(item, pos):
    entry = {
        "title": item.get("id", "TF"),
        "item_body": item["prompt"],
        "interaction_type_slug": "true-false",
        "interaction_data": {"true_choice": "True", "false_choice": "False"},
        "scoring_data": {"value": bool(item["answer"])},
        "scoring_algorithm": "Equivalence",
        "feedback": _neutral_feedback(item),
    }
    return _wrap(entry, pos)


def t_numeric(item, pos):
    # Confirmed live: numeric needs scoring_algorithm "Numeric" and a value ARRAY
    # of response objects (with "Equivalence" the value array is rejected AND the
    # student's response won't display/grade).
    ev = item.get("evaluation") or {}
    mode = ev.get("mode", "exact")
    ans = str(item["answer"])
    rid = _u()
    if mode == "range":
        resp = {"id": rid, "type": "withinARange",
                "start": str(ev.get("min")), "end": str(ev.get("max"))}
    elif mode == "percent_margin":
        resp = {"id": rid, "type": "marginOfError", "value": ans,
                "margin": str(ev.get("value")), "margin_type": "percent"}
    elif mode == "absolute_margin":
        resp = {"id": rid, "type": "marginOfError", "value": ans,
                "margin": str(ev.get("value")), "margin_type": "absolute"}
    elif mode in ("decimal_places", "significant_digits"):
        ptype = "decimals" if mode == "decimal_places" else "significantDigits"
        resp = {"id": rid, "type": "preciseResponse", "value": ans,
                "precision": str(ev.get("value")), "precision_type": ptype}
    else:  # exact
        resp = {"id": rid, "type": "exactResponse", "value": ans}
    entry = {
        "title": item.get("id", "NUM"),
        "item_body": item["prompt"],
        "interaction_type_slug": "numeric",
        "interaction_data": {},
        "scoring_data": {"value": [resp]},
        "scoring_algorithm": "Numeric",
        "feedback": _neutral_feedback(item),
    }
    return _wrap(entry, pos)


def t_fitb(item, pos):
    tokens = re.findall(r"\[blank\d*\]", item["prompt"])
    accept = item.get("accept", []) or []
    mode = str(item.get("answer_mode", "open_entry") or "open_entry").lower()
    case_sensitive = bool(item.get("case_sensitive", False))
    body = re.sub(r"\[blank\d*\]", "`Blank`", item["prompt"])

    # A one-element array-of-arrays is an unambiguous single-blank form. Keep
    # it equivalent to the documented flat accept list.
    if len(tokens) <= 1 and isinstance(accept, list) and len(accept) == 1 and isinstance(accept[0], list):
        accept = accept[0]

    if len(tokens) > 1:
        if len(tokens) > 3:
            raise ValueError(
                f"{item.get('id')}: multi-blank FITB supports at most 3 blanks.")
        if mode != "open_entry":
            raise ValueError(
                f"{item.get('id')}: multi-blank FITB supports open_entry only; "
                f"use separate single-blank items for {mode}.")
        if not isinstance(accept, list) or len(accept) != len(tokens) or not all(isinstance(group, list) and group for group in accept):
            raise ValueError(
                f"{item.get('id')}: multi-blank FITB requires one non-empty "
                "accept array per blank.")
        blanks = []
        values = []
        for group in accept:
            bid = _u()
            answer = str(group[0])
            blanks.append({"id": bid, "answer_type": "openEntry"})
            values.append({
                "id": bid,
                "scoring_data": {
                    "value": answer,
                    "blank_text": answer,
                    "ignore_case": not case_sensitive,
                    "edit_distance": 1,
                },
                "scoring_algorithm": "TextCloseEnough",
            })
        interaction_data = {
            "blanks": blanks,
            "word_bank_choices": [],
            "reuse_word_bank_choices": False,
        }
        scoring_data = {"value": values, "working_item_body": body}
    else:
        bid = _u()
        answers = [str(value) for value in accept if not isinstance(value, list)] if isinstance(accept, list) else []
        answer = answers[0] if answers else ""
        options = [str(value) for value in (item.get("options") or [])]
        if mode == "wordbank":
            if not options:
                raise ValueError(f"{item.get('id')}: FITB wordbank requires a non-empty options array.")
            word_bank = [{"id": _u(), "item_body": option} for option in options]
            selected = next((choice for choice in word_bank if choice["item_body"].strip().lower() in {value.strip().lower() for value in answers}), None)
            if selected is None:
                raise ValueError(f"{item.get('id')}: FITB wordbank accept answer must appear in options.")
            blanks = [{"id": bid, "choices": None, "answer_type": "wordbank"}]
            interaction_data = {
                "blanks": blanks,
                "word_bank_choices": word_bank,
                "reuse_word_bank_choices": True,
            }
            scoring_data = {"value": answer, "choice_id": selected["id"], "blank_text": answer}
            scoring_algorithm = "TextEquivalence"
        elif mode == "dropdown":
            if not options:
                raise ValueError(f"{item.get('id')}: FITB dropdown requires a non-empty options array.")
            choices = [{"id": _u(), "position": index, "item_body": option} for index, option in enumerate(options, 1)]
            selected = next((choice for choice in choices if choice["item_body"].strip().lower() in {value.strip().lower() for value in answers}), None)
            if selected is None:
                raise ValueError(f"{item.get('id')}: FITB dropdown accept answer must appear in options.")
            blanks = [{"id": bid, "choices": choices, "answer_type": "dropdown"}]
            interaction_data = {"blanks": blanks}
            scoring_data = {"value": selected["id"], "blank_text": answer}
            scoring_algorithm = "Equivalence"
        else:
            blanks = [{"id": bid, "answer_type": "openEntry"}]
            interaction_data = {
                "blanks": blanks,
                "word_bank_choices": [],
                "reuse_word_bank_choices": False,
            }
            scoring_data = {
                "value": answer,
                "blank_text": answer,
                "ignore_case": not case_sensitive,
                "edit_distance": 1,
            }
            scoring_algorithm = "TextCloseEnough"

    entry = {
        "title": item.get("id", "FITB"),
        "item_body": body,
        "interaction_type_slug": "rich-fill-blank",
        "interaction_data": interaction_data,
        "scoring_data": {
            "value": ([{
                "id": bid,
                "scoring_data": scoring_data,
                "scoring_algorithm": scoring_algorithm,
            }] if len(tokens) <= 1 else scoring_data["value"]),
            "working_item_body": body,
        },
        "scoring_algorithm": "MultipleMethods",
        "feedback": _neutral_feedback(item),
    }
    return _wrap(entry, pos)


def t_matching(item, pos):
    pairs = item["pairs"]
    distractors = list(item.get("distractors", []))
    questions, value, matches = [], {}, []
    answers = [p["right"] for p in pairs] + distractors
    for i, p in enumerate(pairs, 1):
        qid = str(10000 + i)
        questions.append({"id": qid, "item_body": p["left"]})
        value[qid] = p["right"]
        matches.append({"answer_body": p["right"], "question_id": qid,
                        "question_body": p["left"]})
    entry = {
        "title": item.get("id", "MATCH"),
        "item_body": item["prompt"],
        "interaction_type_slug": "matching",
        "interaction_data": {"answers": answers, "questions": questions},
        "scoring_data": {"value": value,
                         "edit_data": {"matches": matches, "distractors": distractors}},
        "scoring_algorithm": "DeepEquals",
        "feedback": _neutral_feedback(item),
    }
    return _wrap(entry, pos)


def t_ordering(item, pos):
    choices, order = {}, []
    for entry_text in item["items"]:
        cu = _u()
        choices[cu] = {"id": cu, "item_body": str(entry_text)}  # plain text
        order.append(cu)
    entry = {
        "title": item.get("id", "ORDER"),
        "item_body": item.get("prompt") or _p(item.get("header", "Put in order")),
        "interaction_type_slug": "ordering",
        "interaction_data": {"choices": choices},
        "scoring_data": {"value": order},
        "scoring_algorithm": "DeepEquals",
        "feedback": _neutral_feedback(item),
    }
    return _wrap(entry, pos)


def t_categorization(item, pos):
    categories, cat_order, label_to_cat = {}, [], {}
    for label in item["categories"]:
        cu = _u()
        categories[cu] = {"id": cu, "item_body": label}
        cat_order.append(cu)
        label_to_cat[label] = cu
    distractors, cat_items = {}, {cu: [] for cu in cat_order}
    for it in item["items"]:
        iu = _u()
        distractors[iu] = {"id": iu, "item_body": it["label"]}
        cat_items[label_to_cat[it["category"]]].append(iu)
    for d in item.get("distractors", []):       # unsortable extras
        iu = _u()
        distractors[iu] = {"id": iu, "item_body": d}
    value = [{"id": cu, "scoring_data": {"value": cat_items[cu]},
              "scoring_algorithm": "AllOrNothing"} for cu in cat_order]
    entry = {
        "title": item.get("id", "CAT"),
        "item_body": item["prompt"],
        "interaction_type_slug": "categorization",
        "interaction_data": {"categories": categories, "distractors": distractors,
                             "category_order": cat_order},
        "scoring_data": {"value": value, "score_method": "all_or_nothing"},
        "scoring_algorithm": "Categorization",
        "feedback": _neutral_feedback(item),
    }
    return _wrap(entry, pos)


BUILDERS = {
    "MC": t_mc, "MA": t_ma, "TF": t_tf, "NUMERICAL": t_numeric, "FITB": t_fitb,
    "MATCHING": t_matching, "ORDERING": t_ordering, "CATEGORIZATION": t_categorization,
}


def _normalize_canvas_payload(value):
    """Normalize every text value that will be displayed by New Quizzes."""
    if isinstance(value, dict):
        return {key: _normalize_canvas_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_canvas_payload(item) for item in value]
    if isinstance(value, str):
        return normalize_student_text(value)
    return value


def build_item(qf_item, position):
    t = qf_item["type"]
    if t not in BUILDERS:
        raise ValueError(f"No transformer for QF type {t!r}")
    return _normalize_canvas_payload(BUILDERS[t](qf_item, position))
