"""Shared Canvas Assignment-type module placement helpers."""

from __future__ import annotations

from .. import models
from .adapter_support import (
    build_result,
    ensure_step,
    find_step,
    is_uncertain,
    module_id_from_steps,
    normalize,
    replace_step,
)
from api.platform_services import canvas_client


def _resolve_or_create_module(
    *,
    course_id: str,
    module_name: str = "",
    module_id: str | None = None,
    create_module: bool = False,
    steps: list[dict],
    context,
    attach_step_key: str,
    returned_object_id: str,
    deterministic_failure_state: str,
    read_modules=None,
) -> tuple[str | None, dict | None]:
    """Resolve an existing Canvas module or create one.

    Returns (module_id, None) on success — caller proceeds to attach phase.
    Returns (None, result_dict) on early exit — caller must return result_dict.
    Preserves exact Canvas call order, checkpoint timing, step state transitions,
    error codes, and result shapes from the pre-split behavior.
    """
    create_module_step = find_step(steps, "create_module")
    attach_step = find_step(steps, attach_step_key)
    selected_module_id = str(module_id or module_id_from_steps(create_module_step, attach_step) or "")

    if selected_module_id and not create_module:
        module, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/modules/{selected_module_id}"
        )
        if error or not isinstance(module, dict) or str(module.get("id") or "") != selected_module_id:
            return (None, build_result(
                "blocked" if not error else "sent_unknown",
                steps=steps,
                error_code="module_exact_id_unverified",
                returned_object_id=returned_object_id,
            ))
        attach_step = ensure_step(steps, attach_step_key)
        attach_step["module_id"] = selected_module_id
        return (selected_module_id, None)

    if create_module and not str(module_name or "").strip():
        return (None, build_result(
            "blocked", steps=steps, error_code="module_name_required",
            returned_object_id=returned_object_id,
        ))

    if create_module:
        # Explicit create intent never falls back to name lookup.
        matches = []
    elif not str(module_name or "").strip():
        return (None, build_result(
            "blocked", steps=steps, error_code="module_selection_required",
            returned_object_id=returned_object_id,
        ))
    else:
        matches = None

    if matches is None:
        if read_modules is None:
            modules, error = canvas_client.canvas_get_all(
                f"/api/v1/courses/{course_id}/modules",
                {"per_page": 100},
            )
        else:
            modules, error = read_modules(course_id)
        if error:
            return (None, build_result(
                "sent_unknown",
                steps=steps,
                error_code="module_lookup_failed",
                private_diagnostic=error,
                returned_object_id=returned_object_id,
            ))
        matches = [
            module for module in modules
            if normalize(module.get("name")) == normalize(module_name)
        ]
    if len(matches) > 1:
        return (None, build_result(
            "blocked",
            steps=steps,
            error_code="ambiguous_module",
            returned_object_id=returned_object_id,
        ))
    if matches:
        selected_module_id = str(matches[0].get("id"))
        attach_step = ensure_step(steps, attach_step_key)
        attach_step["module_id"] = selected_module_id
        return (selected_module_id, None)

    create_module_step = ensure_step(steps, "create_module")
    if (
        create_module_step.get("state") == "sent_unknown"
        and create_module_step.get("outbound_started_at")
        and not create_module_step.get("returned_object_id")
    ):
        return (None, build_result(
            "sent_unknown",
            steps=steps,
            error_code="module_creation_unresolved",
            returned_object_id=returned_object_id,
        ))
    module_path = f"/api/v1/courses/{course_id}/modules"
    module_request = {"module": {"name": module_name}}
    digest = models.sha256_dict(
        {"method": "POST", "path": module_path, "payload": module_request}
    )
    marked = context.before_send("create_module", digest)
    replace_step(steps, marked)
    response, error = canvas_client._canvas_send(
        "POST",
        module_path,
        module_request,
    )
    if error:
        state = "sent_unknown" if is_uncertain(error) else deterministic_failure_state
        create_module_step["state"] = state if state == "sent_unknown" else "failed"
        create_module_step["error_code"] = (
            "timeout_or_disconnect" if state == "sent_unknown" else "module_rejected"
        )
        create_module_step["private_diagnostic"] = error
        create_module_step = context.checkpoint_step(create_module_step)
        replace_step(steps, create_module_step)
        return (None, build_result(
            state,
            steps=steps,
            error_code=create_module_step["error_code"],
            private_diagnostic=error,
            returned_object_id=returned_object_id,
        ))
    module_id_val = (
        str(response.get("id"))
        if isinstance(response, dict) and response.get("id") is not None
        else None
    )
    if not module_id_val:
        create_module_step["state"] = "sent_unknown"
        create_module_step["error_code"] = "unparseable_response"
        create_module_step["private_diagnostic"] = "missing module id"
        create_module_step = context.checkpoint_step(create_module_step)
        replace_step(steps, create_module_step)
        return (None, build_result(
            "sent_unknown",
            steps=steps,
            error_code="unparseable_response",
            returned_object_id=returned_object_id,
        ))
    create_module_step["state"] = "applied"
    create_module_step = context.checkpoint_step(
        create_module_step,
        returned_object_id=module_id_val,
    )
    replace_step(steps, create_module_step)
    return (module_id_val, None)


