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
from .module_placement import attach_assignment_type_module_item
from .assignment_groups import GroupResolutionError, resolve_assignment_groups
from . import differentiated_bridge


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
    try:
        resolved = resolve_assignment_groups(
            course_id,
            [{"label": row["label"], "group": row["group_name"]} for row in tiers],
        )
    except GroupResolutionError:
        return build_result("failed", steps=steps, error_code="group_resolution_failed")
    if resolved.get("safe") != payload.get("group_snapshot"):
        return build_result("failed", steps=steps, error_code="group_membership_drift")
    transient_ids = resolved.get("student_ids_by_group") or {}
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
            # A returned ID is not a postcondition. Re-read the exact
            # assignment before any override or publication call.
            verified, verify_error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            )
            if verify_error or not verified or not _source_shape_matches(verified, assignment_data, published=False):
                marked["state"] = "sent_unknown"
                marked["error_code"] = "assignment_create_unverified"
                marked["private_diagnostic"] = verify_error or "assignment postcondition mismatch"
                marked = context.checkpoint_step(marked)
                _replace_local_step(steps, marked)
                return build_result("sent_unknown", steps=steps, error_code=marked["error_code"])
            marked["state"] = "applied"
            marked = context.checkpoint_step(
                marked,
                returned_object_id=assignment_id,
                returned_object_url=assignment_url,
            )
            _replace_local_step(steps, marked)

        result = _restrict_assignment(
            course_id=course_id, assignment_id=str(assignment_id), tier=tier,
            payload=payload, steps=steps, context=context,
            step_key=f"restrict_assignment:{index}",
            failure_state=_tier_failure_state(steps),
        )
        if result is not None:
            return result
        result = _create_group_override(
            course_id=course_id, assignment_id=str(assignment_id), tier=tier,
            payload=payload, transient_ids=transient_ids, steps=steps, context=context,
            step_key=f"create_override:{index}",
            failure_state=_tier_failure_state(steps),
        )
        if result is not None:
            return result
        result = _publish_assignment(
            course_id=course_id, assignment_id=str(assignment_id), payload=payload,
            steps=steps, context=context, step_key=f"publish_assignment:{index}",
            failure_state=_tier_failure_state(steps),
        )
        if result is not None:
            return result

    return differentiated_bridge.execute_family_tail(
        course_id=course_id,
        payload=payload,
        source_ids=[str(find_step(steps, f"create_tier_assignment:{index}").get("returned_object_id"))
                    for index, _tier in enumerate(tiers)],
        source_titles=[tier["title"] for tier in tiers],
        steps=steps,
        context=context,
        failure_state="partial",
    )


def reconcile(payload: dict, target: dict, *, ordered_steps) -> dict:
    course_id = target["course_id"]
    stored_steps = ordered_steps(target)
    projected = []
    source_ids = []
    source_titles = []
    for index, tier in enumerate(payload.get("tiers") or []):
        assignment_step = find_step(stored_steps, f"create_tier_assignment:{index}")
        assignment_id = assignment_step.get("returned_object_id")
        if not assignment_id:
            return _tier_reconcile_unfinished(assignment_step, projected)
        assignment, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
        )
        if error or not assignment or str(assignment.get("id")) != str(assignment_id):
            return _tier_reconcile_result("sent_unknown", projected)
        source_ids.append(str(assignment_id))
        source_titles.append(str(tier.get("title") or assignment.get("name") or ""))
        projected.append(_applied_safe_step(assignment_step, returned_object_url=assignment.get("html_url")))
        for key in (f"restrict_assignment:{index}", f"create_override:{index}", f"publish_assignment:{index}"):
            step = find_step(stored_steps, key)
            if step.get("state") not in {"applied", "skipped"}:
                return _tier_reconcile_unfinished(step, projected)
            projected.append(_applied_safe_step(step))
    return differentiated_bridge.reconcile_family_tail(
        course_id=course_id, payload=payload, source_ids=source_ids,
        source_titles=source_titles, stored_steps=stored_steps, projected=projected,
    )


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
        "only_visible_to_overrides": True,
        "omit_from_final_grade": True,
        "post_to_sis": False,
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
    if payload.get("assignment_group_id") is not None:
        data["assignment_group_id"] = payload["assignment_group_id"]
    else:
        assignment_group_name = payload.get("assignment_group_name")
        if assignment_group_name:
            group_id = find_assignment_group(course_id, assignment_group_name)
            if group_id is not None:
                data["assignment_group_id"] = group_id
    return data


def _source_shape_matches(actual: dict, expected: dict, *, published: bool) -> bool:
    for key in (
        "name", "description", "submission_types", "grading_type",
        "only_visible_to_overrides", "omit_from_final_grade", "post_to_sis",
    ):
        if key in expected and actual.get(key) != expected[key]:
            return False
    if "points_possible" in expected and float(actual.get("points_possible", 0)) != float(expected["points_possible"]):
        return False
    if "assignment_group_id" in expected and str(actual.get("assignment_group_id")) != str(expected["assignment_group_id"]):
        return False
    if published and actual.get("published") is not True:
        return False
    return actual.get("published") is published


