"""Classic Quiz delivery for the ``content.quiz`` kind: execute, reconcile, review.

A QuizForge file that declares ``"quiz_engine": "classic"`` becomes one whole-class Canvas
Classic Quiz, optionally with a Differentiated Hub of restricted tier pages linked from
its description. Steps checkpoint in this order: tier pages (Hub only), ``create_quiz:0``,
``create_question:0:<index>``, ``save_quiz:0``, ``patch_assignment:0``, ``attach_module:0``,
``publish_quiz:0``. Existence is always proven through the quiz's *assignment* id: a
deleted classic quiz still answers 200 on its own GET, but its assignment is gone.

Facts this module relies on: ``docs/reference/classic-quiz-design.md``, "Verified Canvas
facts: Classic Quiz REST".
"""

from __future__ import annotations

import math
from datetime import datetime

from .. import models
from . import differentiated_bridge, quiz_steps, tier_pages
from .adapter_support import (
    as_list,
    build_result,
    canvas_message_from_error,
    ensure_step,
    find_step,
    has_outbound_marker,
    is_uncertain,
    module_id_from_steps,
    normalize,
    replace_step,
)
from .module_placement import attach_assignment_type_module_item
from api.platform_services import canvas_client, config
from api.student_text import normalize_author_model
from engine.rendering.forge.canvas_html import render_tier_page, render_tier_pages_line

WRITING_TYPES = ("ESSAY", "FILEUPLOAD")
DIFFERENTIATED_REFUSAL = (
    "Classic quizzes differentiate with a Hub through preview_content_push, "
    "not preview_differentiated_quiz_push."
)
TEACHER_NOTE = "Canvas Expert cannot score classic quiz writing yet; grade it in SpeedGrader."

# Fields sent when the quiz is created; every other plan field is saved with one PUT,
# which is also what makes Canvas compute the quiz's points.
_CREATE_FIELDS = ("title", "description", "quiz_type", "published")
_NEVER_SAVED = (*_CREATE_FIELDS, "points_possible_expected")
_LOOKUP_WINDOW_SECONDS = 600

_TIER_STEP_ORDER = {"create_tier_page": 0, "restrict_tier_page": 1,
                    "assign_tier_page": 2, "clear_tier_page_assignment": 3}
_QUIZ_STEP_ORDER = {"create_quiz": 0, "create_question": 1, "save_quiz": 2,
                    "patch_assignment": 3, "create_module": 4, "attach_module": 5,
                    "publish_quiz": 6, "rollback_quiz": 7}


# ── Preparation (no Canvas call) ─────────────────────────────────────────


def prepare_hub(payload: dict) -> None:
    """Render the tier pages and the description's Supports line into a classic payload.

    Public tags and colors resolve here, in the reviewed payload, so a later Settings
    change cannot alter an apply. The quiz title becomes the unsuffixed base title.
    """
    plan = payload["plan"]
    authored = plan.pop("hub")["tiers"]
    base_title = differentiated_bridge.normalize_base_title(plan["title"])
    tags = differentiated_bridge.resolve_public_tags(
        [row["label"] for row in authored], minimum_tiers=1)
    colors = config.get_tier_colors()
    slots = [{"tag": resolved["tag"], "palette_key": colors[resolved["tier"]],
              "href": f"{{{{ce-tier-page:{index}}}}}"}
             for index, resolved in enumerate(tags)]
    quiz = plan["quiz_payload"]["quiz"]
    plan["title"] = quiz["title"] = base_title
    quiz["description"] = (str(quiz.get("description") or "")
                           + render_tier_pages_line(slots, palette_key=colors["untiered"]))
    tiers = []
    for index, (row, resolved) in enumerate(zip(authored, tags)):
        model = {"title": base_title,
                 "supports": normalize_author_model(row.get("supports"))}
        tiers.append({
            "label": resolved["tier"], **resolved,
            "title": differentiated_bridge.source_title(base_title, resolved["tag"]),
            "description": render_tier_page(
                model, palette_key=colors[resolved["tier"]],
                tier=resolved["tier"], public_tag=resolved["tag"]),
            "tag_status": "unavailable",
            "page_href_token": f"{{{{ce-tier-page:{index}}}}}",
        })
    payload.update({"hub": True, "tiers": tiers, "base_title": base_title})


