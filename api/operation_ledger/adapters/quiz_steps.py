"""Focused write-ahead helpers for quiz operation execution."""

from __future__ import annotations

import re

from .. import models
from .adapter_support import build_result, ensure_step, is_uncertain, replace_step
from .module_placement import attach_assignment_type_module_item
from api.platform_services import canvas_client, config


_ITEM_FIELDS = (
    "answer_mode", "accept", "options", "choices", "matches",
    "categories", "distractors",
)


def _failed_item_details(
    *,
    item_payload: dict,
    source_item_id: object,
    source_type: object,
    plan_index: object,
    error: object,
) -> dict:
    """Build a small, PII-free explanation for a definitive item rejection."""
    def bounded(value: object) -> str:
        return " ".join(str(value).split())[:120]

    try:
        item_index = int(plan_index)
    except (TypeError, ValueError):
        item_index = 0
    if item_index < 1:
        item_index = 1

    item = item_payload.get("item") if isinstance(item_payload, dict) else {}
    item = item if isinstance(item, dict) else {}
    error_text = str(error or "").lower()
    present = [name for name in _ITEM_FIELDS if name in item]
    field = next(
        (name for name in _ITEM_FIELDS if name in item and name in error_text),
        None,
    )
    if field is None:
        field = present[0] if len(present) == 1 else "item"

    details = {"item_index": item_index, "field": field}
    if source_item_id is not None and bounded(source_item_id):
        details["id"] = bounded(source_item_id)
    if source_type is not None and bounded(source_type):
        details["source_type"] = bounded(source_type)
    status = re.search(r"\bHTTP\s+(\d{3})\b", str(error or ""), re.IGNORECASE)
    if status:
        details["canvas_status"] = int(status.group(1))
    details["reason"] = (
        f"Canvas rejected the {field} field for this quiz item."
        if field != "item" else "Canvas rejected this quiz item."
    )
    return details


def build_assignment_patch(assignment_settings: dict) -> dict:
    patch: dict = {}
    for key in ("due_at", "unlock_at", "lock_at"):
        value = assignment_settings.get(key)
        if value:
            patch[key] = str(value).strip()
    for key in (
        "post_to_sis", "published", "only_visible_to_overrides",
        "omit_from_final_grade",
    ):
        if key in assignment_settings:
            patch[key] = bool(assignment_settings[key])
    assignment_group_name = assignment_settings.get("assignment_group_name")
    if assignment_group_name:
        patch["assignment_group_name"] = str(assignment_group_name).strip()
    assignment_group_id = assignment_settings.get("assignment_group_id")
    if assignment_group_id:
        patch["assignment_group_id"] = int(assignment_group_id)
    return patch


