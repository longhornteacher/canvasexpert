"""Build a deterministic, no-network plan from a QuizForge file.

Pipeline:
  1. Read the .txt, extract JSON from the <QUIZFORGE_JSON> envelope.
  2. Merge rationales onto their items (item["_rationale"]).
  3. Inline STIMULUS HTML into each attached item (by explicit stimulus_id),
     then drop STIMULUS / STIMULUS_END.
  4. Return the quiz and item payloads for the reviewed runtime operation.

This module plans only. It never talks to Canvas.
"""
import json
import os
import re

from api import codefmt, teks, transform, transform_classic, validate_qf
from api.student_text import normalize_student_text
from engine.validation.authored_points import authored_points, authored_total

ENVELOPE = re.compile(r"<QUIZFORGE_JSON>(.*?)</QUIZFORGE_JSON>", re.DOTALL)
# QuizForge defaults applied to every pushed quiz:
#  - shuffle answers (not questions, to keep stimulus-attached items together)
QUIZ_SETTINGS = {
    "shuffle_answers": True,
    "shuffle_questions": False,
}

WRITING_TYPES = {"ESSAY", "FILEUPLOAD"}

SETTING_KEYS = (
    "shuffle_answers", "shuffle_questions", "access_code",
    "allow_multiple_attempts", "score_to_keep", "allowed_attempts",
    "build_on_last_attempt", "attempt_cooldown", "has_time_limit",
    "time_limit_minutes", "calculator_type", "one_at_a_time",
    "allow_backtracking", "hide_results", "due_at", "unlock_at", "lock_at",
    "assignment_group_id", "assignment_group_name", "post_to_sis", "published",
    "module_id", "module_name",
)


def distribute_points(items, total=None):
    """Return authored scored-item values without inferring or rescaling them."""
    return authored_points(items, total)


