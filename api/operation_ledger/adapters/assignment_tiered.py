"""Tiered assignment execution and reconciliation helpers."""

from __future__ import annotations

from datetime import datetime

from .. import models
from .adapter_support import (
    build_result,
    canonical_html,
    canonical_text,
    canvas_message_from_error,
    ensure_step,
    find_step,
    is_uncertain as _is_uncertain,
)
from api.platform_services import canvas_client
from api.student_text import normalize_student_text
from .module_placement import attach_assignment_type_module_item
from . import differentiated_bridge

# AC3: how far back an exact-title match can be and still count as the
# create this step attempted, when Canvas reports a creation time.
_RECENT_CREATE_WINDOW_SECONDS = 600


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
            assignment_data = _assignment_data(
                payload, tier["title"], tier["description"], course_id,
                find_assignment_group,
            )
            existing, error = canvas_client.canvas_get(
                f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
            )
            if error or not existing:
                return build_result("sent_unknown", steps=steps, error_code="assignment_exact_id_unverified")
            needs_create_verification = assignment_step.get("state") not in {"applied", "skipped"}
            if needs_create_verification and not _source_shape_matches(
                existing, assignment_data, published=False,
            ):
                marked = context.checkpoint_step(
                    dict(assignment_step,
                         state="sent_unknown",
                         error_code="assignment_create_unverified",
                         returned_object_id=str(assignment_id),
                         returned_object_url=existing.get("html_url") or assignment_url,
                         private_diagnostic=_shape_mismatch_diagnostic(
                             existing, assignment_data, published=False,
                         )),
                    returned_object_id=str(assignment_id),
                    returned_object_url=existing.get("html_url") or assignment_url,
                )
                _replace_local_step(steps, marked)
                return build_result(
                    "sent_unknown", steps=steps,
                    returned_object_id=str(assignment_id),
                    returned_object_url=existing.get("html_url") or assignment_url,
                    error_code=marked["error_code"],
                    private_diagnostic=marked["private_diagnostic"],
                )
            assignment_step["state"] = "skipped"
            assignment_url = existing.get("html_url") or assignment_url
        elif assignment_step.get("outbound_started_at"):
            # AC3: the create's outcome is unknown and no id was recorded
            # (typically a crash between the send and the response). Resolve
            # it by exact title before ever sending a second create.
            lookup = _ambiguous_create_lookup(
                course_id=course_id, tier=tier, payload=payload, steps=steps,
                context=context, assignment_key=assignment_key,
                find_assignment_group=find_assignment_group,
            )
            if "assignment_id" not in lookup:
                return lookup
            assignment_id = lookup["assignment_id"]
            assignment_url = lookup["assignment_url"]
        else:
            stop_result, assignment_id, assignment_url = _create_tier_source(
                course_id=course_id, tier=tier, payload=payload, steps=steps,
                context=context, assignment_key=assignment_key,
                find_assignment_group=find_assignment_group,
            )
            if stop_result is not None:
                return stop_result

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


def _create_tier_source(
    *, course_id, tier, payload, steps, context, assignment_key, find_assignment_group,
) -> tuple[dict | None, str | None, str | None]:
    """POST and verify one tier source assignment.

    Returns ``(stop_result, assignment_id, assignment_url)``. ``execute()``
    must return ``stop_result`` immediately when it is not ``None``;
    otherwise the create succeeded and the two ids are set.
    """
    assignment_data = _assignment_data(
        payload, tier["title"], tier["description"], course_id, find_assignment_group,
    )
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
        return build_result(
            state, steps=steps, error_code=marked["error_code"],
            canvas_message=canvas_message_from_error(error),
        ), None, None
    assignment_id = str(response.get("id")) if isinstance(response, dict) and response.get("id") is not None else None
    assignment_url = response.get("html_url") if isinstance(response, dict) else None
    if not assignment_id:
        marked["state"] = "sent_unknown"
        marked["error_code"] = "unparseable_response"
        marked["private_diagnostic"] = "missing assignment id"
        marked = context.checkpoint_step(marked)
        _replace_local_step(steps, marked)
        return build_result("sent_unknown", steps=steps, error_code="unparseable_response"), None, None
    # A returned ID is not a postcondition. Re-read the exact assignment
    # before the publication call.
    verified, verify_error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
    )
    if verify_error or not verified or not _source_shape_matches(verified, assignment_data, published=False):
        marked["state"] = "sent_unknown"
        marked["error_code"] = "assignment_create_unverified"
        marked["private_diagnostic"] = verify_error or _shape_mismatch_diagnostic(
            verified or {}, assignment_data, published=False,
        )
        marked = context.checkpoint_step(
            marked, returned_object_id=assignment_id, returned_object_url=assignment_url,
        )
        _replace_local_step(steps, marked)
        return build_result(
            "sent_unknown", steps=steps,
            returned_object_id=assignment_id, returned_object_url=assignment_url,
            error_code=marked["error_code"], private_diagnostic=marked["private_diagnostic"],
        ), None, None
    marked["state"] = "applied"
    marked = context.checkpoint_step(
        marked, returned_object_id=assignment_id, returned_object_url=assignment_url,
    )
    _replace_local_step(steps, marked)
    return None, assignment_id, assignment_url