def _restrict_assignment(*, course_id, assignment_id, tier, payload, steps, context, step_key, failure_state):
    step = ensure_step(steps, step_key)
    request = {"assignment": {
        "only_visible_to_overrides": True,
        "omit_from_final_grade": True,
        "post_to_sis": False,
        "published": False,
    }}
    return _put_and_verify(
        course_id=course_id, assignment_id=assignment_id, request=request,
        steps=steps, context=context, step_key=step_key,
        failure_state=failure_state, verify_published=False,
        error_prefix="restrict_assignment",
    )


def _create_group_override(*, course_id, assignment_id, tier, payload, transient_ids, steps, context, step_key, failure_state):
    step = ensure_step(steps, step_key)
    override_id = step.get("returned_object_id")
    if override_id:
        existing, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides/{override_id}"
        )
        if error or not existing:
            return build_result("sent_unknown", steps=steps, returned_object_id=assignment_id, error_code="override_exact_id_unverified")
        step["state"] = "skipped"
        return None
    if step.get("outbound_started_at"):
        return build_result("sent_unknown", steps=steps, returned_object_id=assignment_id, error_code="override_creation_unresolved")
    snapshot = (payload.get("group_snapshot") or {}).get("tiers") or []
    expected = next((row for row in snapshot if str(row.get("label")) == str(tier.get("label"))), None)
    if not expected:
        return build_result(failure_state, steps=steps, returned_object_id=assignment_id, error_code="source_group_target_missing")
    request = {"assignment_override": {
        "title": f"{tier['title']} - {expected['group_name']}",
        "group_id": expected.get("group_id"),
    }}
    path = f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides"
    marked = context.before_send(step_key, models.sha256_dict({
        "method": "POST", "path": path,
        "membership_digest": expected.get("membership_digest"),
        "group_id": expected.get("group_id"),
    }))
    _replace_local_step(steps, marked)
    response, error = canvas_client._canvas_send("POST", path, request)
    if error:
        state = "sent_unknown" if _is_uncertain(error) else failure_state
        marked["state"] = state
        marked["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "override_rejected"
        marked["private_diagnostic"] = type(error).__name__
        _replace_local_step(steps, context.checkpoint_step(marked))
        return build_result(state, steps=steps, returned_object_id=assignment_id, error_code=marked["error_code"])
    override_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
    if not override_id:
        marked["state"] = "sent_unknown"
        marked["error_code"] = "unparseable_response"
        _replace_local_step(steps, context.checkpoint_step(marked))
        return build_result("sent_unknown", steps=steps, returned_object_id=assignment_id, error_code=marked["error_code"])
    verified, verify_error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}/overrides/{override_id}"
    )
    if verify_error or not verified:
        marked["state"] = "sent_unknown"
        marked["error_code"] = "override_exact_id_unverified"
        _replace_local_step(steps, context.checkpoint_step(marked, returned_object_id=override_id))
        return build_result("sent_unknown", steps=steps, returned_object_id=assignment_id, error_code=marked["error_code"])
    marked["state"] = "applied"
    _replace_local_step(steps, context.checkpoint_step(marked, returned_object_id=override_id))
    return None


def _publish_assignment(*, course_id, assignment_id, payload, steps, context, step_key, failure_state):
    return _put_and_verify(
        course_id=course_id, assignment_id=assignment_id,
        request={"assignment": {
            "published": True, "only_visible_to_overrides": True,
            "omit_from_final_grade": True, "post_to_sis": False,
        }}, steps=steps, context=context, step_key=step_key,
        failure_state=failure_state, verify_published=True,
        error_prefix="publish_assignment",
    )


def _put_and_verify(*, course_id, assignment_id, request, steps, context, step_key, failure_state, verify_published, error_prefix):
    step = ensure_step(steps, step_key)
    if step.get("state") in {"applied", "skipped"}:
        current, error = canvas_client.canvas_get(f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
        if error or not current:
            return build_result("sent_unknown", steps=steps, returned_object_id=assignment_id, error_code=f"{error_prefix}_exact_id_unverified")
        return None
    if step.get("outbound_started_at"):
        return build_result("sent_unknown", steps=steps, returned_object_id=assignment_id, error_code=f"{error_prefix}_unresolved")
    path = f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
    marked = context.before_send(step_key, models.sha256_dict({"method": "PUT", "path": path, "payload": request}))
    _replace_local_step(steps, marked)
    _response, error = canvas_client._canvas_send("PUT", path, request)
    if error:
        state = "sent_unknown" if _is_uncertain(error) else failure_state
        marked["state"] = state
        marked["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else f"{error_prefix}_rejected"
        marked["private_diagnostic"] = type(error).__name__
        _replace_local_step(steps, context.checkpoint_step(marked))
        return build_result(state, steps=steps, returned_object_id=assignment_id, error_code=marked["error_code"])
    current, verify_error = canvas_client.canvas_get(path)
    expected = request["assignment"]
    if verify_error or not current or any(current.get(key) != value for key, value in expected.items()):
        marked["state"] = "sent_unknown"
        marked["error_code"] = f"{error_prefix}_unverified"
        _replace_local_step(steps, context.checkpoint_step(marked))
        return build_result("sent_unknown", steps=steps, returned_object_id=assignment_id, error_code=marked["error_code"])
    marked["state"] = "applied"
    _replace_local_step(steps, context.checkpoint_step(marked, returned_object_id=assignment_id))
    return None


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