def published_intent(payload: dict) -> bool:
    return bool(((payload.get("plan") or {}).get("assignment_settings") or {}).get("published"))


def ordered_steps(target: dict) -> list[dict]:
    existing = {step.get("step_key"): step for step in target.get("steps", [])}

    def order(step):
        key = str(step.get("step_key") or "")
        prefix, _, suffix = key.partition(":")
        tail = suffix.rsplit(":", 1)[-1]
        index = int(tail) if tail.isdigit() else 0
        if prefix in _TIER_STEP_ORDER:
            return (0, index, _TIER_STEP_ORDER[prefix])
        if prefix in ("publish_tier_page", "unpublish_tier_page"):
            return (1, index, 1 if prefix == "unpublish_tier_page" else 0)
        return (2, _QUIZ_STEP_ORDER.get(prefix, 9), index)

    return sorted(existing.values(), key=order)


# ── Review ───────────────────────────────────────────────────────────────


def freeze_review(payload: dict, target: dict, baseline: dict, course_name: str) -> dict:
    plan = payload.get("plan", {})
    quiz = plan.get("quiz_payload", {}).get("quiz", {})
    settings = plan.get("assignment_settings", {})
    module = plan.get("module", {})
    item_types: dict[str, int] = {}
    for item in plan.get("items", []):
        kind = str(item.get("source_type") or "unknown")
        item_types[kind] = item_types.get(kind, 0) + 1
    attempts = quiz.get("allowed_attempts", 1)
    multiple = attempts != 1
    review: dict = {
        "course_name": course_name,
        "title": plan.get("title"),
        "mode": "whole",
        "quiz_engine": "classic",
        "item_count": len(plan.get("items", [])),
        "item_types": item_types,
        "writing_item_count": sum(item_types.get(kind, 0) for kind in WRITING_TYPES),
        "total_points": quiz.get("points_possible_expected"),
        "due_at": quiz.get("due_at"),
        "unlock_at": quiz.get("unlock_at"),
        "lock_at": quiz.get("lock_at"),
        "published": settings.get("published", False),
        "post_to_sis": settings.get("post_to_sis", False),
        "assignment_group_name": settings.get("assignment_group_name"),
        "module_name": module.get("module_name"),
        "shuffle_answers": quiz.get("shuffle_answers"),
        "access_code": bool(quiz.get("access_code")),
        "multiple_attempts": multiple,
        "score_to_keep": str(quiz.get("scoring_policy", "")).removeprefix("keep_") if multiple else None,
        "allowed_attempts": attempts if attempts > 0 else None,
        "time_limit_minutes": quiz.get("time_limit"),
        "one_at_a_time": bool(quiz.get("one_question_at_a_time")),
        "allow_backtracking": not quiz.get("cant_go_back"),
        "hide_results": quiz.get("hide_results") == "always",
        "teacher_note": TEACHER_NOTE,
    }
    existing = baseline.get("existing_quiz")
    review["baseline_has_existing"] = existing is not None
    review["baseline_existing_id"] = existing["id"] if existing else None
    review["baseline_existing_url"] = existing["html_url"] if existing else None
    review["baseline_existing_new_quizzes"] = existing["new_quizzes"] if existing else None
    if payload.get("hub"):
        review["hub"] = tier_pages.review_hub(
            payload.get("tiers"), settings.get("published"), target.get("steps", []),
            plan.get("title"))
    return review


# ── Checkpointed writes ──────────────────────────────────────────────────


def _rejection_is_definitive(error) -> bool:
    """A 4xx (or a request that never left) proves nothing was written; anything else may have been."""
    text = str(error or "")
    return not (is_uncertain(text) or text.startswith("HTTP 5"))


def _send(context, steps, key, method, path, payload, *, rejected_code, identity):
    """One write-ahead-marked Canvas write. Returns ``(response, failure_result)``."""
    digest = models.sha256_dict({"method": method, "path": path, "payload": payload})
    marked = context.before_send(key, digest)
    replace_step(steps, marked)
    response, error = canvas_client._canvas_send(method, path, payload)
    if not error:
        return response, None
    definitive = _rejection_is_definitive(error)
    marked["state"] = "failed" if definitive else "sent_unknown"
    marked["error_code"] = rejected_code if definitive else "timeout_or_disconnect"
    marked["private_diagnostic"] = error
    marked = context.checkpoint_step(marked)
    replace_step(steps, marked)
    return None, build_result(
        marked["state"], steps=steps, error_code=marked["error_code"],
        private_diagnostic=error, canvas_message=canvas_message_from_error(error),
        **identity,
    )


