"""PageForge adapter for the crash-safe ``content.page`` operation kind."""

import json

from engine.rendering.forge.canvas_html import render_page

from .. import models
from .adapter_support import (
    as_list as _as_list,
    build_result as _build_result,
    ensure_step as _ensure_step,
    find_step as _find_step,
    has_outbound_marker as _has_outbound_marker,
    is_uncertain as _is_uncertain,
    module_id_from_steps as _module_id_from_steps,
    normalize as _normalize_title,
    ordered_steps as _ordered_steps_from_order,
    prepend_step as _step,
    replace_step as _replace_local_step,
)
from api.platform_services import canvas_client, config
from api.student_text import normalize_author_model, normalize_student_text
from api.webui import pf


KIND = "content.page"

_RENDER_FIELDS = ("title", "layout", "overview", "sections", "extras", "unit_info", "body", "banner")


class PageAdapter:
    kind = KIND

    def build_payload(self, prepare_request: dict) -> dict:
        path = prepare_request.get("path")
        if not path:
            raise ValueError("path is required")
        data, problems = pf.parse_file(path)
        if data is None or problems:
            raise ValueError("; ".join(problems or ["unreadable file"]))
        title = normalize_student_text(data.get("title") or "").strip()
        model = {key: normalize_author_model(data[key]) for key in _RENDER_FIELDS if key in data}
        model["title"] = title
        tier_colors = config.get_tier_colors()
        body = render_page(model, palette_key=tier_colors["untiered"])
        published = bool(prepare_request.get("published"))
        module_name = prepare_request.get("module_name") or None
        if module_name:
            module_name = str(module_name).strip() or None
        return {
            "title": title,
            "body": body,
            "published": published,
            "module_name": module_name,
            "source_path": path,
        }

    def source_digest(self, payload: dict) -> str:
        return models.sha256_dict({
            "title": payload.get("title"),
            "body": payload.get("body"),
            "published": payload.get("published"),
            "module_name": payload.get("module_name"),
        })

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        active_ids = {str(course["id"]) for course in config.active_courses()}
        verified = []
        for target in targets:
            course_id = str(target.get("course_id") or "")
            if not course_id:
                raise ValueError("target missing course_id")
            if course_id not in active_ids:
                raise ValueError(f"course {course_id} is not in active courses")
            verified.append({
                "course_id": course_id,
                "target_key": self.target_key(payload, course_id),
                "idempotency_key": self.idempotency_key(payload, course_id),
            })
        return verified

    def target_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(f"{KIND}|{self.source_digest(payload)}|{course_id}")

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{self.source_digest(payload)}|{course_id}|{_normalize_title(payload.get('title'))}"
        )

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        title = payload.get("title", "")
        baseline = {"existing_page": None}
        pages, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/pages",
            params={"per_page": 100, "search_term": title},
        )
        if error:
            baseline["canvas_error"] = error
            return baseline
        for page in _as_list(pages):
            if _normalize_title(page.get("title")) == _normalize_title(title):
                baseline["existing_page"] = {
                    "url": page.get("url"),
                    "title": page.get("title"),
                    "body": page.get("body"),
                    "published": page.get("published"),
                }
                break
        return baseline

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        course_id = target["course_id"]
        course_name = course_id
        for course in config.active_courses():
            if str(course["id"]) == str(course_id):
                course_name = course.get("name") or course.get("nickname") or course_id
                break
        existing = baseline.get("existing_page")
        return {
            "course_name": course_name,
            "page_title": payload.get("title"),
            "published": payload.get("published"),
            "module_name": payload.get("module_name"),
            "baseline_has_existing_page": existing is not None,
            "baseline_page_url": existing.get("url") if existing else None,
        }

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        if baseline is None:
            return False
        if "canvas_error" in baseline:
            return True
        existing = baseline.get("existing_page")
        title = payload.get("title", "")
        pages, error = canvas_client.canvas_get(
            f"/api/v1/courses/{target['course_id']}/pages",
            params={"per_page": 100, "search_term": title},
        )
        if error:
            return True
        current = next((page for page in _as_list(pages)
                        if _normalize_title(page.get("title")) == _normalize_title(title)), None)
        if existing is None:
            return current is not None
        if current is None:
            return True
        return (current.get("body") != existing.get("body") or
                bool(current.get("published")) != bool(existing.get("published")))

    def execute(self, payload: dict, target: dict, baseline: dict, claim: dict, context) -> dict:
        course_id = target["course_id"]
        title = payload.get("title", "Untitled page")
        page_body = payload.get("body", "")
        published = bool(payload.get("published"))
        module_name = payload.get("module_name")
        steps = _ordered_steps(target)
        page_step = _step(steps, "create_page")
        page_slug = target.get("returned_object_id") or page_step.get("returned_object_id")
        page_url = target.get("returned_object_url") or page_step.get("returned_object_url")

        if page_step.get("state") in ("applied", "skipped") and page_slug:
            page, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/pages/{page_slug}")
            if not error and page:
                page_step["state"] = "skipped"
                page_url = page.get("html_url") or page_url
            else:
                return _build_result("sent_unknown", steps=steps,
                                     returned_object_id=page_slug,
                                     returned_object_url=page_url,
                                     error_code="page_exact_id_unverified")
        elif page_slug:
            page, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/pages/{page_slug}")
            if not error and page:
                page_step["state"] = "skipped"
                page_url = page.get("html_url") or page_url
            elif page_step.get("outbound_started_at"):
                return _build_result("sent_unknown", steps=steps,
                                     returned_object_id=page_slug,
                                     returned_object_url=page_url,
                                     error_code="page_exact_id_unverified")
            else:
                page_slug = None

        if not page_slug:
            request = {"wiki_page": {
                "title": title, "body": page_body, "published": published}}
            path = f"/api/v1/courses/{course_id}/pages"
            digest = models.sha256_dict({"method": "POST", "path": path,
                                         "payload": request})
            page_step = context.before_send("create_page", digest)
            _replace_local_step(steps, page_step)
            response, error = canvas_client._canvas_send("POST", path, request)
            if error:
                state = "sent_unknown" if _is_uncertain(error) else "failed"
                page_step["state"] = state
                page_step["error_code"] = (
                    "timeout_or_disconnect" if state == "sent_unknown"
                    else "canvas_rejected")
                page_step["private_diagnostic"] = error
                page_step = context.checkpoint_step(page_step)
                _replace_local_step(steps, page_step)
                return _build_result(state, steps=steps,
                                     error_code=page_step["error_code"],
                                     private_diagnostic=error)
            page_slug = response.get("url") if isinstance(response, dict) else None
            page_url = response.get("html_url") if isinstance(response, dict) else None
            if not page_slug:
                page_step["state"] = "sent_unknown"
                page_step["error_code"] = "unparseable_response"
                page_step["private_diagnostic"] = "missing page url"
                page_step = context.checkpoint_step(page_step)
                _replace_local_step(steps, page_step)
                return _build_result("sent_unknown", steps=_ordered_steps({"steps": steps}),
                                     error_code="unparseable_response")
            page_step["state"] = "applied"
            page_step = context.checkpoint_step(
                page_step, returned_object_id=page_slug, returned_object_url=page_url)
            _replace_local_step(steps, page_step)

        if not module_name:
            return _build_result("applied", steps=steps,
                                 returned_object_id=page_slug,
                                 returned_object_url=page_url)

        create_module_step = _find_step(steps, "create_module")
        attach_step = _find_step(steps, "attach_module")
        module_id = _module_id_from_steps(create_module_step, attach_step)

        if not module_id:
            modules, error = _read_modules(course_id)
            if error:
                return _build_result("sent_unknown", steps=steps,
                                     returned_object_id=page_slug,
                                     returned_object_url=page_url,
                                     error_code="module_lookup_failed")
            matches = [module for module in modules
                       if _normalize_title(module.get("name")) == _normalize_title(module_name)]
            if len(matches) > 1:
                return _build_result("blocked", steps=steps,
                                     returned_object_id=page_slug,
                                     returned_object_url=page_url,
                                     error_code="ambiguous_module")
            if matches:
                module_id = str(matches[0].get("id"))
                attach_step = _ensure_step(steps, "attach_module")
                attach_step["module_id"] = module_id
            else:
                create_module_step = _ensure_step(steps, "create_module")
                if (create_module_step.get("state") == "sent_unknown" and
                        create_module_step.get("outbound_started_at") and
                        not create_module_step.get("returned_object_id")):
                    return _build_result("sent_unknown", steps=steps,
                                         returned_object_id=page_slug,
                                         returned_object_url=page_url,
                                         error_code="module_creation_unresolved")
                module_path = f"/api/v1/courses/{course_id}/modules"
                module_request = {"module": {"name": module_name}}
                digest = models.sha256_dict({"method": "POST", "path": module_path,
                                             "payload": module_request})
                marked = context.before_send("create_module", digest)
                _replace_local_step(steps, marked)
                response, error = canvas_client._canvas_send(
                    "POST", module_path, module_request)
                if error:
                    state = "sent_unknown" if _is_uncertain(error) else "failed"
                    create_module_step["state"] = state
                    create_module_step["error_code"] = (
                        "timeout_or_disconnect" if state == "sent_unknown"
                        else "module_rejected")
                    create_module_step["private_diagnostic"] = error
                    create_module_step = context.checkpoint_step(create_module_step)
                    _replace_local_step(steps, create_module_step)
                    return _build_result(state, steps=steps,
                                         returned_object_id=page_slug,
                                         returned_object_url=page_url,
                                         error_code=create_module_step["error_code"])
                module_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
                if not module_id:
                    create_module_step["state"] = "sent_unknown"
                    create_module_step["error_code"] = "unparseable_response"
                    create_module_step["private_diagnostic"] = "missing module id"
                    create_module_step = context.checkpoint_step(create_module_step)
                    _replace_local_step(steps, create_module_step)
                    return _build_result("sent_unknown", steps=steps,
                                         returned_object_id=page_slug,
                                         returned_object_url=page_url,
                                         error_code="unparseable_response")
                create_module_step["state"] = "applied"
                create_module_step = context.checkpoint_step(
                    create_module_step, returned_object_id=module_id)
                _replace_local_step(steps, create_module_step)

        attach_step = _ensure_step(steps, "attach_module")
        attach_step["module_id"] = str(module_id)
        item_id = attach_step.get("returned_object_id")
        if attach_step.get("state") in ("applied", "skipped") and item_id:
            item, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}")
            if not error and item:
                attach_step["state"] = "skipped"
                return _build_result("applied", steps=steps,
                                     returned_object_id=page_slug,
                                     returned_object_url=page_url)
            return _build_result("sent_unknown", steps=steps,
                                 returned_object_id=page_slug,
                                 returned_object_url=page_url,
                                 error_code="module_item_exact_id_unverified")
        if attach_step.get("state") == "applied" and not item_id:
            # A previously applied attachment without an item ID cannot be
            # proven to exist; it must not be treated as success.
            attach_step["state"] = "sent_unknown"
            attach_step["error_code"] = "module_item_exact_id_unverified"
            attach_step = context.checkpoint_step(attach_step)
            _replace_local_step(steps, attach_step)
            return _build_result("sent_unknown", steps=steps,
                                 returned_object_id=page_slug,
                                 returned_object_url=page_url,
                                 error_code="module_item_exact_id_unverified")

        item_path = f"/api/v1/courses/{course_id}/modules/{module_id}/items"
        item_request = {"module_item": {
            "title": title, "type": "Page", "page_url": page_slug}}
        # Persist the exact selected module ID before the attachment mutation.
        attach_step = context.checkpoint_step(attach_step)
        _replace_local_step(steps, attach_step)
        digest = models.sha256_dict({"method": "POST", "path": item_path,
                                     "payload": item_request})
        marked = context.before_send("attach_module", digest)
        marked["module_id"] = str(module_id)
        _replace_local_step(steps, marked)
        attach_step = marked
        response, error = canvas_client._canvas_send("POST", item_path, item_request)
        if error:
            state = "sent_unknown" if _is_uncertain(error) else "failed"
            attach_step["state"] = state
            attach_step["error_code"] = (
                "timeout_or_disconnect" if state == "sent_unknown"
                else "module_item_rejected")
            attach_step["private_diagnostic"] = error
            attach_step = context.checkpoint_step(attach_step)
            _replace_local_step(steps, attach_step)
            return _build_result(state, steps=steps,
                                 returned_object_id=page_slug,
                                 returned_object_url=page_url,
                                 error_code=attach_step["error_code"])
        item_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
        if not item_id:
            # A successful POST without an ID cannot prove the attachment
            # landed; checkpoint as sent_unknown so reconciliation must
            # prove the exact Page/page_url relationship before applied.
            attach_step["state"] = "sent_unknown"
            attach_step["error_code"] = "unparseable_response"
            attach_step["private_diagnostic"] = "missing module item id"
            attach_step["module_id"] = str(module_id)
            attach_step = context.checkpoint_step(attach_step)
            _replace_local_step(steps, attach_step)
            return _build_result("sent_unknown", steps=steps,
                                 returned_object_id=page_slug,
                                 returned_object_url=page_url,
                                 error_code="unparseable_response")
        attach_step["state"] = "applied"
        attach_step["module_id"] = str(module_id)
        attach_step = context.checkpoint_step(attach_step, returned_object_id=item_id)
        _replace_local_step(steps, attach_step)
        return _build_result("applied", steps=_ordered_steps({"steps": steps}),
                             returned_object_id=page_slug,
                             returned_object_url=page_url)

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        course_id = target["course_id"]
        steps = _ordered_steps(target)
        page_step = _find_step(steps, "create_page")
        page_slug = target.get("returned_object_id") or page_step.get("returned_object_id")
        has_marker = _has_outbound_marker(steps)

        if not page_slug:
            title = payload.get("title", "")
            pages, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/pages",
                params={"per_page": 100, "search_term": title},
            )
            if error:
                return {"state": "sent_unknown"}
            if any(_normalize_title(page.get("title")) == _normalize_title(title)
                   for page in _as_list(pages)):
                return {"state": "sent_unknown"}
            return {"state": "sent_unknown" if has_marker else "pending"}

        page, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/pages/{page_slug}")
        if error:
            if "404" in str(error) and not page_step.get("outbound_started_at"):
                return {"state": "pending"}
            return {"state": "sent_unknown"}
        if not page:
            return {"state": "sent_unknown"}

        result = {"state": "applied", "returned_object_id": page_slug,
                  "returned_object_url": page.get("html_url")}
        if not payload.get("module_name"):
            return result

        create_module_step = _find_step(steps, "create_module")
        attach_step = _find_step(steps, "attach_module")
        module_id = _module_id_from_steps(create_module_step, attach_step)
        if not module_id:
            return {"state": "sent_unknown" if has_marker else "pending",
                    "returned_object_id": page_slug,
                    "returned_object_url": page.get("html_url")}

        item_id = attach_step.get("returned_object_id")
        if item_id:
            item, item_error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}")
            if not item_error and item:
                # Prove the exact Page/page_url relationship before applied.
                if (str(item.get("type", "")).lower() == "page"
                        and item.get("page_url") == page_slug):
                    result["module_item_id"] = item_id
                    return result
                # The stored item ID exists but does not identify this page;
                # fall through to the exact-match search below.

        items, item_error = canvas_client.canvas_get_all(
            f"/api/v1/courses/{course_id}/modules/{module_id}/items",
            {"per_page": 100},
        )
        if item_error:
            return {"state": "sent_unknown", "returned_object_id": page_slug}
        matches = [item for item in (items or [])
                   if str(item.get("type", "")).lower() == "page"
                   and item.get("page_url") == page_slug]
        if len(matches) == 1 and matches[0].get("id") is not None:
            result["module_item_id"] = str(matches[0]["id"])
            return result
        if attach_step.get("outbound_started_at") or has_marker:
            return {"state": "sent_unknown", "returned_object_id": page_slug}
        return {"state": "pending", "returned_object_id": page_slug}

    def retry_selector(self, operation: dict) -> list[dict]:
        return [target for target in operation.get("targets", [])
                if models.is_unresolved_target_state(target.get("state", "pending"))]


def _read_modules(course_id: str):
    return canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/modules", {"per_page": 100})


def _ordered_steps(target: dict) -> list[dict]:
    return _ordered_steps_from_order(
        target,
        ("create_page", "create_module", "attach_module"),
    )
