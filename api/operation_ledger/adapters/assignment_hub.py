"""Checkpointed Differentiated Hub delivery: restricted Canvas tier pages plus one assignment.

The page helpers live in ``tier_pages``; this module sequences them around one
whole-class assignment.
"""
from __future__ import annotations

from . import assignment_whole, tier_pages
from .adapter_support import build_result
from api.platform_services import canvas_client


def execute(payload, target, context, *, ordered_steps, whole_execute,
            find_assignment_group, read_modules):
    course_id = target["course_id"]
    steps = ordered_steps(target)
    pages, failure = tier_pages.run_pages(
        course_id, payload.get("tiers"), payload.get("published"), steps, context)
    if failure: return failure
    description, failure = tier_pages.bind_links(payload.get("description"), pages, course_id, steps)
    if failure: return failure
    whole_payload = {**payload, "description": description, "tiers": None, "hub": False}
    result = whole_execute(whole_payload, {**target, "steps": ordered_steps({"steps": steps})}, context,
                           ordered_steps=ordered_steps,
                           find_assignment_group=find_assignment_group, read_modules=read_modules)
    if result.get("state") not in {"applied", "skipped"}:
        return result
    assignment_id = result.get("returned_object_id")
    assignment, error = canvas_client.canvas_get(f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
    body = str((assignment or {}).get("description") or "")
    if error or not tier_pages.links_present(body, pages, course_id):
        return build_result("sent_unknown", steps=ordered_steps({"steps": steps}),
                            returned_object_id=assignment_id, returned_object_url=result.get("returned_object_url"),
                            error_code="hub_links_unverified")
    return result


def reconcile(payload, target, *, ordered_steps):
    """Read only exact checkpointed objects; never infer page identity by title."""
    course_id = target["course_id"]
    steps = ordered_steps(target)
    unsettled = tier_pages.reconcile_pages(
        course_id, payload.get("tiers"), payload.get("published"), steps)
    if unsettled is not None:
        return unsettled
    assignment_result = assignment_whole.reconcile(
        {**payload, "hub": False, "tiers": None}, target, ordered_steps=ordered_steps)
    if assignment_result.get("state") != "applied":
        return assignment_result
    assignment, error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_result.get('returned_object_id')}"
    )
    body = str((assignment or {}).get("description") or "")
    if error or not tier_pages.slug_links_present(body, course_id, payload.get("tiers"), steps):
        return {"state": "sent_unknown"}
    return {**assignment_result, "steps": steps}