def _quiz_path(course_id, quiz_id) -> str:
    return f"/api/v1/courses/{course_id}/quizzes/{quiz_id}"


def _quiz_url(course_id, quiz_id) -> str:
    return f"{config.get_canvas_base()}/courses/{course_id}/quizzes/{quiz_id}"


def _get_assignment(course_id, assignment_id):
    assignment, error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
    return (assignment, None) if not error and isinstance(assignment, dict) else (None, error or "no response")


def _verified_assignment_id(course_id, quiz_id, recorded_assignment_id):
    """The quiz's assignment id once that assignment answers, else ``None``."""
    assignment_id = recorded_assignment_id
    if not assignment_id:
        quiz, error = canvas_client.canvas_get(_quiz_path(course_id, quiz_id))
        if error or not isinstance(quiz, dict) or quiz.get("assignment_id") is None:
            return None
        assignment_id = str(quiz["assignment_id"])
    assignment, _error = _get_assignment(course_id, assignment_id)
    return str(assignment_id) if assignment is not None else None


def _in_window(rows, step):
    """Narrow same-title rows to those created near the unresolved write, when Canvas dates them."""
    timed = [row for row in rows if row.get("created_at")]
    if not timed or not step.get("outbound_started_at"):
        return rows
    try:
        started = datetime.fromisoformat(str(step["outbound_started_at"]).replace("Z", "+00:00"))
        return [row for row in timed if abs(
            (datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")) - started
             ).total_seconds()) <= _LOOKUP_WINDOW_SECONDS]
    except (ValueError, TypeError):
        return rows


def _create_body(plan: dict, description: str) -> dict:
    quiz = plan["quiz_payload"]["quiz"]
    body = {key: quiz[key] for key in _CREATE_FIELDS if key in quiz}
    body["description"] = description
    body["only_visible_to_overrides"] = False
    return {"quiz": body}


def _ensure_quiz(course_id, plan, description, target, steps, context):
    """Create the unpublished quiz once. Returns ``(quiz_id, assignment_id, url, failure)``."""
    step = ensure_step(steps, "create_quiz:0")
    quiz_id = target.get("returned_object_id") or step.get("returned_object_id")
    url = target.get("returned_object_url") or step.get("returned_object_url")
    if quiz_id:
        assignment_id = _verified_assignment_id(course_id, quiz_id, step.get("assignment_id"))
        if assignment_id:
            step["state"] = "skipped"
            return str(quiz_id), assignment_id, url or _quiz_url(course_id, quiz_id), None
        if step.get("state") in ("applied", "skipped") or step.get("outbound_started_at"):
            return None, None, None, build_result(
                "sent_unknown", steps=steps, returned_object_id=quiz_id, returned_object_url=url,
                error_code="quiz_exact_id_unverified")
    identity = {}
    if step.get("outbound_started_at"):
        adopted, failure = _adopt_created_quiz(course_id, plan, step, steps, context)
        if failure:
            return None, None, None, failure
        if adopted:
            return adopted
    path = f"/api/v1/courses/{course_id}/quizzes"
    response, failure = _send(context, steps, "create_quiz:0", "POST", path,
                              _create_body(plan, description),
                              rejected_code="canvas_rejected", identity=identity)
    if failure:
        return None, None, None, failure
    quiz_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
    assignment_id = (str(response["assignment_id"])
                     if isinstance(response, dict) and response.get("assignment_id") is not None else None)
    step = find_step(steps, "create_quiz:0")
    if not quiz_id or not assignment_id:
        step["state"] = "sent_unknown"
        step["error_code"] = "unparseable_response"
        step["private_diagnostic"] = "missing quiz or assignment id"
        replace_step(steps, context.checkpoint_step(step))
        return None, None, None, build_result(
            "sent_unknown", steps=steps, error_code="unparseable_response")
    return _checkpoint_created(context, steps, step, course_id, quiz_id, assignment_id)


