"""QuizForge item -> Canvas Classic Quiz question.

One builder per QF type; field shapes come from the verified table in
``docs/reference/classic-quiz-design.md``. Rationales arrive pre-merged as
``item["_rationale"]`` (see ``qf_pusher.prepare_items``). The planner sets each
question's ``points_possible`` afterwards. Types Classic Quizzes cannot hold are
refused with the same one-sentence message ``validate_qf`` gives at staging.
"""
import re

from api import validate_qf
from api.transform import _normalize_canvas_payload, _p

_BLANK = re.compile(r"\[(blank\d*)\]")


def _question(item, question_type, *, text=None, answers=None, **fields):
    question = {
        "question_name": item.get("id") or item["type"],
        "question_text": (item.get("prompt") or "") if text is None else text,
        "question_type": question_type,
        **fields,
    }
    if answers is not None:
        question["answers"] = answers
    return {"question": question}


def _neutral(item):
    text = (item.get("_rationale") or {}).get("rationale")
    return {"neutral_comments_html": _p(text)} if text else {}


def _answer_body(text):
    text = str(text)
    return {"answer_html": text} if re.search(r"<[a-zA-Z/]", text) else {"answer_text": text}


def _choice_answers(item):
    rationale = {c.get("id"): c.get("rationale", "")
                 for c in (item.get("_rationale") or {}).get("choices", [])}
    answers = []
    for choice in item["choices"]:
        answer = {**_answer_body(choice["text"]),
                  "answer_weight": 100 if choice.get("correct") else 0}
        if rationale.get(choice.get("id")):
            answer["answer_comment_html"] = _p(rationale[choice["id"]])
        answers.append(answer)
    return answers


def q_mc(item):
    return _question(item, "multiple_choice_question", answers=_choice_answers(item))


def q_ma(item):
    return _question(item, "multiple_answers_question", answers=_choice_answers(item))


def q_tf(item):
    correct = bool(item["answer"])
    answers = [{"answer_text": label, "answer_weight": 100 if (label == "True") == correct else 0}
               for label in ("True", "False")]
    return _question(item, "true_false_question", answers=answers, **_neutral(item))


def q_matching(item):
    answers = [{"answer_match_left": pair["left"], "answer_match_right": pair["right"]}
               for pair in item["pairs"]]
    extra = {}
    distractors = [str(value) for value in item.get("distractors", [])]
    if distractors:
        extra["matching_answer_incorrect_matches"] = "\n".join(distractors)
    return _question(item, "matching_question", answers=answers, **extra, **_neutral(item))


def q_fitb(item):
    prompt = str(item["prompt"])
    tokens = _BLANK.findall(prompt)
    mode = str(item.get("answer_mode", "open_entry") or "open_entry").lower()
    accept = item.get("accept", []) or []
    if len(tokens) <= 1 and isinstance(accept, list) and len(accept) == 1 and isinstance(accept[0], list):
        accept = accept[0]
    label = f"FITB item {item.get('id')!r}"

    if len(tokens) > 1:
        if len(tokens) > 3 or len(set(tokens)) != len(tokens):
            raise ValueError(f"{label} needs 2 or 3 uniquely numbered blanks.")
        if mode != "open_entry" or not isinstance(accept, list) or len(accept) != len(tokens) \
                or not all(isinstance(group, list) and group for group in accept):
            raise ValueError(f"{label} needs open_entry and one non-empty accept array per blank.")
        answers = [{"answer_text": str(value), "answer_weight": 100, "blank_id": name}
                   for name, group in zip(tokens, accept) for value in group]
        return _question(item, "fill_in_multiple_blanks_question", answers=answers, **_neutral(item))

    accepted = [str(value) for value in accept if not isinstance(value, list)] if isinstance(accept, list) else []
    if not accepted:
        raise ValueError(f"{label} needs a non-empty accept array.")
    if mode == "dropdown":
        options = [str(value) for value in (item.get("options") or [])]
        wanted = {value.strip().casefold() for value in accepted}
        answers = [{"answer_text": option, "blank_id": tokens[0],
                    "answer_weight": 100 if option.strip().casefold() in wanted else 0}
                   for option in options]
        if not any(answer["answer_weight"] for answer in answers):
            raise ValueError(f"{label} needs its accept answer to appear in options.")
        return _question(item, "multiple_dropdowns_question", answers=answers, **_neutral(item))
    # A short-answer question shows its own answer box, so the blank token becomes a rule.
    answers = [{"answer_text": value, "answer_weight": 100} for value in accepted]
    return _question(item, "short_answer_question", text=_BLANK.sub("_____", prompt),
                     answers=answers, **_neutral(item))


def q_numerical(item):
    evaluation = item.get("evaluation") or {}
    mode = evaluation.get("mode", "exact")
    answer = item["answer"]
    if mode == "range":
        row = {"numerical_answer_type": "range_answer",
               "answer_range_start": evaluation.get("min"), "answer_range_end": evaluation.get("max")}
    elif mode == "significant_digits":
        row = {"numerical_answer_type": "precision_answer",
               "answer_approximate": answer, "answer_precision": evaluation.get("value")}
    else:  # exact and absolute_margin
        margin = evaluation.get("value") if mode == "absolute_margin" else 0
        row = {"numerical_answer_type": "exact_answer",
               "answer_exact": answer, "answer_error_margin": margin}
    return _question(item, "numerical_question", answers=[{**row, "answer_weight": 100}],
                     **_neutral(item))


def q_essay(item):
    return _question(item, "essay_question")


def q_fileupload(item):
    return _question(item, "file_upload_question")


BUILDERS = {
    "MC": q_mc, "MA": q_ma, "TF": q_tf, "MATCHING": q_matching, "FITB": q_fitb,
    "NUMERICAL": q_numerical, "ESSAY": q_essay, "FILEUPLOAD": q_fileupload,
}


def build_question(qf_item, position):
    """Return ``{"question": {...}}`` for one prepared QuizForge item."""
    problems = validate_qf.classic_item_problems(qf_item, set())
    if problems:
        raise ValueError(problems[0])
    item_type = qf_item["type"]
    if item_type not in BUILDERS:
        raise ValueError(f"No classic builder for QF type {item_type!r}")
    built = BUILDERS[item_type](qf_item)
    built["question"]["position"] = position
    return _normalize_canvas_payload(built)
