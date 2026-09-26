"""Checkpointed Differentiated Hub delivery: restricted Canvas tier pages plus one assignment."""
from __future__ import annotations

from datetime import datetime

from .. import models
from . import assignment_whole
from .adapter_support import build_result, ensure_step, find_step, is_uncertain, replace_step
from api.platform_services import canvas_client


def _page_path(course_id, page_id):
    return f"/api/v1/courses/{course_id}/pages/{page_id}"


def _dates_path(course_id, page_id):
    return f"{_page_path(course_id, page_id)}/date_details"


def _read_dates(course_id, page_id):
    return canvas_client.canvas_get(_dates_path(course_id, page_id))


def _send(context, steps, key, method, path, payload):
    marked = context.before_send(key, models.sha256_dict({"method": method, "path": path, "payload": payload}))
    replace_step(steps, marked)
    response, error = canvas_client._canvas_send(method, path, payload)
    if error:
        marked["state"] = "sent_unknown" if is_uncertain(error) else "failed"
        if is_uncertain(error):
            marked["error_code"] = "timeout_or_disconnect"
        elif str(error).startswith("HTTP 4"):
            marked["error_code"] = "canvas_4xx"
        else:
            marked["error_code"] = "canvas_rejected"
        marked["private_diagnostic"] = type(error).__name__
        context.checkpoint_step(marked)
        replace_step(steps, marked)
        return None, build_result(marked["state"], steps=steps, error_code=marked["error_code"])
    return response, None