def _checkpoint_created(context, steps, step, course_id, quiz_id, assignment_id):
    url = _quiz_url(course_id, quiz_id)
    step.update({"state": "applied", "quiz_id": quiz_id, "assignment_id": assignment_id})
    replace_step(steps, context.checkpoint_step(
        step, returned_object_id=quiz_id, returned_object_url=url))
    return quiz_id, assignment_id, url, None


def _adopt_created_quiz(course_id, plan, step, steps, context):
    """After an unknown-outcome create: adopt the one exact-title quiz made in the window.

    Returns ``(adopted_tuple | None, failure | None)``; ``(None, None)`` means create again.
    """
    title = plan["title"]
    rows, error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/quizzes", {"per_page": 100, "search_term": title})
    if error:
        return None, build_result("sent_unknown", steps=steps, error_code="quiz_lookup_unverified")
    exact = _in_window([row for row in rows or [] if normalize(row.get("title")) == normalize(title)], step)
    if len(exact) > 1:
        return None, build_result("blocked", steps=steps, error_code="duplicate_suspected")
    if len(exact) == 1 and exact[0].get("id") is not None:
        quiz_id = str(exact[0]["id"])
        quiz, read_error = canvas_client.canvas_get(_quiz_path(course_id, quiz_id))
        if read_error or not isinstance(quiz, dict) or quiz.get("assignment_id") is None:
            return None, build_result("sent_unknown", steps=steps, error_code="quiz_lookup_unverified")
        return _checkpoint_created(context, steps, step, course_id, quiz_id, str(quiz["assignment_id"])), None
    if step.get("retry_attempted"):
        return None, build_result("failed", steps=steps, error_code="quiz_create_retry_failed")
    step["retry_attempted"] = True
    replace_step(steps, context.checkpoint_step(step))
    return None, None


def _ensure_question(course_id, quiz_id, item, steps, context, identity):
    key = f"create_question:0:{item.get('index', 0)}"
    step = ensure_step(steps, key)
    path = f"{_quiz_path(course_id, quiz_id)}/questions"
    question_id = step.get("returned_object_id")
    payload = item.get("payload", {})
    if question_id:
        found, error = canvas_client.canvas_get(f"{path}/{question_id}")
        if not error and isinstance(found, dict) and str(found.get("id")) == str(question_id):
            step["state"] = "skipped"
            return None
        return build_result("sent_unknown", steps=steps, error_code="question_exact_id_unverified", **identity)
    if step.get("outbound_started_at"):
        adopted, failure = _adopt_created_question(path, payload, key, step, steps, context, identity)
        if failure or adopted:
            return failure
    response, failure = _send(context, steps, key, "POST", path, payload,
                              rejected_code="question_rejected", identity=identity)
    if failure:
        if failure["error_code"] == "question_rejected":
            failure["failed_items"] = [quiz_steps._failed_item_details(
                item_payload={"item": payload.get("question") or {}},
                source_item_id=item.get("source_item_id"), source_type=item.get("source_type"),
                plan_index=item.get("index"), error=failure.get("private_diagnostic"))]
        return failure
    question_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
    step = find_step(steps, key)
    if not question_id:
        step["state"] = "sent_unknown"
        step["error_code"] = "unparseable_response"
        step["private_diagnostic"] = "missing question id"
        replace_step(steps, context.checkpoint_step(step))
        return build_result("sent_unknown", steps=steps, error_code="unparseable_response", **identity)
    step["state"] = "applied"
    replace_step(steps, context.checkpoint_step(step, returned_object_id=question_id))
    return None


def _adopt_created_question(path, payload, key, step, steps, context, identity):
    """After an unknown-outcome question POST: adopt the one unclaimed question it must have made.

    Returns ``(adopted, failure)``; ``(False, None)`` means the write never landed.
    """
    rows, error = canvas_client.canvas_get_all(path, {"per_page": 100})
    if error:
        return False, build_result("sent_unknown", steps=steps, error_code="question_lookup_unverified", **identity)
    claimed = {str(other.get("returned_object_id")) for other in steps
               if str(other.get("step_key", "")).startswith("create_question:") and other.get("returned_object_id")}
    question = payload.get("question") or {}
    matches = [row for row in rows or []
               if str(row.get("id")) not in claimed
               and row.get("question_name") == question.get("question_name")
               and row.get("question_type") == question.get("question_type")]
    if len(matches) > 1:
        return False, build_result("sent_unknown", steps=steps, error_code="question_lookup_ambiguous", **identity)
    if not matches:
        return False, None
    step.update({"state": "applied"})
    replace_step(steps, context.checkpoint_step(step, returned_object_id=str(matches[0]["id"])))
    return True, None


