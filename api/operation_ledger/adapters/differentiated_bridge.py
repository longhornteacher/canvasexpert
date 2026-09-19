"""Shared differentiated-family bridge rules and ledger tail steps."""

from __future__ import annotations

import copy
import html
import math
import re
from datetime import datetime, time
from urllib.parse import urlsplit, urlunsplit

from api.platform_services import canvas_client, config
from api.student_text import normalize_student_text

from .. import models
from . import adapter_support
from .module_placement import attach_assignment_type_module_item


CANONICAL_TIERS = ("Support", "Core", "Accelerate", "Extend")
BRIDGE_SHAPE_FIELDS = (
    "name", "description", "points_possible", "assignment_group_id", "due_at",
    "grading_type", "submission_types", "published", "only_visible_to_overrides",
    "omit_from_final_grade", "post_to_sis", "overrides",
)
RECONCILIATION_FIELDS = ("name", "description", "due_at")

_FAMILY_KEYS = ("family_id", "differentiation_family_id", "canonical_family_id")
_TIER_KEYS = ("tier", "canonical_tier", "variant", "variant_label")


def _metadata_value(assignment: dict, keys: tuple[str, ...]):
    metadata = assignment.get("metadata")
    candidates = [assignment, metadata if isinstance(metadata, dict) else {}]
    for candidate in candidates:
        for key in keys:
            value = candidate.get(key)
            if value not in (None, ""):
                return value
    return None


def canonical_family_key(assignment: dict) -> str | None:
    value = _metadata_value(assignment, _FAMILY_KEYS)
    return str(value).strip() if value not in (None, "") else None


def canonical_assignment_tier(assignment: dict) -> str | None:
    value = _metadata_value(assignment, _TIER_KEYS)
    if value in (None, ""):
        return None
    try:
        return canonical_tier(value)
    except ValueError:
        return None


def normalized_family_title(value: object) -> str:
    """Normalize only for the legacy title fallback, never as primary identity."""
    title = normalize_student_text(value or "").strip()
    title = re.sub(r"\s*[-–—:]\s*(?:Support|Core|Accelerate|Extend|Bridge)\s*$", "", title, flags=re.I)
    return title.strip()


