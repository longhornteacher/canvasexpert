"""Tiered assignment execution and reconciliation helpers."""

from __future__ import annotations

from .. import models
from .adapter_support import (
    build_result,
    ensure_step,
    find_step,
    is_uncertain as _is_uncertain,
)
from api.platform_services import canvas_client
from api.student_text import normalize_student_text


def execute(
    payload: dict,
    target: dict,
    baseline: dict,
    context,
    *,
    ordered_steps,
    find_assignment_group,
) -> dict:
    course_id = target["course_id"]
    tiers = payload["tiers"]
    steps = ordered_steps(target)
    for index, tier in enumerate(tiers):
        assignment_key = f"create_tier_assignment:{index}"
        assignment_step = ensure_step(steps, assignment_key)
        assignment_id = assignment_step.get("returned_object_id")
        assignment_url = assignment_step.get("returned_object_url")

        if assignment_id:
            existing, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            )
            if error or not existing:
                return build_result("sent_unknown", steps=steps, error_code="assignment_exact_id_unverified")
            assignment_step["state"] = "skipped"
            assignment_url = existing.get("html_url") or assignment_url
        elif assignment_step.get("outbound_started_at"):
            return build_result("sent_unknown", steps=steps, error_code="assignment_creation_unresolved")
        else:
            assignment_data = _assignment_data(payload, tier["title"], tier["description"], course_id, find_assignment_group)
            request = {"assignment": assignment_data}
            path = f"/api/v1/courses/{course_id}/assignments"
            marked = context.before_send(
                assignment_key,
                models.sha256_dict({"method": "POST", "path": path, "payload": request}),
            )
            _replace_local_step(steps, marked)
            response, error = canvas_client._canvas_send("POST", path, request)
            if error:
                state = "sent_unknown" if _is_uncertain(error) else _tier_failure_state(steps)
                marked["state"] = state if state == "sent_unknown" else "failed"
                marked["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "canvas_rejected"
                marked["private_diagnostic"] = type(error).__name__
                marked = context.checkpoint_step(marked)
                _replace_local_step(steps, marked)
                return build_result(state, steps=steps, error_code=marked["error_code"])
            assignment_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
            assignment_url = response.get("html_url") if isinstance(response, dict) else None
            if not assignment_id:
                marked["state"] = "sent_unknown"
                marked["error_code"] = "unparseable_response"
                marked["private_diagnostic"] = "missing assignment id"
                marked = context.checkpoint_step(marked)
                _replace_local_step(steps, marked)
                return build_result("sent_unknown", steps=steps, error_code="unparseable_response")
            marked["state"] = "applied"
            marked = context.checkpoint_step(
                marked,
                returned_object_id=assignment_id,
                returned_object_url=assignment_url,
            )
            _replace_local_step(steps, marked)

    return build_result(
        "applied",
        steps=steps,
        returned_object_id=(
            find_step(steps, "create_tier_assignment:0").get("returned_object_id")
            if tiers else None
        ),
        returned_object_url=(
            find_step(steps, "create_tier_assignment:0").get("returned_object_url")
            if tiers else None
        ),
    )


def reconcile(payload: dict, target: dict, *, ordered_steps) -> dict:
    course_id = target["course_id"]
    stored_steps = ordered_steps(target)
    projected = []
    for index, _tier in enumerate(payload.get("tiers") or []):
        assignment_step = find_step(stored_steps, f"create_tier_assignment:{index}")
        assignment_id = assignment_step.get("returned_object_id")
        if not assignment_id:
            return _tier_reconcile_unfinished(assignment_step, projected)
        assignment, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
        )
        if error or not assignment or str(assignment.get("id")) != str(assignment_id):
            return _tier_reconcile_result("sent_unknown", projected)
        projected.append(_applied_safe_step(assignment_step, returned_object_url=assignment.get("html_url")))

    first = find_step(stored_steps, "create_tier_assignment:0")
    return {
        "state": "applied",
        "steps": projected,
        "returned_object_id": first.get("returned_object_id") if first else None,
        "returned_object_url": first.get("returned_object_url") if first else None,
    }


def _tier_reconcile_unfinished(step: dict, projected: list[dict]) -> dict:
    state = "sent_unknown" if step.get("outbound_started_at") else "pending"
    return _tier_reconcile_result(state, projected)


def _tier_reconcile_result(state: str, steps: list[dict]) -> dict:
    return {
        "state": state,
        "returned_object_id": None,
        "returned_object_url": None,
        "steps": steps,
    }


def _applied_safe_step(step: dict, returned_object_url=None) -> dict:
    return {
        "step_key": step.get("step_key"),
        "state": "applied",
        "returned_object_id": step.get("returned_object_id"),
        "returned_object_url": returned_object_url or step.get("returned_object_url"),
        "error_code": None,
    }


def _assignment_data(
    payload: dict, title: str, description: str, course_id: str,
    find_assignment_group,
) -> dict:
    data = {
        "name": normalize_student_text(title),
        "submission_types": payload.get("submission_types", ["online_text_entry"]),
        "grading_type": "points",
        "only_visible_to_overrides": False,
        "omit_from_final_grade": bool(payload.get("omit_from_final_grade")),
        "post_to_sis": bool(payload.get("post_to_sis")),
        # Tier drafts are always left for the teacher to assign and publish.
        "published": False,
    }
    if description:
        data["description"] = normalize_student_text(description)
    if payload.get("points") is not None:
        data["points_possible"] = float(payload["points"])
    for key in ("allowed_extensions", "external_tool_tag_attributes"):
        if payload.get(key):
            data[key] = payload[key]
    for key in ("due_at", "unlock_at", "lock_at"):
        if payload.get(key):
            data[key] = payload[key]
    assignment_group_name = payload.get("assignment_group_name")
    if assignment_group_name:
        group_id = find_assignment_group(course_id, assignment_group_name)
        if group_id is not None:
            data["assignment_group_id"] = group_id
    return data


def _tier_failure_state(steps: list[dict]) -> str:
    return "partial" if any(
        step.get("state") in ("applied", "skipped") and step.get("returned_object_id")
        for step in steps
    ) else "failed"


def _replace_local_step(steps: list[dict], step: dict) -> None:
    for index, existing in enumerate(steps):
        if existing.get("step_key") == step.get("step_key"):
            steps[index] = step
            return
    steps.append(step)