def _rollback_quiz(course_id, quiz_id, steps, context, identity) -> dict:
    """Delete a quiz created by this run after a definitive question rejection."""
    step = ensure_step(steps, "rollback_quiz:0")
    if step.get("state") == "applied":
        return {"state": "applied"}
    if step.get("outbound_started_at"):
        return {"state": "sent_unknown", "error_code": "rollback_unknown"}
    _response, failure = _send(context, steps, "rollback_quiz:0", "DELETE", _quiz_path(course_id, quiz_id),
                               {}, rejected_code="rollback_failed", identity=identity)
    if failure:
        return {"state": failure["state"],
                "error_code": "rollback_unknown" if failure["state"] == "sent_unknown" else "rollback_failed"}
    step = find_step(steps, "rollback_quiz:0")
    step["state"] = "applied"
    replace_step(steps, context.checkpoint_step(step))
    return {"state": "applied"}


def _points_match(quiz, expected) -> bool:
    try:
        return math.isclose(float(quiz.get("points_possible")), float(expected), abs_tol=0.01)
    except (TypeError, ValueError):
        return False


def _save_quiz(course_id, quiz_id, plan, steps, context, identity):
    """PUT the settings (always: it is what computes points), then require the expected total."""
    step = ensure_step(steps, "save_quiz:0")
    quiz = plan["quiz_payload"]["quiz"]
    if step.get("state") not in ("applied", "skipped"):
        settings = {key: value for key, value in quiz.items() if key not in _NEVER_SAVED}
        _response, failure = _send(context, steps, "save_quiz:0", "PUT", _quiz_path(course_id, quiz_id),
                                   {"quiz": settings}, rejected_code="quiz_settings_rejected",
                                   identity=identity)
        if failure:
            return failure
    live, error = canvas_client.canvas_get(_quiz_path(course_id, quiz_id))
    step = find_step(steps, "save_quiz:0")
    if error or not isinstance(live, dict) or not _points_match(live, quiz.get("points_possible_expected")):
        step["state"] = "sent_unknown"
        step["error_code"] = "quiz_points_unverified"
        step["private_diagnostic"] = "points_possible postcondition mismatch"
        replace_step(steps, context.checkpoint_step(step))
        return build_result("sent_unknown", steps=steps, error_code="quiz_points_unverified", **identity)
    step["state"] = "skipped" if step.get("state") in ("applied", "skipped") else "applied"
    replace_step(steps, context.checkpoint_step(step))
    return None


def _resolve_group(course_id, settings, steps, identity):
    """Assignment-group id for the patch: explicit id, else exact name lookup. ``(id, failure)``."""
    if settings.get("assignment_group_id"):
        return int(settings["assignment_group_id"]), None
    name = settings.get("assignment_group_name")
    if not name:
        return None, None
    groups, error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/assignment_groups", {"per_page": 100})
    if error:
        return None, build_result("sent_unknown", steps=steps, error_code="assignment_group_lookup_failed", **identity)
    for group in groups or []:
        if normalize(group.get("name")) == normalize(name) and group.get("id") is not None:
            return int(group["id"]), None
    return None, build_result("blocked", steps=steps, error_code="assignment_group_not_found", **identity)


def _assignment_patch(settings: dict, group_id) -> dict:
    patch = {}
    if "post_to_sis" in settings:
        patch["post_to_sis"] = bool(settings["post_to_sis"])
    if group_id:
        patch["assignment_group_id"] = group_id
    return patch


def _publish(course_id, quiz_id, steps, context, identity):
    step = ensure_step(steps, "publish_quiz:0")
    live, error = canvas_client.canvas_get(_quiz_path(course_id, quiz_id))
    if not error and isinstance(live, dict) and live.get("published") is True:
        step["state"] = "skipped"
        return None
    _response, failure = _send(context, steps, "publish_quiz:0", "PUT", _quiz_path(course_id, quiz_id),
                               {"quiz": {"published": True}}, rejected_code="quiz_publish_rejected",
                               identity=identity)
    if failure:
        return failure
    live, error = canvas_client.canvas_get(_quiz_path(course_id, quiz_id))
    step = find_step(steps, "publish_quiz:0")
    if error or not isinstance(live, dict) or live.get("published") is not True:
        step["state"] = "sent_unknown"
        step["error_code"] = "quiz_publish_unverified"
        replace_step(steps, context.checkpoint_step(step))
        return build_result("sent_unknown", steps=steps, error_code="quiz_publish_unverified", **identity)
    step["state"] = "applied"
    replace_step(steps, context.checkpoint_step(step))
    return None


