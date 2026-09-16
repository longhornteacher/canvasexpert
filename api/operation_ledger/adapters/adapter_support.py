"""Shared helper primitives for content operation-ledger adapters."""

from __future__ import annotations

from api.platform_services import canvas_client

from .. import models


def ordered_steps(target: dict, order: tuple[str, ...]) -> list[dict]:
    existing = {step.get("step_key"): step for step in target.get("steps", [])}
    return [existing[key] for key in order if key in existing]


def find_step(steps: list[dict], step_key: str) -> dict:
    return next(
        (step for step in steps if step.get("step_key") == step_key),
        models.new_step(step_key),
    )


def prepend_step(steps: list[dict], step_key: str) -> dict:
    found = next((step for step in steps if step.get("step_key") == step_key), None)
    if found is not None:
        return found
    step = models.new_step(step_key)
    steps.insert(0, step)
    return step


def ensure_step(steps: list[dict], step_key: str) -> dict:
    found = next((step for step in steps if step.get("step_key") == step_key), None)
    if found is not None:
        return found
    step = models.new_step(step_key)
    steps.append(step)
    return step


def replace_step(steps: list[dict], step: dict) -> None:
    for index, existing in enumerate(steps):
        if existing.get("step_key") == step.get("step_key"):
            steps[index] = step
            return
    steps.append(step)


def module_id_from_steps(create_step: dict, attach_step: dict) -> str | None:
    return (
        str(create_step.get("returned_object_id"))
        if create_step.get("returned_object_id") is not None
        else str(attach_step.get("module_id"))
        if attach_step.get("module_id") is not None
        else None
    )


def has_outbound_marker(steps: list[dict]) -> bool:
    return any(step.get("outbound_started_at") for step in steps)


def as_list(data) -> list:
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def normalize(value) -> str:
    return str(value or "").strip().lower()


def is_uncertain(error: str) -> bool:
    lower = str(error or "").lower()
    return any(
        term in lower
        for term in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "unparseable",
            "no response",
            "read timed out",
        )
    )


def get_assignment(course_id: str, assignment_id: str) -> tuple[dict | None, str | None]:
    assignment, error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
    )
    if error or not isinstance(assignment, dict):
        return None, str(error or "invalid assignment response")
    return assignment, None


def build_result(
    state: str,
    *,
    steps: list[dict],
    returned_object_id: str | None = None,
    returned_object_url: str | None = None,
    error_code: str | None = None,
    private_diagnostic: str | None = None,
    failed_items: list[dict] | None = None,
    cleanup_required: bool | None = None,
    rollback_state: str | None = None,
    rollback_error_code: str | None = None,
) -> dict:
    result = {
        "state": state,
        "returned_object_id": returned_object_id,
        "returned_object_url": returned_object_url,
        "error_code": error_code,
        "private_diagnostic": private_diagnostic,
        "steps": steps,
    }
    if failed_items is not None:
        result["failed_items"] = failed_items
    if cleanup_required is not None:
        result["cleanup_required"] = cleanup_required
    if rollback_state is not None:
        result["rollback_state"] = rollback_state
    if rollback_error_code is not None:
        result["rollback_error_code"] = rollback_error_code
    return result