def load_qf(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    m = ENVELOPE.search(raw)
    payload = m.group(1).strip() if m else raw  # tolerate bare JSON too
    return json.loads(payload)


def _reject_writing_items(data):
    """Reject writing immediately after parse, before preparation or transport."""
    for item in data.get("items") or []:
        item_type = str((item or {}).get("type") or "")
        if item_type in WRITING_TYPES:
            raise ValueError(
                f"{item_type} cannot be pushed as a Canvas New Quiz. Author each "
                "writing portion as a separate AssignmentForge assignment with "
                "teacher-chosen points (for example, a matching ' - ECR' assignment)."
            )


def prepare_items(data):
    """Return scored items in order, with rationales merged and stimulus inlined."""
    rationales = {r["item_id"]: r for r in data.get("rationales", [])}
    stim_html = {it.get("id"): it.get("prompt", "")
                 for it in data["items"] if it["type"] == "STIMULUS"}

    prepared = []
    for it in data["items"]:
        if it["type"] in ("STIMULUS", "STIMULUS_END"):
            continue
        it = dict(it)
        if it.get("type") == "FITB":
            tokens = re.findall(r"\[blank\d*\]", str(it.get("prompt") or ""))
            accept = it.get("accept")
            if len(tokens) <= 1 and isinstance(accept, list) and len(accept) == 1 and isinstance(accept[0], list):
                it["accept"] = accept[0]
        # inline stimulus by EXPLICIT id only (never implicit, to avoid
        # gluing a code block onto an unrelated generic question)
        sid = it.get("stimulus_id")
        if sid and sid in stim_html and it.get("prompt"):
            it["prompt"] = stim_html[sid] + it["prompt"]
        # VSCode-style highlighting for any code blocks (incl. inlined stimulus)
        if it.get("prompt"):
            it["prompt"] = codefmt.highlight_html_blocks(it["prompt"])
        # visible TEKS label on the question
        codes = teks.item_teks(it)
        if codes and it.get("prompt"):
            it["prompt"] = it["prompt"] + teks.label_html(codes)
        if it.get("id") in rationales:
            it["_rationale"] = rationales[it["id"]]
        prepared.append(it)
    return prepared


def _normalized_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("QF push settings must be an object")
    return {key: settings[key] for key in SETTING_KEYS if key in settings}


def _effective_quiz_settings(push_settings):
    quiz_settings = dict(QUIZ_SETTINGS)
    if "shuffle_answers" in push_settings:
        quiz_settings["shuffle_answers"] = bool(push_settings["shuffle_answers"])
    if "shuffle_questions" in push_settings:
        quiz_settings["shuffle_questions"] = bool(push_settings["shuffle_questions"])
    if push_settings.get("access_code"):
        quiz_settings["require_student_access_code"] = True
        quiz_settings["student_access_code"] = str(push_settings["access_code"]).strip()
    if push_settings.get("allow_multiple_attempts"):
        score = push_settings.get("score_to_keep", "highest")
        raw_attempts = push_settings.get("allowed_attempts", -1)
        attempt_count = -1 if str(raw_attempts) in ("-1", "unlimited") else int(raw_attempts)
        attempts = {
            "multiple_attempts_enabled": True,
            "score_to_keep": score if score in ("highest", "latest", "average", "first") else "highest",
            "build_on_last_attempt": bool(push_settings.get("build_on_last_attempt", False)),
        }
        if attempt_count > 0:
            attempts.update({"attempt_limit": True, "max_attempts": attempt_count})
        cooldown = int(push_settings.get("attempt_cooldown", 0) or 0)
        if cooldown > 0:
            attempts.update({"cooling_period": True, "cooling_period_seconds": cooldown * 60})
        quiz_settings["multiple_attempts"] = attempts
    if push_settings.get("has_time_limit"):
        minutes = int(push_settings.get("time_limit_minutes", 0))
        if minutes > 0:
            quiz_settings.update({
                "has_time_limit": True,
                "session_time_limit_in_seconds": minutes * 60,
            })
    if push_settings.get("calculator_type") in ("basic", "scientific"):
        quiz_settings["calculator_type"] = push_settings["calculator_type"]
    if push_settings.get("one_at_a_time"):
        quiz_settings.update({
            "one_at_a_time_type": "question",
            "allow_backtracking": bool(push_settings.get("allow_backtracking", True)),
        })
    if push_settings.get("hide_results"):
        quiz_settings["result_view_settings"] = {
            "result_view_restricted": True,
            "display_points_awarded": False,
            "display_points_possible": False,
            "display_items": False,
        }
    else:
        quiz_settings["result_view_settings"] = {
            "result_view_restricted": True,
            "display_points_awarded": True,
            "display_points_possible": True,
            "display_items": True,
            "display_item_response": True,
            "display_item_response_correctness": True,
            "display_item_response_qualifier": "after_last_attempt",
            "display_item_correct_answer": True,
            "display_item_feedback": True,
        }
    return quiz_settings


_CLASSIC_SCORING_POLICY = {"highest": "keep_highest", "latest": "keep_latest",
                           "average": "keep_average"}


def _classic_setting_problems(push_settings):
    """Push settings Classic Quizzes cannot honor, one sentence each."""
    problems = []
    if push_settings.get("calculator_type"):
        problems.append("calculator_type has no Classic Quiz equivalent; remove it.")
    if push_settings.get("shuffle_questions") is True:
        problems.append("shuffle_questions has no Classic Quiz equivalent; remove it.")
    if push_settings.get("build_on_last_attempt"):
        problems.append("build_on_last_attempt has no Classic Quiz equivalent; remove it.")
    if int(push_settings.get("attempt_cooldown", 0) or 0) > 0:
        problems.append("attempt_cooldown has no Classic Quiz equivalent; remove it.")
    if push_settings.get("score_to_keep") == "first":
        problems.append('score_to_keep "first" has no Classic Quiz equivalent; use highest, latest, or average.')
    return problems


def _classic_points(prepared):
    """Return explicit points for every scored Classic Quiz item."""
    return distribute_points(prepared)


def _classic_quiz_fields(data, title, push_settings, total):
    attempts = 1
    if push_settings.get("allow_multiple_attempts"):
        raw = push_settings.get("allowed_attempts", -1)
        attempts = -1 if str(raw) in ("-1", "unlimited") else int(raw)
    hidden = bool(push_settings.get("hide_results"))
    one_at_a_time = bool(push_settings.get("one_at_a_time"))
    quiz = {
        "title": title,
        "description": normalize_student_text(data.get("instructions") or ""),
        "quiz_type": "assignment",
        "published": False,
        "shuffle_answers": bool(push_settings.get("shuffle_answers", True)),
        "allowed_attempts": attempts,
        "scoring_policy": _CLASSIC_SCORING_POLICY.get(
            push_settings.get("score_to_keep", "highest"), "keep_highest"),
        "one_question_at_a_time": one_at_a_time,
        "cant_go_back": one_at_a_time and not bool(push_settings.get("allow_backtracking", True)),
        "show_correct_answers": not hidden,
        "points_possible_expected": total,
    }
    minutes = int(push_settings.get("time_limit_minutes", 0) or 0)
    if push_settings.get("has_time_limit") and minutes > 0:
        quiz["time_limit"] = minutes
    if push_settings.get("access_code"):
        quiz["access_code"] = str(push_settings["access_code"]).strip()
    if hidden:
        quiz["hide_results"] = "always"
    for key in ("due_at", "unlock_at", "lock_at"):
        if push_settings.get(key):
            quiz[key] = push_settings[key]
    return quiz


def _build_classic_plan(path, data, push_settings):
    problems = _classic_setting_problems(push_settings)
    if problems:
        raise ValueError(problems[0])
    title = normalize_student_text(data.get("title", os.path.basename(path))).strip()
    prepared = prepare_items(data)
    points = _classic_points(prepared)
    items = []
    for index, (qf_item, point) in enumerate(zip(prepared, points), 1):
        payload = transform_classic.build_question(qf_item, index)
        payload["question"]["points_possible"] = point
        items.append({
            "index": index,
            "source_item_id": qf_item.get("id"),
            "source_type": qf_item.get("type"),
            "payload": payload,
        })
    plan = {
        "version": 1,
        "quiz_engine": "classic",
        "title": title,
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        "source_path": str(path),
        "quiz_payload": {"quiz": _classic_quiz_fields(
            data, title, push_settings, authored_total(points))},
        "items": items,
        "assignment_settings": {
            key: push_settings[key]
            for key in ("published", "assignment_group_id", "assignment_group_name", "post_to_sis")
            if key in push_settings
        },
        "module": {
            key: push_settings[key] for key in ("module_id", "module_name")
            if push_settings.get(key) not in (None, "")
        },
    }
    if data.get("differentiation") == "hub":
        plan["hub"] = {"tiers": [{"label": tier["label"], "supports": tier["supports"]}
                                 for tier in data["tiers"]]}
    return plan


def build_push_plan(path, settings=None):
    """Build the deterministic, JSON-safe no-network QuizForge push plan."""
    push_settings = _normalized_settings(settings or {})
    data = load_qf(path)
    problems = validate_qf.engine_problems(data)
    if problems:
        raise ValueError(problems[0])
    if data.get("quiz_engine") == "classic":
        return _build_classic_plan(path, data, push_settings)
    _reject_writing_items(data)
    title = normalize_student_text(
        data.get("title", os.path.basename(path))
    ).strip()
    prepared = prepare_items(data)
    points = distribute_points(prepared)
    items = []
    for index, (qf_item, point) in enumerate(zip(prepared, points), 1):
        payload = transform.build_item(qf_item, index)
        payload["item"]["points_possible"] = point
        items.append({
            "index": index,
            "source_item_id": qf_item.get("id"),
            "source_type": qf_item.get("type"),
            "payload": payload,
        })
    quiz_points = authored_total(points)
    assignment_keys = (
        "due_at", "unlock_at", "lock_at", "assignment_group_id",
        "assignment_group_name", "post_to_sis", "published",
        "allow_multiple_attempts", "allowed_attempts",
    )
    return {
        "version": 1,
        "title": title,
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        "source_path": str(path),
        "quiz_payload": {"quiz": {
            "title": title,
            "points_possible": quiz_points,
            "grading_type": "points",
            "quiz_settings": _effective_quiz_settings(push_settings),
        }},
        "items": items,
        "assignment_settings": {
            key: push_settings[key] for key in assignment_keys if key in push_settings
        },
        "module": {
            key: push_settings[key] for key in ("module_id", "module_name")
            if push_settings.get(key) not in (None, "")
        },
    }