# ── Execute ──────────────────────────────────────────────────────────────


def execute(payload: dict, target: dict, context) -> dict:
    steps = ordered_steps(target)
    result = _run(payload, target, context, steps)
    result["steps"] = ordered_steps({"steps": result.get("steps") or steps})
    return result


def _run(payload, target, context, steps) -> dict:
    course_id = target["course_id"]
    plan = payload["plan"]
    quiz = plan["quiz_payload"]["quiz"]
    settings = plan.get("assignment_settings") or {}
    title = plan.get("title", "Untitled quiz")
    description = str(quiz.get("description") or "")

    group_id, failure = _resolve_group(course_id, settings, steps, {})
    if failure:
        return failure

    pages = []
    if payload.get("hub"):
        pages, failure = tier_pages.run_pages(
            course_id, payload.get("tiers"), published_intent(payload), steps, context)
        if failure:
            return failure
        description, failure = tier_pages.bind_links(description, pages, course_id, steps)
        if failure:
            return failure

    before = find_step(steps, "create_quiz:0")
    created_here = (not target.get("returned_object_id") and not before.get("returned_object_id")
                    and not before.get("outbound_started_at"))
    quiz_id, assignment_id, quiz_url, failure = _ensure_quiz(
        course_id, plan, description, target, steps, context)
    if failure:
        return failure
    identity = {"returned_object_id": quiz_id, "returned_object_url": quiz_url}

    for item in plan.get("items", []):
        failure = _ensure_question(course_id, quiz_id, item, steps, context, identity)
        if failure:
            if failure.get("error_code") == "question_rejected" and created_here:
                rollback = _rollback_quiz(course_id, quiz_id, steps, context, identity)
                failure["rollback_state"] = rollback["state"]
                failure["cleanup_required"] = rollback["state"] != "applied"
                if rollback.get("error_code"):
                    failure["rollback_error_code"] = rollback["error_code"]
            return failure

    failure = _save_quiz(course_id, quiz_id, plan, steps, context, identity)
    if failure:
        return failure

    patch = _assignment_patch(settings, group_id)
    if patch:
        # The patch addresses the quiz's assignment; every result still names the quiz.
        failure = quiz_steps.patch_assignment(
            course_id=course_id, quiz_id=assignment_id, quiz_url=quiz_url,
            step_key="patch_assignment:0", assignment_settings=patch,
            steps=steps, context=context, failure_state="failed")
        if failure:
            failure.update(identity)
            return failure

    module = plan.get("module") or {}
    if module.get("module_name") or module.get("module_id"):
        result = attach_assignment_type_module_item(
            course_id=course_id, content_id=quiz_id, title=title,
            module_name=module.get("module_name") or "", module_id=module.get("module_id"),
            steps=steps, context=context, attach_step_key="attach_module:0",
            returned_object_id=quiz_id, deterministic_failure_state="failed", item_type="Quiz")
        if result.get("state") != "applied":
            result["returned_object_url"] = quiz_url
            return result

    if settings.get("published"):
        failure = _publish(course_id, quiz_id, steps, context, identity)
        if failure:
            return failure

    if payload.get("hub"):
        live, error = canvas_client.canvas_get(_quiz_path(course_id, quiz_id))
        if error or not isinstance(live, dict) or not tier_pages.links_present(
                live.get("description"), pages, course_id):
            return build_result("sent_unknown", steps=steps, error_code="hub_links_unverified", **identity)

    return build_result("applied", steps=steps, **identity)


# ── Reconcile ────────────────────────────────────────────────────────────