def _ambiguous_create_lookup(
    *, course_id, tier, payload, steps, context, assignment_key, find_assignment_group,
) -> dict:
    """AC3: resolve a create whose outcome is unknown and carries no id.

    Lists the course's assignments filtered by exact title, narrowed to
    those created within the last ten minutes of this step's own outbound
    marker when Canvas reports a creation time (falls back to the unfiltered
    exact-title matches when it does not, since recency can't be proven
    either way). Exactly one match is adopted as the create; none retries
    the create exactly once (a persistent ``retry_attempted`` marker on the
    step makes that retry idempotent under resume); more than one stops with
    ``duplicate_suspected``.

    Returns ``{"assignment_id": ..., "assignment_url": ...}`` when resolved,
    otherwise a terminal ``build_result(...)`` dict ``execute()`` must return
    as-is.
    """
    step = ensure_step(steps, assignment_key)
    title = str(tier["title"])
    matches, error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/assignments",
        {"per_page": 100, "search_term": title},
    )
    if error:
        return build_result("sent_unknown", steps=steps, error_code="assignment_lookup_unverified")
    exact = [row for row in (matches or []) if str(row.get("name") or "").strip() == title.strip()]
    candidates = _recent_candidates(exact, step.get("outbound_started_at"))

    if len(candidates) == 1:
        found = candidates[0]
        assignment_id = str(found.get("id"))
        verified, verify_error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
        )
        if verify_error or not verified:
            return build_result("sent_unknown", steps=steps, error_code="assignment_lookup_unverified")
        marked = dict(step)
        marked["state"] = "applied"
        marked = context.checkpoint_step(
            marked, returned_object_id=assignment_id,
            returned_object_url=verified.get("html_url"),
        )
        _replace_local_step(steps, marked)
        return {"assignment_id": assignment_id, "assignment_url": verified.get("html_url")}

    if len(candidates) > 1:
        ids = sorted(str(row.get("id")) for row in candidates)
        marked = dict(step)
        marked["state"] = "blocked"
        marked["error_code"] = "duplicate_suspected"
        marked["private_diagnostic"] = ",".join(ids)
        marked = context.checkpoint_step(marked)
        _replace_local_step(steps, marked)
        return build_result(
            "blocked", steps=steps, error_code="duplicate_suspected",
            private_diagnostic=",".join(ids),
        )

    # None found. Retry the create exactly once per step -- a persistent
    # marker (survives checkpointing/resume) prevents a second automatic
    # retry from piling up duplicate creates across repeated resumes.
    if step.get("retry_attempted"):
        marked = dict(step)
        marked["state"] = "failed"
        marked["error_code"] = "assignment_create_retry_failed"
        marked = context.checkpoint_step(marked)
        _replace_local_step(steps, marked)
        return build_result("failed", steps=steps, error_code="assignment_create_retry_failed")

    marked = dict(step)
    marked["retry_attempted"] = True
    _replace_local_step(steps, marked)
    stop_result, assignment_id, assignment_url = _create_tier_source(
        course_id=course_id, tier=tier, payload=payload, steps=steps, context=context,
        assignment_key=assignment_key, find_assignment_group=find_assignment_group,
    )
    if stop_result is not None:
        return stop_result
    return {"assignment_id": assignment_id, "assignment_url": assignment_url}


def _recent_candidates(rows: list[dict], outbound_started_at: str | None) -> list[dict]:
    timed = [row for row in rows if row.get("created_at")]
    if not timed or not outbound_started_at:
        return rows
    try:
        started = datetime.fromisoformat(str(outbound_started_at).replace("Z", "+00:00"))
    except ValueError:
        return rows
    recent = []
    for row in timed:
        try:
            created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if abs((created - started).total_seconds()) <= _RECENT_CREATE_WINDOW_SECONDS:
            recent.append(row)
    return recent


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
        for key in (f"publish_assignment:{index}",):
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
        "only_visible_to_overrides": False,
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
    return not _shape_mismatches(actual, expected, published=published)


def _shape_mismatch_diagnostic(actual: dict, expected: dict, *, published: bool) -> str:
    mismatches = _shape_mismatches(actual, expected, published=published)
    return "assignment postcondition mismatch: " + ", ".join(mismatches or ["unknown"])


def _shape_mismatches(actual: dict, expected: dict, *, published: bool) -> list[str]:
    mismatches = []
    for key in (
        "name", "description", "submission_types", "grading_type",
        "only_visible_to_overrides", "omit_from_final_grade", "post_to_sis",
    ):
        if key in expected and not _shape_value_matches(key, actual.get(key), expected[key]):
            mismatches.append(key)
    if "points_possible" in expected:
        try:
            points_match = float(actual.get("points_possible", 0)) == float(expected["points_possible"])
        except (TypeError, ValueError):
            points_match = False
        if not points_match:
            mismatches.append("points_possible")
    if "assignment_group_id" in expected and str(actual.get("assignment_group_id")) != str(expected["assignment_group_id"]):
        mismatches.append("assignment_group_id")
    if actual.get("published") is not published:
        mismatches.append("published")
    return mismatches


def _shape_value_matches(key: str, actual: object, expected: object) -> bool:
    if key == "description":
        # The create sends normalize_student_text(description), and Canvas's
        # sanitizer rewrites markup, so compare normalized visible text only
        # (Issue #11 live root cause).
        return (canonical_text(normalize_student_text(actual))
                == canonical_text(normalize_student_text(expected)))
    if key == "submission_types":
        return sorted(actual or []) == sorted(expected or [])
    return actual == expected


def _publish_assignment(*, course_id, assignment_id, payload, steps, context, step_key, failure_state):
    return _put_and_verify(
        course_id=course_id, assignment_id=assignment_id,
        request={"assignment": {
            "published": True, "only_visible_to_overrides": False,
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
        return build_result(
            state, steps=steps, returned_object_id=assignment_id,
            error_code=marked["error_code"],
            canvas_message=canvas_message_from_error(error),
        )
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