def ensure_quiz(
    *,
    course_id: str,
    step_key: str,
    quiz_payload: dict,
    steps: list[dict],
    context,
    current_quiz_id: str | None,
    current_quiz_url: str | None,
    failure_state: str,
) -> tuple[str | None, str | None, dict | None]:
    quiz_step = ensure_step(steps, step_key)
    quiz_id = current_quiz_id or quiz_step.get("returned_object_id")
    quiz_url = current_quiz_url or quiz_step.get("returned_object_url")

    if quiz_step.get("state") in ("applied", "skipped") and quiz_id:
        quiz, error = canvas_client.canvas_get(f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}")
        if not error and quiz:
            quiz_step["state"] = "skipped"
            return quiz_id, quiz_url, None
        return quiz_id, quiz_url, build_result(
            "sent_unknown",
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code="quiz_exact_id_unverified",
        )

    if quiz_id:
        quiz, error = canvas_client.canvas_get(f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}")
        if not error and quiz:
            quiz_step["state"] = "skipped"
            return quiz_id, quiz_url, None
        if quiz_step.get("outbound_started_at"):
            return quiz_id, quiz_url, build_result(
                "sent_unknown",
                steps=steps,
                returned_object_id=quiz_id,
                returned_object_url=quiz_url,
                error_code="quiz_exact_id_unverified",
            )
        quiz_id = None

    if quiz_id is None:
        path_create = f"/api/quiz/v1/courses/{course_id}/quizzes"
        digest = models.sha256_dict({"method": "POST", "path": path_create, "payload": quiz_payload})
        quiz_step = context.before_send(step_key, digest)
        replace_step(steps, quiz_step)
        response, error = canvas_client._canvas_send("POST", path_create, quiz_payload)
        if error:
            state = "sent_unknown" if is_uncertain(error) else failure_state
            quiz_step["state"] = state if state == "sent_unknown" else "failed"
            quiz_step["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "canvas_rejected"
            quiz_step["private_diagnostic"] = error
            quiz_step = context.checkpoint_step(quiz_step)
            replace_step(steps, quiz_step)
            return None, None, build_result(
                state,
                steps=steps,
                error_code=quiz_step["error_code"],
                private_diagnostic=error,
            )
        quiz_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
        quiz_url = f"{config.get_canvas_base()}/courses/{course_id}/assignments/{quiz_id}" if quiz_id else None
        if not quiz_id:
            quiz_step["state"] = "sent_unknown"
            quiz_step["error_code"] = "unparseable_response"
            quiz_step["private_diagnostic"] = "missing quiz id"
            quiz_step = context.checkpoint_step(quiz_step)
            replace_step(steps, quiz_step)
            return None, None, build_result("sent_unknown", steps=steps, error_code="unparseable_response")
        quiz_step["state"] = "applied"
        quiz_step = context.checkpoint_step(
            quiz_step,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
        )
        replace_step(steps, quiz_step)

    return quiz_id, quiz_url, None


def restrict_assignment(
    *,
    course_id: str,
    quiz_id: str,
    quiz_url: str | None,
    step_key: str,
    steps: list[dict],
    context,
    failure_state: str,
) -> dict | None:
    restrict_step = ensure_step(steps, step_key)
    if restrict_step.get("state") in ("applied", "skipped"):
        restrict_step["state"] = "skipped"
        return None
    if restrict_step.get("outbound_started_at"):
        return build_result(
            "sent_unknown",
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code="restrict_unresolved",
        )

    restrict_payload = {"assignment": {"only_visible_to_overrides": True}}
    restrict_path = f"/api/v1/courses/{course_id}/assignments/{quiz_id}"
    digest = models.sha256_dict({"method": "PUT", "path": restrict_path, "payload": restrict_payload})
    restrict_step = context.before_send(step_key, digest)
    replace_step(steps, restrict_step)
    response, error = canvas_client._canvas_send("PUT", restrict_path, restrict_payload)
    if error:
        state = "sent_unknown" if is_uncertain(error) else failure_state
        restrict_step["state"] = state if state == "sent_unknown" else "failed"
        restrict_step["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "restrict_rejected"
        restrict_step["private_diagnostic"] = error
        restrict_step = context.checkpoint_step(restrict_step)
        replace_step(steps, restrict_step)
        return build_result(
            state,
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code=restrict_step["error_code"],
        )
    restrict_step["state"] = "applied"
    restrict_step = context.checkpoint_step(restrict_step)
    replace_step(steps, restrict_step)
    return None


def create_override(
    *,
    course_id: str,
    quiz_id: str,
    quiz_url: str | None,
    step_key: str,
    steps: list[dict],
    context,
    failure_state: str,
    override_request: dict,
    membership_digest: str,
    bucket_index: int,
    bucket_kind: str,
) -> dict | None:
    override_step = ensure_step(steps, step_key)
    override_id = override_step.get("returned_object_id")
    if override_id:
        existing, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{quiz_id}/overrides/{override_id}"
        )
        if error or not existing:
            return build_result(
                "sent_unknown",
                steps=steps,
                returned_object_id=quiz_id,
                returned_object_url=quiz_url,
                error_code="override_exact_id_unverified",
            )
        override_step["state"] = "skipped"
        return None
    if override_step.get("outbound_started_at"):
        return build_result(
            "sent_unknown",
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code="override_creation_unresolved",
        )

    override_path = f"/api/v1/courses/{course_id}/assignments/{quiz_id}/overrides"
    digest = models.sha256_dict(
        {
            "method": "POST",
            "path": override_path,
            "membership_digest": membership_digest,
            "bucket_index": bucket_index,
            "bucket_kind": bucket_kind,
        }
    )
    marked = context.before_send(step_key, digest)
    replace_step(steps, marked)
    response, error = canvas_client._canvas_send("POST", override_path, override_request)
    if error:
        state = "sent_unknown" if is_uncertain(error) else failure_state
        marked["state"] = state if state == "sent_unknown" else "failed"
        marked["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "override_rejected"
        marked["private_diagnostic"] = error
        marked = context.checkpoint_step(marked)
        replace_step(steps, marked)
        return build_result(
            state,
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code=marked["error_code"],
        )
    override_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
    if not override_id:
        marked["state"] = "sent_unknown"
        marked["error_code"] = "unparseable_response"
        marked["private_diagnostic"] = "missing override id"
        marked = context.checkpoint_step(marked)
        replace_step(steps, marked)
        return build_result(
            "sent_unknown",
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code="unparseable_response",
        )
    marked["state"] = "applied"
    marked = context.checkpoint_step(marked, returned_object_id=override_id)
    replace_step(steps, marked)
    return None


def ensure_item(
    *,
    course_id: str,
    quiz_id: str,
    quiz_url: str | None,
    step_key: str,
    item_payload: dict,
    source_item_id: object = None,
    source_type: object = None,
    plan_index: object = None,
    steps: list[dict],
    context,
    failure_state: str,
) -> dict | None:
    item_step = ensure_step(steps, step_key)
    item_id = item_step.get("returned_object_id")
    if item_step.get("state") in ("applied", "skipped") and item_id:
        verify, error = canvas_client.canvas_get(
            f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/items/{item_id}"
        )
        if not error and verify:
            item_step["state"] = "skipped"
            return None
        return build_result(
            "sent_unknown",
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code="item_exact_id_unverified",
        )
    if item_id:
        verify, error = canvas_client.canvas_get(
            f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/items/{item_id}"
        )
        if not error and verify:
            item_step["state"] = "skipped"
            return None
        if item_step.get("outbound_started_at"):
            return build_result(
                "sent_unknown",
                steps=steps,
                returned_object_id=quiz_id,
                returned_object_url=quiz_url,
                error_code="item_exact_id_unverified",
            )

    item_path = f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/items"
    digest = models.sha256_dict({"method": "POST", "path": item_path, "payload": item_payload})
    item_step = context.before_send(step_key, digest)
    replace_step(steps, item_step)
    response, error = canvas_client._canvas_send("POST", item_path, item_payload)
    if error:
        state = "sent_unknown" if is_uncertain(error) else failure_state
        item_step["state"] = state if state == "sent_unknown" else "failed"
        item_step["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "item_rejected"
        item_step["private_diagnostic"] = error
        item_step = context.checkpoint_step(item_step)
        replace_step(steps, item_step)
        return build_result(
            state,
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code=item_step["error_code"],
            private_diagnostic=error,
            failed_items=[_failed_item_details(
                item_payload=item_payload,
                source_item_id=source_item_id,
                source_type=source_type,
                plan_index=plan_index,
                error=error,
            )] if state != "sent_unknown" else None,
        )
    item_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
    if not item_id:
        item_step["state"] = "sent_unknown"
        item_step["error_code"] = "unparseable_response"
        item_step["private_diagnostic"] = "missing item id"
        item_step = context.checkpoint_step(item_step)
        replace_step(steps, item_step)
        return build_result(
            "sent_unknown",
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code="unparseable_response",
        )
    item_step["state"] = "applied"
    item_step = context.checkpoint_step(item_step, returned_object_id=item_id)
    replace_step(steps, item_step)
    return None


def rollback_quiz(
    *,
    course_id: str,
    quiz_id: str,
    step_key: str,
    steps: list[dict],
    context,
) -> dict:
    """Delete a quiz created by this attempt after a definitive item failure."""
    rollback_path = f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}"
    rollback_step = ensure_step(steps, step_key)
    if rollback_step.get("state") == "applied":
        return {"state": "applied"}
    if rollback_step.get("outbound_started_at"):
        return {"state": "sent_unknown", "error_code": "rollback_unknown"}

    digest = models.sha256_dict({"method": "DELETE", "path": rollback_path, "payload": {}})
    rollback_step = context.before_send(step_key, digest)
    replace_step(steps, rollback_step)
    _response, error = canvas_client._canvas_send("DELETE", rollback_path, {})
    if error:
        state = "sent_unknown" if is_uncertain(error) else "failed"
        rollback_step["state"] = state
        rollback_step["error_code"] = "rollback_unknown" if state == "sent_unknown" else "rollback_failed"
        rollback_step["private_diagnostic"] = error
        rollback_step = context.checkpoint_step(rollback_step)
        replace_step(steps, rollback_step)
        return {"state": state, "error_code": rollback_step["error_code"]}

    rollback_step["state"] = "applied"
    rollback_step = context.checkpoint_step(rollback_step)
    replace_step(steps, rollback_step)
    return {"state": "applied"}


def patch_assignment(
    *,
    course_id: str,
    quiz_id: str,
    quiz_url: str | None,
    step_key: str,
    assignment_settings: dict,
    steps: list[dict],
    context,
    failure_state: str,
) -> dict | None:
    patch_step = ensure_step(steps, step_key)
    patch_data = build_assignment_patch(assignment_settings)
    if not patch_data:
        return None
    if patch_step.get("state") in ("applied", "skipped"):
        patch_step["state"] = "skipped"
        return None

    patch_path = f"/api/v1/courses/{course_id}/assignments/{quiz_id}"
    request = {"assignment": patch_data}
    digest = models.sha256_dict({"method": "PUT", "path": patch_path, "payload": request})
    patch_step = context.before_send(step_key, digest)
    replace_step(steps, patch_step)
    response, error = canvas_client._canvas_send("PUT", patch_path, request)
    if error:
        state = "sent_unknown" if is_uncertain(error) else failure_state
        patch_step["state"] = state if state == "sent_unknown" else "failed"
        patch_step["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "assignment_patch_rejected"
        patch_step["private_diagnostic"] = error
        patch_step = context.checkpoint_step(patch_step)
        replace_step(steps, patch_step)
        return build_result(
            state,
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code=patch_step["error_code"],
            private_diagnostic=error,
        )
    verify, verify_error = canvas_client.canvas_get(f"/api/v1/courses/{course_id}/assignments/{quiz_id}")
    if verify_error or not verify:
        patch_step["state"] = "sent_unknown"
        patch_step["error_code"] = "assignment_verify_failed"
        patch_step["private_diagnostic"] = verify_error or "no response"
        patch_step = context.checkpoint_step(patch_step)
        replace_step(steps, patch_step)
        return build_result(
            "sent_unknown",
            steps=steps,
            returned_object_id=quiz_id,
            returned_object_url=quiz_url,
            error_code="assignment_verify_failed",
        )
    for key, expected in patch_data.items():
        if key == "assignment_group_name":
            continue
        actual = verify.get(key)
        if key == "assignment_group_id":
            matched = str(actual) == str(expected)
        else:
            matched = actual == expected
        if not matched:
            patch_step["state"] = "sent_unknown"
            patch_step["error_code"] = "assignment_verify_failed"
            patch_step["private_diagnostic"] = f"{key} postcondition mismatch"
            patch_step = context.checkpoint_step(patch_step)
            replace_step(steps, patch_step)
            return build_result(
                "sent_unknown", steps=steps, returned_object_id=quiz_id,
                returned_object_url=quiz_url,
                error_code="assignment_verify_failed",
            )
    patch_step["state"] = "applied"
    patch_step = context.checkpoint_step(patch_step)
    replace_step(steps, patch_step)
    return None


def attach_module(
    *,
    course_id: str,
    quiz_id: str,
    title: str,
    module_name: str,
    steps: list[dict],
    context,
    attach_step_key: str,
    failure_state: str,
) -> dict:
    return attach_assignment_type_module_item(
        course_id=course_id,
        content_id=quiz_id,
        title=title,
        module_name=module_name,
        steps=steps,
        context=context,
        attach_step_key=attach_step_key,
        returned_object_id=quiz_id,
        deterministic_failure_state=failure_state,
    )