def _create_page(course_id, tier, index, steps, context):
    key = f"create_tier_page:{index}"
    step = ensure_step(steps, key)
    page_id = step.get("returned_object_id")
    if page_id:
        page, error = canvas_client.canvas_get(_page_path(course_id, page_id))
        if not error and isinstance(page, dict):
            step["state"] = "skipped"
            step["page_slug"] = page.get("url") or page_id
            replace_step(steps, step)
            if not page.get("url"):
                return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_slug_unverified")
            return str(page_id), page.get("html_url"), page["url"], None
        if step.get("outbound_started_at"):
            return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_exact_id_unverified")
    elif step.get("outbound_started_at"):
        rows, error = canvas_client.canvas_get_all(
            f"/api/v1/courses/{course_id}/pages", {"search_term": tier["title"], "per_page": 100})
        if error:
            return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_lookup_unverified")
        exact = [row for row in rows or [] if str(row.get("title") or "").strip() == tier["title"].strip()]
        timed = [row for row in exact if row.get("created_at")]
        if timed and step.get("outbound_started_at"):
            try:
                start = datetime.fromisoformat(str(step["outbound_started_at"]).replace("Z", "+00:00"))
                exact = [row for row in timed if abs((datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")) - start).total_seconds()) <= 600]
            except (ValueError, TypeError):
                pass
        if len(exact) == 1:
            found = exact[0]
            page_id = str(found.get("page_id") or found.get("id") or found.get("url") or "")
            if not page_id:
                return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_lookup_unverified")
            if not found.get("url"):
                return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_slug_unverified")
            step.update({"state": "applied", "returned_object_id": page_id,
                         "returned_object_url": found.get("html_url"), "page_slug": found["url"]})
            context.checkpoint_step(step)
            replace_step(steps, step)
            return page_id, found.get("html_url"), found["url"], None
        if len(exact) > 1:
            return None, None, None, build_result("blocked", steps=steps, error_code="duplicate_suspected")
        if step.get("retry_attempted"):
            return None, None, None, build_result("failed", steps=steps, error_code="tier_page_create_retry_failed")
        step["retry_attempted"] = True
        replace_step(steps, step)
    body = {"wiki_page": {"title": tier["title"], "body": tier["description"], "published": False}}
    response, failure = _send(context, steps, key, "POST", f"/api/v1/courses/{course_id}/pages", body)
    if failure:
        return None, None, None, failure
    page_id = (response or {}).get("page_id") or (response or {}).get("id") or (response or {}).get("url")
    if page_id is None:
        return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_create_unverified")
    page_id = str(page_id)
    step = find_step(steps, key)
    step.update({"state": "applied", "returned_object_id": page_id,
                 "returned_object_url": (response or {}).get("html_url"),
                 "page_slug": (response or {}).get("url") or page_id})
    context.checkpoint_step(step, returned_object_id=page_id,
                            returned_object_url=(response or {}).get("html_url"))
    replace_step(steps, step)
    page, error = canvas_client.canvas_get(_page_path(course_id, page_id))
    if error or not isinstance(page, dict):
        return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_create_unverified")
    page_slug = page.get("url") or (response or {}).get("url")
    if not page_slug:
        return None, None, None, build_result("sent_unknown", steps=steps, error_code="tier_page_slug_unverified")
    return page_id, page.get("html_url") or (response or {}).get("html_url"), page_slug, None


def _restrict(course_id, page_id, index, steps, context):
    key = f"restrict_tier_page:{index}"
    dates, error = _read_dates(course_id, page_id)
    if not error and isinstance(dates, dict) and dates.get("visible_to_everyone") is False:
        step = ensure_step(steps, key); step["state"] = "skipped"; replace_step(steps, step)
        return None
    request = {"only_visible_to_overrides": True, "assignment_overrides": []}
    _, failure = _send(context, steps, key, "PUT", _dates_path(course_id, page_id), request)
    if failure:
        return failure
    dates, error = _read_dates(course_id, page_id)
    if error or not isinstance(dates, dict) or dates.get("visible_to_everyone") is not False:
        return build_result("sent_unknown", steps=steps, error_code="tier_page_restriction_unverified")
    step = find_step(steps, key); step["state"] = "applied"; context.checkpoint_step(step); replace_step(steps, step)
    return None


def _clear_assignment(course_id, page_id, index, steps, context):
    key = f"clear_tier_page_assignment:{index}"
    _, failure = _send(
        context, steps, key, "PUT", _dates_path(course_id, page_id),
        {"only_visible_to_overrides": True, "assignment_overrides": []},
    )
    if failure:
        return failure
    dates, error = _read_dates(course_id, page_id)
    if error or not isinstance(dates, dict) or dates.get("visible_to_everyone") is not False or dates.get("overrides"):
        step = find_step(steps, key)
        step.update({"state": "sent_unknown", "error_code": "tier_page_unassigned_state_unverified"})
        context.checkpoint_step(step); replace_step(steps, step)
        return build_result("sent_unknown", steps=steps, error_code="tier_page_unassigned_state_unverified")
    step = find_step(steps, key); step["state"] = "applied"
    context.checkpoint_step(step); replace_step(steps, step)
    return None


def _assign(course_id, tier, page_id, index, steps, context):
    key = f"assign_tier_page:{index}"
    if tier.get("tag_status") != "matched":
        step = ensure_step(steps, key); step["state"] = "skipped"; replace_step(steps, step)
        return None
    category_id, group_id = tier.get("matched_category_id"), tier.get("matched_group_id")
    group, error = canvas_client.canvas_get(f"/api/v1/groups/{group_id}")
    if error or not isinstance(group, dict) or str(group.get("id")) != str(group_id) or \
            str(group.get("group_category_id")) != str(category_id) or \
            str(group.get("name") or "").strip().casefold() != str(tier.get("tag") or "").strip().casefold() or \
            group.get("non_collaborative") is not True:
        tier["tag_status"] = "unavailable"
        step = ensure_step(steps, key); step.update({"state": "skipped", "error_code": "tag_changed"})
        context.checkpoint_step(step); replace_step(steps, step)
        return _clear_assignment(course_id, page_id, index, steps, context)
    path = _dates_path(course_id, page_id)
    request = {"only_visible_to_overrides": True,
               "assignment_overrides": [{"group_id": int(group_id)}]}
    _, failure = _send(context, steps, key, "PUT", path, request)
    if failure:
        # Canvas 4xx means assignment refused; page remains restricted and apply continues.
        if failure.get("error_code") == "canvas_4xx":
            tier["tag_status"] = "unavailable"
            step = find_step(steps, key); step["state"] = "skipped"; step["error_code"] = "tag_assignment_refused"
            context.checkpoint_step(step); replace_step(steps, step)
            return None
        return failure
    dates, error = _read_dates(course_id, page_id)
    overrides = (dates or {}).get("overrides") or []
    if not error and len(overrides) == 1 and str(overrides[0].get("group_id")) == str(group_id):
        step = find_step(steps, key); step["state"] = "applied"; context.checkpoint_step(step); replace_step(steps, step)
        return None
    tier["tag_status"] = "unavailable"
    # A mismatched postcondition is not allowed to leave a page assigned to a
    # different group. Clear it and verify the restricted, unassigned shape.
    clear_key = f"clear_tier_page_assignment:{index}"
    clear_failure = _clear_assignment(course_id, page_id, index, steps, context)
    if clear_failure:
        return clear_failure
    step = find_step(steps, key); step.update({"state": "skipped", "error_code": "tag_assignment_unverified"})
    context.checkpoint_step(step); replace_step(steps, step)
    return None


def _unpublish_page(course_id, page_id, index, steps, context):
    key = f"unpublish_tier_page:{index}"
    _, failure = _send(context, steps, key, "PUT", _page_path(course_id, page_id),
                       {"wiki_page": {"published": False}})
    if failure:
        return failure
    page, error = canvas_client.canvas_get(_page_path(course_id, page_id))
    if error or not isinstance(page, dict) or page.get("published") is not False:
        step = find_step(steps, key); step.update({"state": "sent_unknown", "error_code": "tier_page_unpublish_unverified"})
        context.checkpoint_step(step); replace_step(steps, step)
        return build_result("sent_unknown", steps=steps, error_code="tier_page_unpublish_unverified")
    step = find_step(steps, key); step["state"] = "applied"
    context.checkpoint_step(step); replace_step(steps, step)
    return None


def publish_tier_page(course_id, page_id, index, steps, context):
    key = f"publish_tier_page:{index}"
    page, error = canvas_client.canvas_get(_page_path(course_id, page_id))
    if error or not isinstance(page, dict):
        cleanup_failure = _unpublish_page(course_id, page_id, index, steps, context)
        if cleanup_failure:
            return cleanup_failure
        return build_result("sent_unknown", steps=steps, error_code="tier_page_exact_id_unverified")
    dates, error = _read_dates(course_id, page_id)
    if error or not isinstance(dates, dict):
        if page.get("published") is not False:
            cleanup_failure = _unpublish_page(course_id, page_id, index, steps, context)
            if cleanup_failure:
                return cleanup_failure
        return build_result("sent_unknown", steps=steps, error_code="tier_page_publish_visibility_unverified")
    if dates.get("visible_to_everyone") is not False:
        if page.get("published") is True:
            failure = _unpublish_page(course_id, page_id, index, steps, context)
            if failure: return failure
        return build_result("failed", steps=steps, error_code="tier_page_publish_visibility_refused")
    if page.get("published") is True:
        step = ensure_step(steps, key); step["state"] = "skipped"; replace_step(steps, step)
        return None
    _, failure = _send(context, steps, key, "PUT", _page_path(course_id, page_id),
                       {"wiki_page": {"published": True}})
    if failure:
        return failure
    page, page_error = canvas_client.canvas_get(_page_path(course_id, page_id))
    dates, dates_error = _read_dates(course_id, page_id)
    if page_error or dates_error or not isinstance(dates, dict):
        cleanup_failure = _unpublish_page(course_id, page_id, index, steps, context)
        if cleanup_failure:
            return cleanup_failure
        step = find_step(steps, key); step.update({"state": "sent_unknown", "error_code": "tier_page_publish_unverified"})
        context.checkpoint_step(step); replace_step(steps, step)
        return build_result("sent_unknown", steps=steps, error_code="tier_page_publish_unverified")
    if dates.get("visible_to_everyone") is not False:
        # Fail closed: immediately hide the accidentally public page.
        failure = _unpublish_page(course_id, page_id, index, steps, context)
        if failure:
            return failure
        return build_result("failed", steps=steps, error_code="tier_page_visibility_law_violation")
    step = find_step(steps, key); step["state"] = "applied"; context.checkpoint_step(step); replace_step(steps, step)
    return None


def execute(payload, target, context, *, ordered_steps, whole_execute,
            upload_course_file, find_assignment_group, read_modules, prepare_description):
    course_id = target["course_id"]
    steps = ordered_steps(target)
    pages = []
    for index, tier in enumerate(payload.get("tiers") or []):
        page_id, page_url, page_slug, failure = _create_page(course_id, tier, index, steps, context)
        if failure: return failure
        failure = _restrict(course_id, page_id, index, steps, context)
        if failure: return failure
        failure = _assign(course_id, tier, page_id, index, steps, context)
        if failure: return failure
        pages.append({"id": page_id, "url": page_url, "slug": page_slug})
    if payload.get("published"):
        for index, page in enumerate(pages):
            failure = publish_tier_page(course_id, page["id"], index, steps, context)
            if failure: return failure
    description = str(payload.get("description") or "")
    for index, page in enumerate(pages):
        token = f"{{{{ce-tier-page:{index}}}}}"
        href = f"/courses/{course_id}/pages/{page['slug']}"
        if token not in description:
            return build_result("blocked", steps=steps, error_code="tier_page_link_slot_missing")
        description = description.replace(token, href)
    whole_payload = {**payload, "description": description, "tiers": None, "hub": False}
    result = whole_execute(whole_payload, {**target, "steps": ordered_steps({"steps": steps})}, context,
                           ordered_steps=ordered_steps, upload_course_file=upload_course_file,
                           find_assignment_group=find_assignment_group, read_modules=read_modules,
                           prepare_description=prepare_description)
    if result.get("state") not in {"applied", "skipped"}:
        return result
    assignment_id = result.get("returned_object_id")
    assignment, error = canvas_client.canvas_get(f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
    body = str((assignment or {}).get("description") or "")
    if error or any(f"/courses/{course_id}/pages/{page['slug']}" not in body for page in pages):
        return build_result("sent_unknown", steps=ordered_steps({"steps": steps}),
                            returned_object_id=assignment_id, returned_object_url=result.get("returned_object_url"),
                            error_code="hub_links_unverified")
    return result


def reconcile(payload, target, *, ordered_steps):
    """Read only exact checkpointed objects; never infer page identity by title."""
    course_id = target["course_id"]
    steps = ordered_steps(target)
    for index, tier in enumerate(payload.get("tiers") or []):
        step = find_step(steps, f"create_tier_page:{index}")
        page_id = step.get("returned_object_id")
        if not page_id:
            return {"state": "sent_unknown" if step.get("outbound_started_at") else "pending"}
        page, error = canvas_client.canvas_get(_page_path(course_id, page_id))
        if error or not isinstance(page, dict): return {"state": "sent_unknown"}
        dates, date_error = _read_dates(course_id, page_id)
        if date_error or not isinstance(dates, dict) or dates.get("visible_to_everyone") is not False:
            return {"state": "sent_unknown"}
        assignment_step = find_step(steps, f"assign_tier_page:{index}")
        assigned_override = assignment_step.get("state") == "applied"
        overrides = dates.get("overrides") or []
        if assigned_override:
            if len(overrides) != 1 or str(overrides[0].get("group_id")) != str(tier.get("matched_group_id")):
                return {"state": "sent_unknown"}
        elif overrides:
            return {"state": "sent_unknown"}
        if payload.get("published"):
            if page.get("published") is not True:
                return {"state": "sent_unknown"}
        elif page.get("published") is True:
            return {"state": "sent_unknown"}
        for key in (f"restrict_tier_page:{index}", f"assign_tier_page:{index}",
                    f"clear_tier_page_assignment:{index}", f"publish_tier_page:{index}",
                    f"unpublish_tier_page:{index}"):
            current = find_step(steps, key)
            if current.get("outbound_started_at") and current.get("state") not in {"applied", "skipped"}:
                return {"state": "sent_unknown"}
    assignment_result = assignment_whole.reconcile(
        {**payload, "hub": False, "tiers": None}, target, ordered_steps=ordered_steps)
    if assignment_result.get("state") != "applied":
        return assignment_result
    assignment, error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_result.get('returned_object_id')}"
    )
    body = str((assignment or {}).get("description") or "")
    if error or any(f"/courses/{course_id}/pages/{find_step(steps, f'create_tier_page:{index}').get('page_slug')}" not in body
                    for index, _tier in enumerate(payload.get("tiers") or [])):
        return {"state": "sent_unknown"}
    return {**assignment_result, "steps": steps}
