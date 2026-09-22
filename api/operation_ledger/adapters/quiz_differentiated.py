"""Differentiated quiz execution and reconciliation helpers."""

from __future__ import annotations

from .. import models
from .adapter_support import as_list, build_result, has_outbound_marker, normalize
from . import differentiated_bridge, quiz_steps
from api.platform_services import canvas_client, config


def execute(
    payload: dict,
    target: dict,
    baseline: dict,
    context,
    *,
    ordered_steps,
) -> dict:
    course_id = target["course_id"]
    variants = payload.get("variants", [])
    steps = ordered_steps(target)
    last_quiz_id = None
    last_quiz_url = None
    for index, variant in enumerate(variants):
        plan = variant["plan"]
        title = plan.get("title", f"Untitled variant {index}")
        failure_state = _variant_failure_state(steps)
        quiz_step_before = next((step for step in steps if step.get("step_key") == f"create_quiz:{index}"), {})
        quiz_created_here = (
            not target.get("returned_object_id")
            and not quiz_step_before.get("returned_object_id")
            and not quiz_step_before.get("outbound_started_at")
        )
        quiz_id, quiz_url, result = quiz_steps.ensure_quiz(
            course_id=course_id,
            step_key=f"create_quiz:{index}",
            quiz_payload=plan.get("quiz_payload", {}),
            steps=steps,
            context=context,
            current_quiz_id=None,
            current_quiz_url=None,
            failure_state=failure_state,
        )
        if result is not None:
            return result
        last_quiz_id, last_quiz_url = quiz_id, quiz_url

        for ordinal, item in enumerate(plan.get("items", []), start=1):
            result = quiz_steps.ensure_item(
                course_id=course_id,
                quiz_id=quiz_id,
                quiz_url=quiz_url,
                step_key=f"create_item:{index}:{item.get('index', 0)}",
                item_payload=item.get("payload", {}),
                source_item_id=item.get("source_item_id", item.get("id")),
                source_type=item.get("source_type", item.get("type")),
                plan_index=item.get("index", ordinal),
                steps=steps,
                context=context,
                failure_state=_variant_failure_state(steps),
            )
            if result is not None:
                if result.get("error_code") == "item_rejected" and quiz_created_here and quiz_id:
                    rollback = quiz_steps.rollback_quiz(
                        course_id=course_id,
                        quiz_id=quiz_id,
                        step_key=f"rollback_quiz:{index}",
                        steps=steps,
                        context=context,
                    )
                    result["rollback_state"] = rollback["state"]
                    result["cleanup_required"] = rollback["state"] != "applied"
                    if rollback.get("error_code"):
                        result["rollback_error_code"] = rollback["error_code"]
                return result

        assignment_settings = plan.get("assignment_settings", {})
        if assignment_settings:
            result = quiz_steps.patch_assignment(
                course_id=course_id,
                quiz_id=quiz_id,
                quiz_url=quiz_url,
                step_key=f"patch_assignment:{index}",
                assignment_settings=assignment_settings,
                steps=steps,
                context=context,
                failure_state=_variant_failure_state(steps),
            )
            if result is not None:
                return result

    return differentiated_bridge.execute_family_tail(
        course_id=course_id,
        payload=payload,
        source_ids=[
            str(next(
                step for step in steps
                if step.get("step_key") == f"create_quiz:{index}"
            ).get("returned_object_id"))
            for index, _variant in enumerate(variants)
        ],
        source_titles=[variant["plan"]["title"] for variant in variants],
        steps=steps,
        context=context,
        failure_state="partial",
    )


def reconcile(payload: dict, target: dict) -> dict:
    course_id = target["course_id"]
    variants = payload.get("variants", [])
    stored_steps = {step["step_key"]: step for step in target.get("steps", [])}
    projected = []
    has_marker = has_outbound_marker(list(stored_steps.values()))
    for index, variant in enumerate(variants):
        plan = variant["plan"]
        title = plan.get("title", "")
        quiz_key = f"create_quiz:{index}"
        quiz_step = stored_steps.get(quiz_key, models.new_step(quiz_key))
        quiz_id = quiz_step.get("returned_object_id")
        if not quiz_id:
            assignments, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments",
                params={"per_page": 100, "search_term": title},
            )
            if error:
                return {"state": "sent_unknown", "steps": projected}
            if any(normalize(assignment.get("name")) == normalize(title) for assignment in as_list(assignments)):
                return {"state": "sent_unknown", "steps": projected}
            return {"state": "sent_unknown" if has_marker else "pending", "steps": projected}

        quiz, error = canvas_client.canvas_get(f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}")
        if error:
            if "404" in str(error) and not quiz_step.get("outbound_started_at"):
                return {"state": "pending", "steps": projected}
            return {"state": "sent_unknown", "steps": projected}
        if not quiz:
            return {"state": "sent_unknown", "steps": projected}
        projected.append({
            "step_key": quiz_key,
            "state": "applied",
            "returned_object_id": quiz_id,
            "returned_object_url": f"{config.get_canvas_base()}/courses/{course_id}/assignments/{quiz_id}",
            "error_code": None,
        })

        for item in plan.get("items", []):
            item_key = f"create_item:{index}:{item.get('index', 0)}"
            item_step = stored_steps.get(item_key, models.new_step(item_key))
            item_id = item_step.get("returned_object_id")
            if not item_id:
                return {"state": "sent_unknown" if has_marker else "pending", "steps": projected, "returned_object_id": quiz_id}
            verify, verify_error = canvas_client.canvas_get(
                f"/api/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/items/{item_id}"
            )
            if verify_error or not verify:
                return {"state": "sent_unknown", "steps": projected, "returned_object_id": quiz_id}
            projected.append({"step_key": item_key, "state": "applied", "returned_object_id": item_id, "returned_object_url": None, "error_code": None})

        patch_key = f"patch_assignment:{index}"
        patch_step = stored_steps.get(patch_key, models.new_step(patch_key))
        if patch_step.get("state") not in {"applied", "skipped"}:
            return {"state": "sent_unknown" if has_marker else "pending", "steps": projected}
        projected.append({"step_key": patch_key, "state": "applied", "returned_object_id": None, "returned_object_url": None, "error_code": None})

    return differentiated_bridge.reconcile_family_tail(
        course_id=course_id,
        payload=payload,
        source_ids=[
            str(stored_steps.get(f"create_quiz:{index}", {}).get("returned_object_id"))
            for index, _variant in enumerate(variants)
        ],
        source_titles=[variant["plan"]["title"] for variant in variants],
        stored_steps=list(stored_steps.values()),
        projected=projected,
    )


def _variant_failure_state(steps: list[dict]) -> str:
    for step in steps:
        if step.get("state") in ("applied", "skipped") and step.get("returned_object_id"):
            return "partial"
    return "failed"
