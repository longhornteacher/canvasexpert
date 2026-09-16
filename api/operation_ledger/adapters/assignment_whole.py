"""Whole-class assignment execution and reconciliation helpers."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import requests

from .. import models
from .adapter_support import (
    as_list,
    build_result,
    find_step,
    has_outbound_marker,
    is_uncertain as _is_uncertain,
    module_id_from_steps,
    normalize,
    prepend_step,
    replace_step,
)
from .module_placement import attach_assignment_type_module_item
from api import operational_log
from api.platform_services import canvas_client
from api.platform_services import config


def execute(
    payload: dict,
    target: dict,
    context,
    *,
    ordered_steps,
    upload_course_file,
    file_link_html,
    find_assignment_group,
    read_modules,
) -> dict:
    course_id = target["course_id"]
    name = payload.get("name", "Untitled assignment")
    steps = ordered_steps(target)
    step = prepend_step(steps, "create_assignment")
    assignment_id = target.get("returned_object_id") or step.get("returned_object_id")
    assignment_url = target.get("returned_object_url") or step.get("returned_object_url")

    assignment_id, assignment_url, assignment_verified, result = _verify_existing_assignment(
        course_id=course_id,
        steps=steps,
        step=step,
        assignment_id=assignment_id,
        assignment_url=assignment_url,
    )
    if result is not None:
        return result

    if not assignment_verified:
        assignment_id, assignment_url, result = _create_assignment(
            payload=payload,
            course_id=course_id,
            name=name,
            steps=steps,
            context=context,
            ordered_steps=ordered_steps,
            upload_course_file=upload_course_file,
            file_link_html=file_link_html,
            find_assignment_group=find_assignment_group,
        )
        if result is not None:
            return result

    module_name = payload.get("module_name")
    if module_name and assignment_id:
        result = attach_assignment_type_module_item(
            course_id=course_id,
            content_id=assignment_id,
            title=name,
            module_name=module_name,
            steps=steps,
            context=context,
            attach_step_key="attach_module",
            returned_object_id=assignment_id,
            deterministic_failure_state="failed",
            read_modules=read_modules,
        )
        if result.get("state") != "applied":
            return result

    return build_result(
        "applied",
        steps=steps,
        returned_object_id=assignment_id,
        returned_object_url=assignment_url,
    )


def _verify_existing_assignment(*, course_id: str, steps: list[dict], step: dict, assignment_id: str | None, assignment_url: str | None) -> tuple[str | None, str | None, bool, dict | None]:
    assignment_verified = False
    if step.get("state") in ("applied", "skipped") and assignment_id:
        assignment, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
        )
        if not error and assignment:
            step["state"] = "skipped"
            assignment_url = assignment.get("html_url") or assignment_url
            assignment_verified = True
        else:
            return assignment_id, assignment_url, False, build_result(
                "sent_unknown",
                steps=steps,
                returned_object_id=assignment_id,
                returned_object_url=assignment_url,
                error_code="assignment_exact_id_unverified",
            )
    elif assignment_id:
        assignment, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
        )
        if not error and assignment:
            step["state"] = "skipped"
            assignment_url = assignment.get("html_url") or assignment_url
            assignment_verified = True
        elif step.get("outbound_started_at"):
            return assignment_id, assignment_url, False, build_result(
                "sent_unknown",
                steps=steps,
                returned_object_id=assignment_id,
                returned_object_url=assignment_url,
                error_code="assignment_exact_id_unverified",
            )
        else:
            assignment_id = None
    return assignment_id, assignment_url, assignment_verified, None


def _create_assignment(
    *,
    payload: dict,
    course_id: str,
    name: str,
    steps: list[dict],
    context,
    ordered_steps,
    upload_course_file,
    file_link_html,
    find_assignment_group,
) -> tuple[str | None, str | None, dict | None]:
    printable_path = payload.get("printable_path")
    description = payload.get("description")
    if printable_path:
        uploaded, upload_err = upload_course_file(course_id, printable_path)
        if upload_err:
            return None, None, build_result(
                "failed",
                steps=steps,
                error_code="printable_upload_failed",
                private_diagnostic=upload_err,
            )
        file_link = file_link_html(uploaded)
        description = "\n".join(part for part in (description, file_link) if part)

    assignment_data = {"name": name, "submission_types": payload.get("submission_types", ["online_text_entry"])}
    if description:
        assignment_data["description"] = description
    points = payload.get("points")
    if points is not None:
        assignment_data["points_possible"] = float(points)
    for key in ("allowed_extensions", "external_tool_tag_attributes"):
        value = payload.get(key)
        if value:
            assignment_data[key] = value
    for key in ("due_at", "unlock_at", "lock_at"):
        value = payload.get(key)
        if value:
            assignment_data[key] = value
    if payload.get("post_to_sis"):
        assignment_data["post_to_sis"] = True
    if payload.get("published"):
        assignment_data["published"] = True
    assignment_group_name = payload.get("assignment_group_name")
    if assignment_group_name:
        assignment_group_id = find_assignment_group(course_id, assignment_group_name)
        if assignment_group_id is not None:
            assignment_data["assignment_group_id"] = assignment_group_id

    request = {"assignment": assignment_data}
    path_create = f"/api/v1/courses/{course_id}/assignments"
    digest = models.sha256_dict({"method": "POST", "path": path_create, "payload": request})
    step = context.before_send("create_assignment", digest)
    replace_step(steps, step)
    response, error = canvas_client._canvas_send("POST", path_create, request)
    if error:
        state = "sent_unknown" if _is_uncertain(error) else "failed"
        step["state"] = state
        step["error_code"] = "timeout_or_disconnect" if state == "sent_unknown" else "canvas_rejected"
        step["private_diagnostic"] = error
        step = context.checkpoint_step(step)
        replace_step(steps, step)
        return None, None, build_result(
            state,
            steps=steps,
            error_code=step["error_code"],
            private_diagnostic=error,
        )
    assignment_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
    assignment_url = response.get("html_url") if isinstance(response, dict) else None
    if not assignment_id:
        step["state"] = "sent_unknown"
        step["error_code"] = "unparseable_response"
        step["private_diagnostic"] = "missing assignment id"
        step = context.checkpoint_step(step)
        replace_step(steps, step)
        return None, None, build_result(
            "sent_unknown",
            steps=ordered_steps({"steps": steps}),
            error_code="unparseable_response",
        )
    step["state"] = "applied"
    step = context.checkpoint_step(
        step,
        returned_object_id=assignment_id,
        returned_object_url=assignment_url,
    )
    replace_step(steps, step)
    return assignment_id, assignment_url, None


def reconcile(payload: dict, target: dict, *, ordered_steps) -> dict:
    course_id = target["course_id"]
    steps = ordered_steps(target)
    step = prepend_step(steps, "create_assignment")
    assignment_id = target.get("returned_object_id") or step.get("returned_object_id")
    has_marker = has_outbound_marker(steps)

    if not assignment_id:
        name = payload.get("name", "")
        assignments, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments",
            params={"per_page": 100, "search_term": name},
        )
        if error:
            return {"state": "sent_unknown"}
        if any(normalize(assignment.get("name")) == normalize(name) for assignment in as_list(assignments)):
            return {"state": "sent_unknown"}
        return {"state": "sent_unknown" if has_marker else "pending"}

    assignment, error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
    )
    if error:
        if "404" in str(error) and not step.get("outbound_started_at"):
            return {"state": "pending"}
        return {"state": "sent_unknown"}
    if not assignment:
        return {"state": "sent_unknown"}

    result = {
        "state": "applied",
        "returned_object_id": assignment_id,
        "returned_object_url": assignment.get("html_url"),
    }
    module_name = payload.get("module_name")
    if not module_name:
        return result

    create_module_step = find_step(steps, "create_module")
    attach_step = find_step(steps, "attach_module")
    module_id = module_id_from_steps(create_module_step, attach_step)
    if not module_id:
        return {
            "state": "sent_unknown" if has_marker else "pending",
            "returned_object_id": assignment_id,
            "returned_object_url": assignment.get("html_url"),
        }

    item_id = attach_step.get("returned_object_id")
    if item_id:
        item, item_error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}"
        )
        if not item_error and item:
            if (
                str(item.get("type", "")).lower() == "assignment"
                and str(item.get("content_id")) == str(assignment_id)
            ):
                result["module_item_id"] = item_id
                return result

    items, item_error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/modules/{module_id}/items",
        {"per_page": 100},
    )
    if item_error:
        return {"state": "sent_unknown", "returned_object_id": assignment_id}
    matches = [
        item
        for item in (items or [])
        if str(item.get("type", "")).lower() == "assignment"
        and str(item.get("content_id")) == str(assignment_id)
    ]
    if len(matches) == 1 and matches[0].get("id") is not None:
        result["module_item_id"] = str(matches[0]["id"])
        return result
    if attach_step.get("outbound_started_at") or has_marker:
        return {"state": "sent_unknown", "returned_object_id": assignment_id}
    return {"state": "pending", "returned_object_id": assignment_id}


def validate_printable_pdf(pdf_path: str, *, allowed_roots=None) -> tuple:
    if not pdf_path:
        return None, "printable_path is required"
    candidate = os.path.realpath(pdf_path)
    if not os.path.isfile(candidate):
        return None, "printable PDF not found"
    if Path(candidate).suffix.lower() != ".pdf":
        return None, "printable attachment must be a PDF"
    roots = allowed_printable_roots() if allowed_roots is None else allowed_roots()
    for root in roots:
        try:
            if os.path.commonpath([candidate, root]) == root:
                return Path(candidate), None
        except ValueError:
            continue
    return None, "printable PDF is outside Canvas Expert export folders"


def allowed_printable_roots():
    from api import runtime_paths

    roots = []
    try:
        roots.append(os.path.realpath(runtime_paths.printables_dir()))
    except Exception as exc:
        operational_log.emit("operation_ledger.printable_root_resolve", "failed", error_class=type(exc))
    try:
        workspace_path = config.get_workspace_path()
        if workspace_path:
            roots.append(os.path.realpath(workspace_path))
    except Exception as exc:
        operational_log.emit("operation_ledger.printable_root_resolve", "failed", error_class=type(exc))
    return roots


def upload_course_file(course_id: str, pdf_path: Path, *, allowed_roots=None):
    pdf, error = validate_printable_pdf(str(pdf_path), allowed_roots=allowed_roots)
    if error:
        return None, error
    filename = pdf.name
    content_type = mimetypes.guess_type(filename)[0] or "application/pdf"
    init_payload = {
        "name": filename,
        "size": pdf.stat().st_size,
        "content_type": content_type,
        "parent_folder_path": "Canvas Expert Printables",
        "on_duplicate": "rename",
    }
    init, error = canvas_client._canvas_send("POST", f"/api/v1/courses/{course_id}/files", init_payload)
    if error:
        return None, error
    upload_url = (init or {}).get("upload_url")
    upload_params = (init or {}).get("upload_params") or {}
    if not upload_url:
        return None, "Canvas did not return a file upload URL"
    try:
        with pdf.open("rb") as handle:
            response = requests.post(
                upload_url,
                data=upload_params,
                files={"file": (filename, handle, content_type)},
                timeout=60,
            )
    except requests.RequestException as exc:
        return None, str(exc)
    if response.status_code not in (200, 201):
        return None, f"Canvas file upload failed: HTTP {response.status_code}: {response.text[:300]}"
    try:
        return response.json(), None
    except ValueError:
        return None, "Canvas file upload returned a non-JSON response"


def file_link_html(uploaded_file: dict) -> str:
    import html as _html

    label = _html.escape(uploaded_file.get("display_name") or uploaded_file.get("filename") or "Printable PDF")
    url = uploaded_file.get("url") or uploaded_file.get("html_url") or ""
    if not url and uploaded_file.get("id"):
        url = f"/files/{uploaded_file['id']}/download?download_frd=1"
    if not url:
        return f"<p><strong>{label}</strong> uploaded to course files.</p>"
    return f'<p><a href="{_html.escape(str(url), quote=True)}">{label}</a></p>'