def reconcile(payload: dict, target: dict) -> dict:
    """Judge only what is checkpointed: exact ids, never title inference once an id exists."""
    course_id = target["course_id"]
    steps = ordered_steps(target)
    plan = payload.get("plan", {})
    settings = plan.get("assignment_settings") or {}
    if payload.get("hub"):
        unsettled = tier_pages.reconcile_pages(
            course_id, payload.get("tiers"), published_intent(payload), steps)
        if unsettled is not None:
            return unsettled

    quiz_step = find_step(steps, "create_quiz:0")
    quiz_id = target.get("returned_object_id") or quiz_step.get("returned_object_id")
    assignment_id = quiz_step.get("assignment_id")
    has_marker = has_outbound_marker(steps)
    if not quiz_id:
        title = plan.get("title", "")
        rows, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments", params={"per_page": 100, "search_term": title})
        if error or any(normalize(row.get("name")) == normalize(title) for row in as_list(rows)):
            return {"state": "sent_unknown"}
        return {"state": "sent_unknown" if has_marker else "pending"}
    if not assignment_id:
        return {"state": "sent_unknown"}

    assignment, error = _get_assignment(course_id, assignment_id)
    if assignment is None:
        if "404" in str(error) and not quiz_step.get("outbound_started_at"):
            return {"state": "pending"}
        return {"state": "sent_unknown"}
    unresolved = {"state": "sent_unknown", "returned_object_id": quiz_id}

    live, error = canvas_client.canvas_get(_quiz_path(course_id, quiz_id))
    if error or not isinstance(live, dict):
        return unresolved
    questions, error = canvas_client.canvas_get_all(f"{_quiz_path(course_id, quiz_id)}/questions", {"per_page": 100})
    if error:
        return unresolved
    expected_ids = []
    for item in plan.get("items", []):
        question_id = find_step(steps, f"create_question:0:{item.get('index', 0)}").get("returned_object_id")
        if not question_id:
            return {"state": "sent_unknown" if has_marker else "pending", "returned_object_id": quiz_id}
        expected_ids.append(str(question_id))
    if sorted(str(row.get("id")) for row in questions or []) != sorted(expected_ids):
        return unresolved
    quiz = plan.get("quiz_payload", {}).get("quiz", {})
    if not _points_match(live, quiz.get("points_possible_expected")):
        return unresolved
    if settings.get("published") and live.get("published") is not True:
        return unresolved
    if "post_to_sis" in settings and assignment.get("post_to_sis") is not bool(settings["post_to_sis"]):
        return unresolved
    if settings.get("assignment_group_id") or settings.get("assignment_group_name"):
        group_id, failure = _resolve_group(course_id, settings, steps, {})
        if failure or str(assignment.get("assignment_group_id")) != str(group_id):
            return unresolved
    if payload.get("hub") and not tier_pages.slug_links_present(
            live.get("description"), course_id, payload.get("tiers"), steps):
        return unresolved

    result = {"state": "applied", "returned_object_id": quiz_id,
              "returned_object_url": _quiz_url(course_id, quiz_id)}
    module = plan.get("module") or {}
    if not (module.get("module_name") or module.get("module_id")):
        return result
    return _reconcile_module(course_id, quiz_id, steps, has_marker, result)


def _module_item_matches(item, quiz_id) -> bool:
    return str(item.get("type", "")).lower() == "quiz" and str(item.get("content_id")) == str(quiz_id)


def _reconcile_module(course_id, quiz_id, steps, has_marker, result) -> dict:
    attach_step = find_step(steps, "attach_module:0")
    module_id = module_id_from_steps(find_step(steps, "create_module"), attach_step)
    if not module_id:
        return {"state": "sent_unknown" if has_marker else "pending", "returned_object_id": quiz_id}
    item_id = attach_step.get("returned_object_id")
    if item_id:
        item, error = canvas_client.canvas_get(f"/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}")
        if not error and isinstance(item, dict) and _module_item_matches(item, quiz_id):
            return {**result, "module_item_id": item_id}
    items, error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/modules/{module_id}/items", {"per_page": 100})
    if error:
        return {"state": "sent_unknown", "returned_object_id": quiz_id}
    matches = [row for row in items or [] if _module_item_matches(row, quiz_id)]
    if len(matches) == 1 and matches[0].get("id") is not None:
        return {**result, "module_item_id": str(matches[0]["id"])}
    if attach_step.get("outbound_started_at") or has_marker:
        return {"state": "sent_unknown", "returned_object_id": quiz_id}
    return {"state": "pending", "returned_object_id": quiz_id}