def _attach_module_item(
    *,
    course_id: str,
    content_id: str,
    title: str,
    module_id: str,
    steps: list[dict],
    context,
    attach_step_key: str,
    returned_object_id: str,
    deterministic_failure_state: str,
) -> dict:
    """Attach or verify a Canvas Assignment-type module item.

    Must be called after _resolve_or_create_module has returned a module_id.
    Preserves exact Canvas call order, checkpoint timing, result states, error codes,
    and returned_object_id behavior from the pre-split function.
    """
    attach_step = ensure_step(steps, attach_step_key)
    attach_step["module_id"] = str(module_id)
    item_id = attach_step.get("returned_object_id")
    if attach_step.get("state") in ("applied", "skipped") and item_id:
        item, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}"
        )
        if not error and item:
            attach_step["state"] = "skipped"
        else:
            return build_result(
                "sent_unknown",
                steps=steps,
                error_code="module_item_exact_id_unverified",
                returned_object_id=returned_object_id,
            )
        return build_result("applied", steps=steps, returned_object_id=returned_object_id)
    if attach_step.get("outbound_started_at") and not item_id:
        return build_result(
            "sent_unknown", steps=steps,
            error_code="module_item_creation_unresolved",
            returned_object_id=returned_object_id,
        )

    item_path = f"/api/v1/courses/{course_id}/modules/{module_id}/items"
    item_request = {
        "module_item": {
            "title": title,
            "type": "Assignment",
            "content_id": int(content_id) if str(content_id).isdigit() else str(content_id),
        }
    }
    attach_step = context.checkpoint_step(attach_step)
    replace_step(steps, attach_step)
    digest = models.sha256_dict(
        {"method": "POST", "path": item_path, "payload": item_request}
    )
    marked = context.before_send(attach_step_key, digest)
    marked["module_id"] = str(module_id)
    replace_step(steps, marked)
    attach_step = marked
    response, error = canvas_client._canvas_send("POST", item_path, item_request)
    if error:
        state = "sent_unknown" if is_uncertain(error) else deterministic_failure_state
        attach_step["state"] = state if state == "sent_unknown" else "failed"
        attach_step["error_code"] = (
            "timeout_or_disconnect" if state == "sent_unknown" else "module_item_rejected"
        )
        attach_step["private_diagnostic"] = error
        attach_step = context.checkpoint_step(attach_step)
        replace_step(steps, attach_step)
        return build_result(
            state,
            steps=steps,
            error_code=attach_step["error_code"],
            private_diagnostic=error,
            returned_object_id=returned_object_id,
        )
    item_id = (
        str(response.get("id"))
        if isinstance(response, dict) and response.get("id") is not None
        else None
    )
    if not item_id:
        attach_step["state"] = "sent_unknown"
        attach_step["error_code"] = "unparseable_response"
        attach_step["private_diagnostic"] = "missing module item id"
        attach_step["module_id"] = str(module_id)
        attach_step = context.checkpoint_step(attach_step)
        replace_step(steps, attach_step)
        return build_result(
            "sent_unknown",
            steps=steps,
            error_code="unparseable_response",
            returned_object_id=returned_object_id,
        )
    attach_step["state"] = "applied"
    attach_step["module_id"] = str(module_id)
    attach_step = context.checkpoint_step(attach_step, returned_object_id=item_id)
    replace_step(steps, attach_step)
    return build_result("applied", steps=steps, returned_object_id=returned_object_id)


def attach_assignment_type_module_item(
    *,
    course_id: str,
    content_id: str,
    title: str,
    module_name: str = "",
    module_id: str | None = None,
    create_module: bool = False,
    steps: list[dict],
    context,
    attach_step_key: str,
    returned_object_id: str,
    deterministic_failure_state: str,
    read_modules=None,
) -> dict:
    """Orchestrate module resolution and Canvas Assignment-type module item attachment.

    Delegates to _resolve_or_create_module then _attach_module_item, preserving the
    exact Canvas call order, checkpoint timing, result states, and error codes.
    """
    module_id, early = _resolve_or_create_module(
        course_id=course_id,
        module_name=module_name,
        module_id=module_id,
        create_module=create_module,
        steps=steps,
        context=context,
        attach_step_key=attach_step_key,
        returned_object_id=returned_object_id,
        deterministic_failure_state=deterministic_failure_state,
        read_modules=read_modules,
    )
    if early is not None:
        return early
    return _attach_module_item(
        course_id=course_id,
        content_id=content_id,
        title=title,
        module_id=module_id,
        steps=steps,
        context=context,
        attach_step_key=attach_step_key,
        returned_object_id=returned_object_id,
        deterministic_failure_state=deterministic_failure_state,
    )
