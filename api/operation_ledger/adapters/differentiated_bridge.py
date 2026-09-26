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


CANONICAL_TIERS = ("Support", "Core", "Accelerate")
SUPPORTED_RENDERERS = ("assignment", "quiz")
BRIDGE_SHAPE_FIELDS = (
    "name", "description", "points_possible", "assignment_group_id", "due_at",
    "grading_type", "submission_types", "published", "only_visible_to_overrides",
    "omit_from_final_grade", "post_to_sis", "overrides",
)
RECONCILIATION_FIELDS = ("name", "description", "due_at")

_FAMILY_KEYS = ("family_id", "differentiation_family_id", "canonical_family_id")
_TIER_KEYS = ("tier", "canonical_tier", "variant", "variant_label")

_DASH_VARIANTS = ("—", "–")  # em dash, en dash
_TRAILING_PARENTHETICAL_RE = re.compile(r"\s*\([^()]*\)\s*$")


class TierTagCollisionError(ValueError):
    """A tier-tag collision inside one envelope (AC5).

    Carries the colliding tier ``labels`` and the shared public ``tag`` so a
    preview boundary can return a stable ``tier_tag_collision`` refusal
    instead of a plain message.
    """

    def __init__(self, message: str, *, labels: list[str], tag: str):
        super().__init__(message)
        self.labels = list(labels)
        self.tag = tag