def discover_families(assignments: list[dict], registrations: list[dict] | None = None) -> list[dict]:
    """Return student-free CE-owned family candidates from assignment metadata.

    Stable family/tier metadata wins. Title normalization is used only when an
    assignment has no stable family metadata at all.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for assignment in assignments or []:
        if not isinstance(assignment, dict) or not assignment.get("id"):
            continue
        family_key = canonical_family_key(assignment)
        tier = canonical_assignment_tier(assignment)
        identity = "metadata"
        is_bridge = (
            _metadata_value(assignment, ("bridge", "is_bridge", "bridge_assignment")) is True
            or str(assignment.get("name") or "").strip().casefold().endswith(" - bridge")
        )
        if assignment.get("post_to_sis") is True and canonical_assignment_tier(assignment) is None and not is_bridge:
            continue
        if not family_key or not tier:
            if family_key and is_bridge:
                groups.setdefault((family_key, identity), []).append(assignment)
                continue
            family_key = normalized_family_title(assignment.get("name"))
            tier = canonical_assignment_tier({"tier": assignment.get("tier")}) or "unknown"
            identity = "title_fallback"
        if not family_key or tier == "unknown":
            continue
        groups.setdefault((family_key, identity), []).append(assignment)
    result = []
    for (family_key, identity), rows in sorted(groups.items(), key=lambda item: item[0]):
        tiers = {}
        bridges = []
        for row in rows:
            tier = canonical_assignment_tier(row)
            if tier:
                tiers.setdefault(tier, []).append(row)
            marker = _metadata_value(row, ("bridge", "is_bridge", "bridge_assignment"))
            if marker is True or str(row.get("name") or "").strip().casefold().endswith(" - bridge"):
                bridges.append(row)
        source_rows = [row for values in tiers.values() for row in values]
        result.append({
            "family_key": family_key,
            "family_title": normalized_family_title(rows[0].get("name")),
            "identity_source": identity,
            "source_assignment_ids": sorted(str(row.get("id")) for row in source_rows),
            "source_titles": [str(row.get("name") or "") for row in sorted(source_rows, key=lambda row: str(row.get("id")))],
            "source_tiers": sorted(tiers),
            "bridge_assignment_ids": sorted(str(row.get("id")) for row in bridges),
            "source_count": len(source_rows),
            "bridge_count": len(bridges),
            "grading_excluded": all(row.get("omit_from_final_grade") is True for row in source_rows),
        })
    return result


def canonical_tier(value: object) -> str:
    text = str(value or "").strip()
    for name in CANONICAL_TIERS:
        if text.casefold() == name.casefold():
            return name
    raise ValueError(
        "Differentiated content must use one canonical tier: "
        "Support, Core, Accelerate, or Extend."
    )


def resolve_public_tags(labels: list[object]) -> list[dict]:
    if len(labels) < 2:
        raise ValueError("Differentiated delivery requires at least two tiers")
    configured = config.get_tier_tags()
    resolved = []
    seen_tags: set[str] = set()
    seen_tiers: set[str] = set()
    for value in labels:
        tier = canonical_tier(value)
        if tier in seen_tiers:
            raise ValueError(f"Differentiated tier {tier!r} is used more than once")
        tag = str(configured.get(tier) or "").strip()
        if not tag:
            raise ValueError(
                f"Public Canvas tag for {tier} is missing; set every used tier tag in Settings."
            )
        tag_key = tag.casefold()
        if tag_key == "bridge":
            raise ValueError("The public Canvas tag 'Bridge' is reserved for the family bridge")
        if tag_key in seen_tags:
            raise ValueError(
                "Public Canvas tags for the used tiers must be unique after trimming "
                "and case-folding; update them in Settings."
            )
        seen_tiers.add(tier)
        seen_tags.add(tag_key)
        resolved.append({"tier": tier, "tag": tag})
    return resolved


def normalize_base_title(value: object) -> str:
    title = normalize_student_text(value or "").strip()
    if not title:
        raise ValueError("Differentiated content requires a base title")
    if re.search(r"(?:^|[\s_:/|\\\-‐‑‒–—−])+bridge\s*$", title, flags=re.IGNORECASE):
        raise ValueError(
            "Differentiated family titles must be unsuffixed; '- Bridge' is reserved"
        )
    return title


def source_title(base_title: str, tag: str) -> str:
    return f"{normalize_student_text(base_title)} - {normalize_student_text(tag)}"


def bridge_title(base_title: str) -> str:
    return f"{normalize_base_title(base_title)} - Bridge"


def require_family_delivery(due_at: object, module_name: object) -> tuple[str, str, str]:
    due_text = str(due_at or "").strip()
    module_text = str(module_name or "").strip()
    if not due_text:
        raise ValueError("Differentiated delivery requires due_at")
    if not module_text:
        raise ValueError("Differentiated delivery requires module_name")
    try:
        parsed = datetime.fromisoformat(due_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Differentiated due_at must be valid ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Differentiated due_at must include a UTC offset")
    bridge_due = datetime.combine(parsed.date(), time(23, 59), tzinfo=parsed.tzinfo)
    return due_text, module_text, bridge_due.isoformat(timespec="seconds")


def dashboard_url() -> str:
    raw = str(config.get_canvas_base() or "").strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Canvas base URL is invalid; update the Canvas connection in Settings")
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def bridge_description() -> str:
    url = html.escape(dashboard_url(), quote=True)
    return (
        "<p><strong>No submission is made here.</strong> This bridge keeps the "
        "assignment visible in the module and gradebook. Open your "
        f'<a href="{url}">Canvas Dashboard</a> and complete the color-suffixed '
        "version assigned to you.</p>"
    )


def expected_bridge(family: dict, *, active: bool) -> dict:
    return {
        "name": bridge_title(family["base_title"]),
        "description": family["bridge_description"],
        "points_possible": family["points_possible"],
        "assignment_group_id": family["assignment_group_id"],
        "due_at": family["bridge_due_at"],
        "grading_type": "points",
        "submission_types": ["none"],
        "only_visible_to_overrides": False,
        "published": active,
        "omit_from_final_grade": not active,
        "post_to_sis": active,
    }


def assignment_shape(assignment: dict, overrides: list[dict] | None = None) -> dict:
    return {
        "id": str(assignment.get("id") or ""),
        "course_id": str(assignment.get("course_id") or ""),
        "name": str(assignment.get("name") or ""),
        "description": str(assignment.get("description") or ""),
        "points_possible": _number(assignment.get("points_possible")),
        "assignment_group_id": str(assignment.get("assignment_group_id") or ""),
        "due_at": assignment.get("due_at"),
        "grading_type": assignment.get("grading_type"),
        "submission_types": sorted(assignment.get("submission_types") or []),
        "published": assignment.get("published") is True,
        "only_visible_to_overrides": assignment.get("only_visible_to_overrides") is True,
        "omit_from_final_grade": assignment.get("omit_from_final_grade") is True,
        "post_to_sis": assignment.get("post_to_sis") is True,
        "overrides": copy.deepcopy(overrides or []),
    }


def structural_digest(state: dict) -> str:
    return models.sha256_dict(state)


def bridge_matches(assignment: dict, family: dict, *, active: bool) -> bool:
    return _fields_match(assignment, expected_bridge(family, active=active))


def bridge_mismatch_fields(
    assignment: dict, family: dict, *, active: bool, overrides: list[dict] | None = None,
) -> list[str]:
    """Return only allowlisted bridge fields whose safe shapes differ."""
    expected = expected_bridge(family, active=active)
    actual = assignment_shape(assignment, overrides)
    mismatches = []
    for field in BRIDGE_SHAPE_FIELDS:
        if field == "overrides":
            if (overrides or []) != []:
                mismatches.append(field)
            continue
        if not _fields_match({field: actual.get(field)}, {field: expected.get(field)}):
            mismatches.append(field)
    return sorted(set(mismatches))


def execute_family_tail(
    *, course_id: str, payload: dict, source_ids: list[str], source_titles: list[str],
    steps: list[dict], context, failure_state: str,
) -> dict:
    family, source_rows, error = _verified_family_sources(
        course_id, payload, source_ids, source_titles
    )
    if error:
        return adapter_support.build_result(
            failure_state, steps=steps, error_code=error,
        )

    create_step = adapter_support.ensure_step(steps, "create_bridge")
    bridge_id = create_step.get("returned_object_id")
    bridge_url = create_step.get("returned_object_url")
    if bridge_id:
        assignment, read_error = adapter_support.get_assignment(course_id, str(bridge_id))
        activate_state = adapter_support.find_step(steps, "activate_bridge").get("state")
        expected_active = activate_state in {"applied", "skipped"}
        if read_error or not bridge_matches(
            assignment or {}, family, active=expected_active
        ):
            return adapter_support.build_result(
                "sent_unknown", steps=steps, error_code="bridge_exact_id_unverified",
            )
        create_step["state"] = "skipped"
        bridge_url = (assignment or {}).get("html_url") or bridge_url
    elif create_step.get("outbound_started_at"):
        return adapter_support.build_result(
            "sent_unknown", steps=steps, error_code="bridge_creation_unresolved",
        )
    else:
        path = f"/api/v1/courses/{course_id}/assignments"
        request = {"assignment": expected_bridge(family, active=False)}
        marked = context.before_send(
            "create_bridge",
            models.sha256_dict({"method": "POST", "path": path, "payload": request}),
        )
        adapter_support.replace_step(steps, marked)
        response, send_error = canvas_client._canvas_send("POST", path, request)
        if send_error:
            return _stop(
                steps, context, marked, send_error,
                uncertain_code="bridge_create_uncertain",
                rejection_code="bridge_create_rejected",
                failure_state=failure_state,
            )
        bridge_id = str((response or {}).get("id") or "")
        if not bridge_id:
            return _stop(
                steps, context, marked, "missing bridge id",
                uncertain_code="bridge_create_unparseable", force_uncertain=True,
                failure_state=failure_state,
            )
        bridge_url = (response or {}).get("html_url")
        marked["state"] = "claimed"
        marked = context.checkpoint_step(
            marked, returned_object_id=bridge_id,
            returned_object_url=bridge_url,
        )
        adapter_support.replace_step(steps, marked)
        assignment, read_error = adapter_support.get_assignment(course_id, bridge_id)
        if read_error or not bridge_matches(assignment or {}, family, active=False):
            return _stop(
                steps, context, marked, read_error or "bridge create postcondition mismatch",
                uncertain_code="bridge_create_unverified", force_uncertain=True,
                failure_state=failure_state,
            )
        marked["state"] = "applied"
        bridge_url = assignment.get("html_url") or bridge_url
        marked = context.checkpoint_step(
            marked, returned_object_id=bridge_id,
            returned_object_url=bridge_url,
        )
        adapter_support.replace_step(steps, marked)

    module_result = attach_assignment_type_module_item(
        course_id=course_id,
        content_id=str(bridge_id),
        title=bridge_title(family["base_title"]),
        module_name=payload["module_name"],
        steps=steps,
        context=context,
        attach_step_key="attach_bridge_module",
        returned_object_id=str(bridge_id),
        deterministic_failure_state=failure_state,
    )
    if module_result.get("state") != "applied":
        return module_result

    activate_step = adapter_support.ensure_step(steps, "activate_bridge")
    assignment, read_error = adapter_support.get_assignment(course_id, str(bridge_id))
    active = not read_error and bridge_matches(assignment or {}, family, active=True)
    if not active:
        if activate_step.get("outbound_started_at"):
            return adapter_support.build_result(
                "sent_unknown", steps=steps, returned_object_id=str(bridge_id),
                error_code="bridge_activation_unresolved",
            )
        path = f"/api/v1/courses/{course_id}/assignments/{bridge_id}"
        request = {"assignment": expected_bridge(family, active=True)}
        marked = context.before_send(
            "activate_bridge",
            models.sha256_dict({"method": "PUT", "path": path, "payload": request}),
        )
        adapter_support.replace_step(steps, marked)
        _response, send_error = canvas_client._canvas_send("PUT", path, request)
        if send_error:
            return _stop(
                steps, context, marked, send_error,
                uncertain_code="bridge_activation_uncertain",
                rejection_code="bridge_activation_rejected",
                failure_state=failure_state,
                returned_object_id=str(bridge_id),
            )
        assignment, read_error = adapter_support.get_assignment(course_id, str(bridge_id))
        if read_error or not bridge_matches(assignment or {}, family, active=True):
            return _stop(
                steps, context, marked,
                read_error or "bridge activation postcondition mismatch",
                uncertain_code="bridge_activation_unverified", force_uncertain=True,
                failure_state=failure_state,
                returned_object_id=str(bridge_id),
            )
        marked["state"] = "applied"
        marked = context.checkpoint_step(
            marked, returned_object_id=str(bridge_id),
            returned_object_url=assignment.get("html_url") or bridge_url,
        )
        adapter_support.replace_step(steps, marked)
    else:
        activate_step["state"] = "skipped"

    # Registration is the final proof-bearing step. Re-read every required
    # Canvas postcondition before any family record is saved.
    family, source_rows, error = _verified_family_sources(
        course_id, payload, source_ids, source_titles
    )
    if error:
        return adapter_support.build_result(
            "sent_unknown", steps=steps, returned_object_id=str(bridge_id),
            error_code=error,
        )
    assignment, read_error = adapter_support.get_assignment(course_id, str(bridge_id))
    if read_error or not bridge_matches(assignment or {}, family, active=True):
        return adapter_support.build_result(
            "sent_unknown", steps=steps, returned_object_id=str(bridge_id),
            error_code="bridge_final_shape_unverified",
        )
    overrides, override_error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/assignments/{bridge_id}/overrides",
        {"per_page": 100},
    )
    if override_error or overrides:
        return adapter_support.build_result(
            "sent_unknown", steps=steps, returned_object_id=str(bridge_id),
            error_code="bridge_overrides_unverified",
        )
    state = assignment_shape(assignment, [])
    registration = {
        "family_title": family["base_title"],
        "source_assignment_ids": list(source_ids),
        "source_titles": list(source_titles),
        "bridge_assignment_id": str(bridge_id),
        "bridge_state_digest": structural_digest(state),
    }
    register_step = adapter_support.ensure_step(steps, "register_family")
    try:
        config.save_sis_grade_bridge(course_id, registration)
        saved = config.get_sis_grade_bridge(course_id, family["base_title"])
    except Exception as exc:
        register_step["state"] = "blocked"
        register_step["error_code"] = "family_registration_failed"
        register_step["private_diagnostic"] = type(exc).__name__
        register_step = context.checkpoint_step(register_step)
        adapter_support.replace_step(steps, register_step)
        return adapter_support.build_result(
            "blocked", steps=steps, returned_object_id=str(bridge_id),
            error_code="family_registration_failed",
        )
    if saved != registration:
        register_step["state"] = "blocked"
        register_step["error_code"] = "family_registration_unverified"
        register_step = context.checkpoint_step(register_step)
        adapter_support.replace_step(steps, register_step)
        return adapter_support.build_result(
            "blocked", steps=steps, returned_object_id=str(bridge_id),
            error_code="family_registration_unverified",
        )
    register_step["state"] = "applied"
    register_step["bridge_state_digest"] = registration["bridge_state_digest"]
    register_step = context.checkpoint_step(register_step)
    adapter_support.replace_step(steps, register_step)
    return adapter_support.build_result(
        "applied", steps=steps, returned_object_id=str(bridge_id),
        returned_object_url=assignment.get("html_url") or bridge_url,
    )


def reconcile_family_tail(
    *, course_id: str, payload: dict, source_ids: list[str],
    source_titles: list[str], stored_steps: list[dict], projected: list[dict],
) -> dict:
    family, _rows, error = _verified_family_sources(
        course_id, payload, source_ids, source_titles
    )
    if error:
        return {"state": "sent_unknown", "steps": projected}
    by_key = {str(step.get("step_key") or ""): step for step in stored_steps}
    create_step = by_key.get("create_bridge") or {}
    bridge_id = str(create_step.get("returned_object_id") or "")
    if not bridge_id:
        return {"state": _unfinished_state(create_step), "steps": projected}
    bridge, read_error = adapter_support.get_assignment(course_id, bridge_id)
    if read_error or not bridge_matches(bridge or {}, family, active=True):
        return {"state": "sent_unknown", "steps": projected}
    projected.append(_safe_step(create_step, bridge.get("html_url")))

    attach_step = by_key.get("attach_bridge_module") or {}
    item_id = str(attach_step.get("returned_object_id") or "")
    module_id = str(attach_step.get("module_id") or "")
    if not item_id or not module_id:
        return {"state": _unfinished_state(attach_step), "steps": projected}
    item, item_error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/modules/{module_id}/items/{item_id}"
    )
    if (
        item_error or not isinstance(item, dict)
        or str(item.get("id") or "") != item_id
        or str(item.get("type") or "").casefold() != "assignment"
        or str(item.get("content_id") or "") != bridge_id
    ):
        return {"state": "sent_unknown", "steps": projected}
    module_step = by_key.get("create_module")
    if module_step:
        projected.append(_safe_step(module_step))
    projected.append(_safe_step(attach_step))

    activate_step = by_key.get("activate_bridge") or {}
    if activate_step.get("state") not in {"applied", "skipped"}:
        return {"state": _unfinished_state(activate_step), "steps": projected}
    projected.append(_safe_step(activate_step, bridge.get("html_url")))

    overrides, override_error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/assignments/{bridge_id}/overrides",
        {"per_page": 100},
    )
    if override_error or overrides:
        return {"state": "sent_unknown", "steps": projected}

    registration = config.get_sis_grade_bridge(course_id, payload["base_title"])
    expected_registration = {
        "family_title": payload["base_title"],
        "source_assignment_ids": list(source_ids),
        "source_titles": list(source_titles),
        "bridge_assignment_id": bridge_id,
        "bridge_state_digest": structural_digest(assignment_shape(bridge, [])),
    }
    register_step = by_key.get("register_family") or {}
    if registration != expected_registration:
        return {"state": "sent_unknown", "steps": projected}
    projected.append(_safe_step(register_step))
    return {
        "state": "applied",
        "steps": projected,
        "returned_object_id": bridge_id,
        "returned_object_url": bridge.get("html_url"),
    }


def _safe_step(step: dict, url: str | None = None) -> dict:
    return {
        "step_key": step.get("step_key"),
        "state": "applied",
        "returned_object_id": step.get("returned_object_id"),
        "returned_object_url": url or step.get("returned_object_url"),
        "error_code": None,
        **({"module_id": step.get("module_id")} if step.get("module_id") else {}),
    }


def _unfinished_state(step: dict) -> str:
    return "sent_unknown" if step.get("outbound_started_at") else "pending"


def _verified_family_sources(
    course_id: str, payload: dict, source_ids: list[str], source_titles: list[str]
) -> tuple[dict, list[dict], str | None]:
    if len(source_ids) < 2 or len(source_ids) != len(source_titles):
        return {}, [], "family_source_identity_invalid"
    rows = []
    points = []
    groups = []
    for source_id, title in zip(source_ids, source_titles):
        assignment, error = adapter_support.get_assignment(course_id, str(source_id))
        if error or assignment is None:
            return {}, rows, "source_exact_id_unverified"
        expected = {
            "name": title,
            "published": True,
            "only_visible_to_overrides": True,
            "omit_from_final_grade": True,
            "post_to_sis": False,
            "grading_type": "points",
            "due_at": payload["due_at"],
        }
        if not _fields_match(assignment, expected):
            return {}, rows, "source_final_shape_unverified"
        overrides, override_error = canvas_client.canvas_get_all(
            f"/api/v1/courses/{course_id}/assignments/{source_id}/overrides",
            {"per_page": 100},
        )
        if override_error or not overrides:
            return {}, rows, "source_override_unverified"
        points.append(_number(assignment.get("points_possible")))
        groups.append(str(assignment.get("assignment_group_id") or ""))
        rows.append(assignment)
    if any(not _numbers_equal(points[0], value) for value in points[1:]):
        return {}, rows, "mixed_points_possible"
    if len(set(groups)) != 1:
        return {}, rows, "mixed_assignment_groups"
    return {
        "base_title": payload["base_title"],
        "bridge_description": payload["bridge_description"],
        "bridge_due_at": payload["bridge_due_at"],
        "points_possible": points[0],
        "assignment_group_id": groups[0],
    }, rows, None


def _fields_match(actual: dict, expected: dict) -> bool:
    for key, value in expected.items():
        current = actual.get(key)
        if key == "points_possible":
            if not _numbers_equal(current, value):
                return False
        elif key == "assignment_group_id":
            if str(current) != str(value):
                return False
        elif key == "submission_types":
            if sorted(current or []) != sorted(value or []):
                return False
        elif current != value:
            return False
    return True


def _stop(
    steps: list[dict], context, step: dict, error: object, *,
    uncertain_code: str, failure_state: str,
    rejection_code: str | None = None, force_uncertain: bool = False,
    returned_object_id: str | None = None,
) -> dict:
    uncertain = force_uncertain or adapter_support.is_uncertain(error)
    state = "sent_unknown" if uncertain else failure_state
    step["state"] = "sent_unknown" if uncertain else "failed"
    step["error_code"] = uncertain_code if uncertain else rejection_code
    step["private_diagnostic"] = str(error)
    step = context.checkpoint_step(
        step,
        returned_object_id=step.get("returned_object_id"),
        returned_object_url=step.get("returned_object_url"),
    )
    adapter_support.replace_step(steps, step)
    return adapter_support.build_result(
        state, steps=steps, returned_object_id=returned_object_id,
        error_code=step.get("error_code"), private_diagnostic=str(error),
    )


def _number(value: object) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(number):
        return float("nan")
    return int(number) if number.is_integer() else number


def _numbers_equal(left: object, right: object) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-6)
    except (TypeError, ValueError):
        return False


__all__ = [
    "assignment_shape",
    "bridge_description",
    "bridge_matches",
    "canonical_assignment_tier",
    "canonical_family_key",
    "canonical_tier",
    "dashboard_url",
    "discover_families",
    "execute_family_tail",
    "expected_bridge",
    "normalize_base_title",
    "normalized_family_title",
    "require_family_delivery",
    "reconcile_family_tail",
    "resolve_public_tags",
    "source_title",
    "structural_digest",
]