def _normalize_title_text(value: object) -> str:
    """Casefold, collapse whitespace, and unify dash variants (AC2)."""
    text = normalize_student_text(value or "").strip()
    for dash in _DASH_VARIANTS:
        text = text.replace(dash, "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def supported_renderers() -> tuple[str, ...]:
    """Return the renderer registry for the shared family contract."""
    return SUPPORTED_RENDERERS


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


def _public_tags(tier_tags: dict | None = None) -> list[str]:
    """Return configured public suffixes, longest first.

    Discovery is intentionally driven by Settings.  The canonical pedagogical
    labels remain useful metadata, but they are not a title vocabulary and must
    never make a one-off colour (or any other teacher tag) special.
    """
    configured = tier_tags if tier_tags is not None else config.get_tier_tags()
    values = []
    for value in (configured or {}).values():
        text = str(value or "").strip()
        if text and text.casefold() not in {item.casefold() for item in values}:
            values.append(text)
    return sorted(values, key=lambda value: (-len(value), value.casefold()))


def title_tag_parts(value: object, tier_tags: dict | None = None) -> tuple[str, str | None]:
    """Split an exact configured ``Base - <tag>`` title.

    Matching normalizes casefold, collapsed whitespace, and dash variants
    (``—``/``–``/``-``) before the suffix comparison (AC2). This is
    candidate discovery only.  Callers still have to prove the live
    assignment structure before treating the result as a source or bridge.
    """
    title = _normalize_title_text(value)
    folded = title.casefold()
    for tag in _public_tags(tier_tags):
        suffix = f" - {tag}".casefold()
        if folded.endswith(suffix) and len(title) > len(suffix):
            return title[:len(title) - len(suffix)].rstrip(), tag
    return title, None


def normalized_family_title(value: object, tier_tags: dict | None = None) -> str:
    """Normalize a candidate title using configured tags and the bridge suffix.

    Strips at most one trailing configured tier tag or the legacy
    ``- Bridge`` suffix, then ignores at most one trailing parenthetical
    (for example ``(Paper)``) so a source-only parenthetical does not split
    an otherwise identical family (AC2).
    """
    title = _normalize_title_text(value)
    if title.casefold().endswith(" - bridge"):
        base = title[:-len(" - bridge")].rstrip()
    else:
        base = title_tag_parts(title, tier_tags)[0].strip()
    return _TRAILING_PARENTHETICAL_RE.sub("", base).strip() or base


def discover_families(
    assignments: list[dict], registrations: list[dict] | None = None,
    tier_tags: dict | None = None,
) -> list[dict]:
    """Return student-free CE-owned family candidates from assignment metadata.

    Stable family/tier metadata wins. Title normalization is used only when an
    assignment has no stable family metadata at all.
    """
    registrations = [row for row in (registrations or []) if isinstance(row, dict)]
    by_source_id = {
        str(value): row
        for row in registrations
        for value in (row.get("source_assignment_ids") or [])
        if str(value).strip()
    }
    by_bridge_id = {
        str(row.get("bridge_assignment_id")): row
        for row in registrations
        if str(row.get("bridge_assignment_id") or "").strip()
    }
    groups: dict[tuple[str, str], list[dict]] = {}
    for assignment in assignments or []:
        if not isinstance(assignment, dict) or not assignment.get("id"):
            continue
        assignment_id = str(assignment.get("id"))
        registration = by_source_id.get(assignment_id) or by_bridge_id.get(assignment_id)
        family_key = canonical_family_key(assignment)
        tier = canonical_assignment_tier(assignment)
        base_title, public_tag = title_tag_parts(assignment.get("name"), tier_tags)
        # Ignore at most one trailing parenthetical (e.g. "(Paper)") for
        # grouping/display so a source-only parenthetical does not split an
        # otherwise identical family from its unsuffixed bridge (AC2).
        base_title = _TRAILING_PARENTHETICAL_RE.sub("", base_title).strip() or base_title
        identity = "metadata"
        is_bridge = (
            _metadata_value(assignment, ("bridge", "is_bridge", "bridge_assignment")) is True
            or str(assignment.get("name") or "").strip().casefold().endswith(" - bridge")
        )
        if registration:
            family_key = str(registration.get("family_key") or registration.get("family_title") or "").strip()
            base_title = str(registration.get("family_title") or base_title).strip()
            tier = tier or public_tag or ("registered" if is_bridge else "registered_source")
            identity = "family_link"
        if not family_key or not tier:
            if family_key and is_bridge:
                tier = "registered"
                copy_row = dict(assignment)
                copy_row["_family_key_display"] = family_key
                copy_row["_family_base_title"] = base_title
                copy_row["_discovery_tier"] = tier
                copy_row["_discovery_bridge"] = True
                groups.setdefault((family_key.casefold(), identity), []).append(copy_row)
                continue
            family_key = normalized_family_title(assignment.get("name"), tier_tags) if is_bridge else base_title
            # An exact unsuffixed title is a structural candidate source, not
            # a bridge. The reconciliation service decides whether its live
            # shape is acceptable; discovery must not discard or reclassify it.
            tier = public_tag or ("unsuffixed" if not is_bridge else "unknown")
            identity = "title_fallback"
        if not family_key or (tier == "unknown" and not is_bridge):
            continue
        copy_row = dict(assignment)
        copy_row["_family_key_display"] = family_key
        copy_row["_family_base_title"] = base_title
        copy_row["_discovery_tier"] = tier
        copy_row["_discovery_bridge"] = is_bridge
        groups.setdefault((family_key.casefold(), identity), []).append(copy_row)
    result = []
    for (family_key, identity), rows in sorted(groups.items(), key=lambda item: item[0]):
        tiers = {}
        bridges = []
        for row in rows:
            tier = canonical_assignment_tier(row)
            if not tier and row.get("_discovery_tier") not in (None, "unknown", "registered"):
                tier = str(row["_discovery_tier"])
            if tier and not row.get("_discovery_bridge"):
                tiers.setdefault(tier, []).append(row)
            marker = _metadata_value(row, ("bridge", "is_bridge", "bridge_assignment"))
            if row.get("_discovery_bridge") or marker is True or str(row.get("name") or "").strip().casefold().endswith(" - bridge"):
                bridges.append(row)
        source_rows = [row for values in tiers.values() for row in values]
        registration = next((r for r in registrations if str(r.get("family_key") or r.get("family_title") or "").casefold() == str(family_key).casefold()), None)
        family_title = str((registration or {}).get("family_title") or rows[0].get("_family_base_title") or normalized_family_title(rows[0].get("name"), tier_tags)).strip()
        display_family_key = str((registration or {}).get("family_key") or rows[0].get("_family_key_display") or family_key).strip()
        result.append({
            "family_key": display_family_key,
            "family_title": family_title,
            "identity_source": "family_link" if registration else identity,
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
        ", ".join(CANONICAL_TIERS) + "."
    )


def resolve_public_tags(labels: list[object], minimum_tiers: int = 2) -> list[dict]:
    if len(labels) < minimum_tiers:
        raise ValueError(f"Differentiated delivery requires at least {minimum_tiers} tier{'s' if minimum_tiers != 1 else ''}")
    configured = config.get_tier_tags()
    resolved = []
    seen_tags: dict[str, str] = {}
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
            # AC5: two tiers resolving to the same public tag inside one
            # envelope is a stable, structured refusal, not a bare message.
            # The same tag reused in a different family is not a collision;
            # only reuse inside this one envelope is.
            raise TierTagCollisionError(
                "Public Canvas tags for the used tiers must be unique within the "
                "envelope after trimming and case-folding; update them in Settings.",
                labels=[seen_tags[tag_key], tier],
                tag=tag,
            )
        seen_tiers.add(tier)
        seen_tags[tag_key] = tier
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


def require_family_delivery(
    due_at: object,
    module_name: object = "",
    module_id: object = "",
    *,
    require_exact_module: bool = False,
    create_module: bool = False,
) -> tuple[str | None, str, str | None]:
    due_text = str(due_at or "").strip()
    module_text = str(module_name or "").strip()
    module_id_text = str(module_id or "").strip()
    if require_exact_module:
        if module_id_text and create_module:
            raise ValueError("module_id and create_module cannot be used together")
        if not module_id_text and not (create_module and module_text):
            raise ValueError(
                "Differentiated AssignmentForge delivery requires module_id or "
                "create_module=true with a non-empty module_name"
            )
    elif not module_text and not module_id_text:
        raise ValueError("Differentiated delivery requires module_id or module_name")
    if not due_text:
        return None, module_text, None
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
        "assignment visible in the gradebook (the source assignments are in the selected module). Open your "
        f'<a href="{url}">Canvas Dashboard</a> and complete the configured-tag-suffixed '
        "version your teacher assigned to you.</p>"
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

    # Attach and verify every source before creating or repairing the bridge.
    # This ordering means a legacy bridge item can never be removed or
    # replaced before the source navigation is complete.
    module_id = str(payload.get("module_id") or "").strip() or None
    module_name = str(payload.get("module_name") or "").strip()
    for index, (source_id, source_title) in enumerate(zip(source_ids, source_titles)):
        module_result = attach_assignment_type_module_item(
            course_id=course_id,
            content_id=str(source_id),
            title=source_title,
            module_name=module_name,
            module_id=module_id,
            create_module=bool(payload.get("create_module")) and not module_id,
            steps=steps,
            context=context,
            attach_step_key=f"attach_source_module:{index}",
            returned_object_id=str(source_id),
            deterministic_failure_state=failure_state,
        )
        if module_result.get("state") != "applied":
            return module_result
        module_id = str(
            adapter_support.find_step(steps, f"attach_source_module:{index}").get("module_id")
            or module_id or ""
        ) or None

    layout_error = _verify_source_module_layout(course_id, source_ids, "", steps)
    if layout_error:
        return adapter_support.build_result(
            failure_state, steps=steps,
            error_code=layout_error,
        )

    create_step = adapter_support.ensure_step(steps, "create_bridge")
    bridge_id = create_step.get("returned_object_id")
    bridge_url = create_step.get("returned_object_url")
    if bridge_id:
        assignment, read_error = adapter_support.get_assignment(course_id, str(bridge_id))
        activate_state = adapter_support.find_step(steps, "activate_bridge").get("state")
        expected_active = activate_state in {"applied", "skipped"}
        if read_error or not bridge_matches(assignment or {}, family, active=expected_active):
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

    # The bridge is gradebook-only. Prove it is absent from every module after
    # source attachment and again after any exact-ID bridge adoption.
    layout_error = _verify_source_module_layout(course_id, source_ids, str(bridge_id), steps)
    if layout_error:
        return adapter_support.build_result(
            "sent_unknown", steps=steps, returned_object_id=str(bridge_id),
            error_code=layout_error,
        )

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

    # The family link is the final proof-bearing step. Re-read every required
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
    # Correction 3: record each source's current live title (a teacher may
    # have renamed it after CE's verified create), not the originally
    # pushed one. The frozen family/base title is untouched.
    live_source_titles = [str(row.get("name") or "") for row in source_rows]
    registration = {
        "family_title": family["base_title"],
        "source_assignment_ids": list(source_ids),
        "source_titles": live_source_titles,
        "bridge_assignment_id": str(bridge_id),
        "bridge_state_digest": structural_digest(state),
        "module_id": module_id,
        "module_name": module_name,
    }
    # ``register_family`` is retained as implementation-private storage
    # terminology so interrupted pilot operations remain readable. It is
    # surfaced as ``family_link`` at the agent boundary.
    register_step = adapter_support.ensure_step(steps, "register_family")
    try:
        verified = config.save_sis_grade_bridge_verified(course_id, registration)
    except Exception as exc:
        register_step["state"] = "blocked"
        register_step["error_code"] = "family_registration_failed"
        register_step["private_diagnostic"] = type(exc).__name__
        register_step = context.checkpoint_step(register_step)
        adapter_support.replace_step(steps, register_step)
        return adapter_support.build_result(
            "blocked", steps=steps, returned_object_id=str(bridge_id),
            error_code="family_link_save_failed",
        )
    if not verified:
        register_step["state"] = "blocked"
        register_step["error_code"] = "family_registration_unverified"
        register_step = context.checkpoint_step(register_step)
        adapter_support.replace_step(steps, register_step)
        return adapter_support.build_result(
            "blocked", steps=steps, returned_object_id=str(bridge_id),
            error_code="family_link_unverified",
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
    family, rows, error = _verified_family_sources(
        course_id, payload, source_ids, source_titles
    )
    if error:
        return {"state": "sent_unknown", "steps": projected}
    live_source_titles = [str(row.get("name") or "") for row in rows]
    by_key = {str(step.get("step_key") or ""): step for step in stored_steps}
    create_step = by_key.get("create_bridge") or {}
    bridge_id = str(create_step.get("returned_object_id") or "")
    if not bridge_id:
        return {"state": _unfinished_state(create_step), "steps": projected}
    bridge, read_error = adapter_support.get_assignment(course_id, bridge_id)
    if read_error or not bridge_matches(bridge or {}, family, active=True):
        return {"state": "sent_unknown", "steps": projected}
    projected.append(_safe_step(create_step, bridge.get("html_url")))

    source_module_steps = []
    for index, source_id in enumerate(source_ids):
        attach_step = by_key.get(f"attach_source_module:{index}") or {}
        if not attach_step.get("returned_object_id") or not attach_step.get("module_id"):
            return {"state": _unfinished_state(attach_step), "steps": projected}
        item, item_error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/modules/{attach_step['module_id']}/items/{attach_step['returned_object_id']}"
        )
        if item_error or not isinstance(item, dict) or str(item.get("content_id") or "") != str(source_id):
            return {"state": "sent_unknown", "steps": projected}
        source_module_steps.append(_safe_step(attach_step))
    module_step = by_key.get("create_module")
    if module_step:
        projected.append(_safe_step(module_step))
    projected.extend(source_module_steps)

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
        "source_titles": live_source_titles,
        "bridge_assignment_id": bridge_id,
        "bridge_state_digest": structural_digest(assignment_shape(bridge, [])),
        "module_id": payload.get("module_id") or (source_module_steps[0].get("module_id") if source_module_steps else None),
        "module_name": payload.get("module_name") or None,
    }
    register_step = by_key.get("register_family") or {}
    if registration != config.normalize_sis_grade_bridge(expected_registration):
        return {"state": "sent_unknown", "steps": projected}
    projected.append(_safe_step(register_step))
    return {
        "state": "applied",
        "steps": projected,
        "returned_object_id": bridge_id,
        "returned_object_url": bridge.get("html_url"),
    }


def _verify_source_module_layout(
    course_id: str, source_ids: list[str], bridge_id: str,
    steps: list[dict],
) -> str | None:
    """Re-read every module and prove exact source/bridge placement laws."""
    module_ids = {
        str(adapter_support.find_step(steps, f"attach_source_module:{index}").get("module_id") or "")
        for index in range(len(source_ids))
    }
    module_ids.discard("")
    if len(module_ids) != 1:
        return "source_module_identity_unverified"
    module_id = next(iter(module_ids))
    modules, error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/modules", {"per_page": 100}
    )
    if error:
        return "source_module_layout_unverified"
    source_set = {str(value) for value in source_ids}
    occurrences = {source_id: [] for source_id in source_set}
    bridge_occurrences = []
    for module in modules or []:
        current_module_id = str(module.get("id") or "")
        if not current_module_id:
            return "source_module_layout_unverified"
        items, item_error = canvas_client.canvas_get_all(
            f"/api/v1/courses/{course_id}/modules/{current_module_id}/items",
            {"per_page": 100},
        )
        if item_error:
            return "source_module_layout_unverified"
        for item in items or []:
            content_id = str(item.get("content_id") or "")
            if content_id in occurrences:
                occurrences[content_id].append((current_module_id, str(item.get("id") or "")))
            if bridge_id and content_id == str(bridge_id):
                bridge_occurrences.append((current_module_id, str(item.get("id") or "")))
    if any(
        len(found) != 1 or found[0][0] != module_id
        for found in occurrences.values()
    ):
        return "source_module_membership_invalid"
    if bridge_occurrences:
        return "bridge_module_item_present"
    return None


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
        # Teacher edits to source titles, due dates, and overrides are
        # authoritative after Canvas Expert verifies its own create.
        expected = {
            "published": True,
            "omit_from_final_grade": True,
            "post_to_sis": False,
            "grading_type": "points",
        }
        if not _fields_match(assignment, expected):
            return {}, rows, "source_final_shape_unverified"
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
        elif key == "due_at":
            if not _timestamps_equal(current, value):
                return False
        elif current != value:
            return False
    return True


def _timestamps_equal(current: object, expected: object) -> bool:
    """Compare Canvas ISO timestamps by instant, not serialization spelling."""
    if current == expected:
        return True
    if not isinstance(current, str) or not isinstance(expected, str):
        return False
    try:
        left = datetime.fromisoformat(current.replace("Z", "+00:00"))
        right = datetime.fromisoformat(expected.replace("Z", "+00:00"))
    except ValueError:
        return False
    return left == right


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
        canvas_message=adapter_support.canvas_message_from_error(error),
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
    "supported_renderers",
    "source_title",
    "structural_digest",
    "TierTagCollisionError",
]
