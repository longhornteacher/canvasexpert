"""AssignmentForge adapter for the crash-safe ``content.assignment`` kind.

Parse and safely apply whole-class or Canvas-group-differentiated assignments.

Excluded from this adapter:
- Rubric association (slice 11c1).
- Placeholder resolution.
"""
import requests

from .. import models
from . import assignment_tiered, assignment_whole, differentiated_bridge
from .adapter_support import (
    as_list as _as_list,
    build_result as _build_result,
    ensure_step as _ensure_step,
    find_step as _find_step,
    has_outbound_marker as _has_outbound_marker,
    is_uncertain as _is_uncertain,
    module_id_from_steps as _module_id_from_steps,
    normalize as _normalize,
    prepend_step as _step,
    replace_step as _replace_local_step,
)
from api.platform_services import canvas_client, config
from api.student_text import normalize_student_text
from api.webui import af


KIND = "content.assignment"


class AssignmentAdapter:
    kind = KIND

    # ── Payload ──────────────────────────────────────────────────────────

    def build_payload(self, prepare_request: dict) -> dict:
        path = prepare_request.get("path")
        if not path:
            raise ValueError("path is required")
        data, problems = af.parse_file(path)
        if data is None or problems:
            raise ValueError("; ".join(problems or ["unreadable file"]))
        name = normalize_student_text(data.get("title") or "").strip()
        if not name:
            raise ValueError("AssignmentForge file must have a title")

        tier_rows = af.tier_payloads(data)
        tiers = [] if not data.get("tiers") else [{
            "label": str(row.get("label") or "").strip(),
            "description": str(row.get("description") or ""),
        } for row in tier_rows]
        if tiers and prepare_request.get("printable_path"):
            raise ValueError("Printable attachments are not supported for tiered assignments")

        if tiers:
            tags = differentiated_bridge.resolve_public_tags(
                [row["label"] for row in tiers]
            )
            tiers = [
                {
                    **row,
                    **resolved,
                }
                for row, resolved in zip(
                    af.add_supports(tiers, data.get("supports"), tags), tags
                )
            ]
            base_title = differentiated_bridge.normalize_base_title(name)
            for row in tiers:
                row["title"] = differentiated_bridge.source_title(
                    base_title, row["tag"]
                )

        description = normalize_student_text(data.get("description") or "")
        if af.PLACEHOLDER_RE.search(description):
            raise ValueError(
                "Course-resource placeholders ({{file:...}} / {{page:...}}) "
                "are not supported through this push path yet."
            )

        sub_fields = af.submission_fields(data)
        if sub_fields.get("_annotatable_file_name"):
            raise ValueError(
                "Student annotation files require per-course resolution "
                "and are not supported through this push path yet."
            )

        points = data.get("points")
        payload = {
            "name": name,
            "description": description,
            "points": float(points) if points is not None else None,
            "submission_types": sub_fields.get("submission_types", ["online_text_entry"]),
            "published": bool(prepare_request.get("published")),
            "post_to_sis": bool(prepare_request.get("post_to_sis")),
            "source_path": path,
        }
        if tiers:
            payload["tiers"] = tiers
            payload["base_title"] = base_title
        if data.get("supports"):
            payload["supports"] = data["supports"]
        if data.get("corrections"):
            payload["corrections"] = data["corrections"]
        if sub_fields.get("allowed_extensions"):
            payload["allowed_extensions"] = sub_fields["allowed_extensions"]
        if sub_fields.get("external_tool_tag_attributes"):
            payload["external_tool_tag_attributes"] = sub_fields["external_tool_tag_attributes"]

        for key in ("due_at", "unlock_at", "lock_at"):
            val = prepare_request.get(key)
            if val:
                payload[key] = str(val).strip()

        ag_name = prepare_request.get("assignment_group_name")
        if ag_name:
            payload["assignment_group_name"] = str(ag_name).strip()

        # Printable PDF attachment
        printable = prepare_request.get("printable_path")
        if printable:
            pdf_path, err = _validate_printable_pdf(printable)
            if err:
                raise ValueError(err)
            payload["printable_path"] = str(pdf_path)

        # Module placement
        mod_name = prepare_request.get("module_name")
        if mod_name and not tiers:
            payload["module_name"] = str(mod_name).strip()

        return payload

    def source_digest(self, payload: dict) -> str:
        keys = {
            "name": payload.get("name"),
            "description": payload.get("description"),
            "points": payload.get("points"),
            "submission_types": payload.get("submission_types"),
            "published": payload.get("published"),
            "post_to_sis": payload.get("post_to_sis"),
            "due_at": payload.get("due_at"),
            "unlock_at": payload.get("unlock_at"),
            "lock_at": payload.get("lock_at"),
            "assignment_group_name": payload.get("assignment_group_name"),
            "printable_path": payload.get("printable_path"),
            "module_name": payload.get("module_name"),
            "tiers": payload.get("tiers"),
            "base_title": payload.get("base_title"),
            "supports": payload.get("supports"),
            "corrections": payload.get("corrections"),
        }
        return models.sha256_dict(keys)

    # ── Target verification ──────────────────────────────────────────────

    def verify_targets(self, payload: dict, targets: list[dict]) -> list[dict]:
        active_ids = {str(course["id"]) for course in config.active_courses()}
        verified = []
        for target in targets:
            course_id = str(target.get("course_id") or "")
            if not course_id:
                raise ValueError("target missing course_id")
            if course_id not in active_ids:
                raise ValueError(
                    f"course {course_id} is not in active courses"
                )
            verified.append({
                "course_id": course_id,
                "target_key": self.target_key(payload, course_id),
                "idempotency_key": self.idempotency_key(payload, course_id),
            })
        return verified

    def target_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{KIND}|{self.source_digest(payload)}|{course_id}"
        )

    def idempotency_key(self, payload: dict, course_id: str) -> str:
        return models.sha256_hex(
            f"{self.source_digest(payload)}|{course_id}"
            f"|{_normalize(payload.get('name'))}"
        )

    # ── Baseline / drift ─────────────────────────────────────────────────

    def capture_baseline(self, payload: dict, target: dict) -> dict:
        course_id = target["course_id"]
        name = payload.get("name", "")
        if payload.get("tiers"):
            matches = []
            for title in [row["title"] for row in payload["tiers"]]:
                assignments, error = canvas_client.canvas_get_all(
                    f"/api/v1/courses/{course_id}/assignments",
                    {"per_page": 100, "search_term": title},
                )
                if error:
                    return {"canvas_error": "Canvas assignments could not be read."}
                matches.extend({
                    "id": str(row.get("id")),
                    "name": str(row.get("name") or ""),
                    "html_url": row.get("html_url"),
                } for row in (assignments or []) if row.get("id") is not None
                    and _normalize(row.get("name")) == _normalize(title))
            return {"existing_assignments": matches}

        baseline = {"existing_assignment": None}
        assignments, error = canvas_client.canvas_get(
            f"/api/v1/courses/{course_id}/assignments",
            params={"per_page": 100, "search_term": name},
        )
        if error:
            baseline["canvas_error"] = error
            return baseline
        for assignment in _as_list(assignments):
            if _normalize(assignment.get("name")) == _normalize(name):
                baseline["existing_assignment"] = {
                    "id": str(assignment.get("id")),
                    "name": assignment.get("name"),
                    "points_possible": assignment.get("points_possible"),
                    "published": assignment.get("published"),
                    "html_url": assignment.get("html_url"),
                }
                break
        return baseline

    def check_drift(self, payload: dict, target: dict, baseline: dict) -> bool:
        if payload.get("tiers"):
            if baseline is None or "canvas_error" in baseline:
                return True
            fresh = self.capture_baseline(payload, target)
            if "canvas_error" in fresh:
                return True
            known = {
                str(step.get("returned_object_id"))
                for step in target.get("steps", [])
                if (
                    str(step.get("step_key", "")).startswith("create_tier_assignment:")
                )
                and step.get("returned_object_id") is not None
            }
            current = {row["id"] for row in fresh.get("existing_assignments", [])}
            return bool(current - known)
        if baseline is None:
            return False
        if "canvas_error" in baseline:
            return True
        existing = baseline.get("existing_assignment")
        if existing is None:
            return False
        return True

    # ── Review ───────────────────────────────────────────────────────────

    def freeze_review(self, payload: dict, target: dict, baseline: dict) -> dict:
        course_id = target["course_id"]
        course_name = course_id
        for course in config.active_courses():
            if str(course["id"]) == str(course_id):
                course_name = (
                    course.get("name")
                    or course.get("nickname")
                    or course_id
                )
                break
        existing = baseline.get("existing_assignment")
        sub = payload.get("submission_types", [])
        dependencies = []
        if payload.get("printable_path"):
            dependencies.append({
                "type": "printable",
                "path": str(payload["printable_path"]),
            })
        if payload.get("module_name"):
            dependencies.append({
                "type": "module",
                "name": payload["module_name"],
            })
        review = {
            "course_name": course_name,
            "assignment_name": payload.get("name"),
            "points": payload.get("points"),
            "description_preview": (payload.get("description") or "")[:120],
            "submission_types": sub,
            "published": payload.get("published"),
            "post_to_sis": payload.get("post_to_sis"),
            "due_at": payload.get("due_at"),
            "baseline_has_existing": existing is not None,
            "baseline_existing_id": existing.get("id") if existing else None,
            "baseline_existing_url": existing.get("html_url") if existing else None,
            "dependencies": dependencies,
        }
        if payload.get("tiers"):
            review.update({
                "tiered": True,
                "tier_count": len(payload["tiers"]),
                "tiers": [{
                    "label": row.get("label"),
                    "public_tag": row.get("tag"),
                    "source_title": row.get("title"),
                } for row in payload["tiers"]],
                "tier_warning": (
                    "Canvas will create one unpublished, unrestricted assignment draft "
                    "per tier. The teacher assigns students, groups, or pods and publishes each draft in Canvas."
                ),
                "teacher_action": (
                    "Teacher action: in Canvas, assign each draft to the intended students, groups, or pods, "
                    "then publish the drafts."
                ),
            })
            review["published"] = False
            review["baseline_has_existing"] = bool(baseline.get("existing_assignments"))
            review["baseline_existing_id"] = None
            review["baseline_existing_url"] = None
        return review

    # ── Execute ──────────────────────────────────────────────────────────

    def execute(
        self, payload: dict, target: dict, baseline: dict,
        claim: dict, context,
    ) -> dict:
        if payload.get("tiers"):
            return assignment_tiered.execute(
                payload,
                target,
                baseline,
                context,
                ordered_steps=_ordered_steps,
                find_assignment_group=_find_assignment_group,
            )
        return assignment_whole.execute(
            payload,
            target,
            context,
            ordered_steps=_ordered_steps,
            upload_course_file=_upload_course_file,
            file_link_html=_file_link_html,
            find_assignment_group=_find_assignment_group,
            read_modules=_read_modules,
        )

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        if payload.get("tiers"):
            return assignment_tiered.reconcile(
                payload,
                target,
                ordered_steps=_ordered_steps,
            )
        return assignment_whole.reconcile(
            payload,
            target,
            ordered_steps=_ordered_steps,
        )

    # ── Retry / reversal ─────────────────────────────────────────────────

    def retry_selector(self, operation: dict) -> list[dict]:
        return [
            target
            for target in operation.get("targets", [])
            if models.is_unresolved_target_state(
                target.get("state", "pending")
            )
        ]


# ── Module-level helpers ─────────────────────────────────────────────────


def _find_assignment_group(course_id: str, name: str) -> int | None:
    data, error = canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/assignment_groups",
        {"per_page": 100},
    )
    if error:
        return None
    for group in data or []:
        if _normalize(group.get("name")) == _normalize(name):
            group_id = group.get("id")
            return int(group_id) if group_id is not None else None
    return None


def _allowed_printable_roots():
    return assignment_whole.allowed_printable_roots()


def _validate_printable_pdf(pdf_path: str) -> tuple:
    return assignment_whole.validate_printable_pdf(
        pdf_path,
        allowed_roots=_allowed_printable_roots,
    )

def _upload_course_file(course_id: str, pdf_path):
    assignment_whole.requests = requests
    return assignment_whole.upload_course_file(
        course_id,
        pdf_path,
        allowed_roots=_allowed_printable_roots,
    )


_file_link_html = assignment_whole.file_link_html


def _attach_to_module(
    course_id: str, assignment_id: str, name: str,
    module_name: str, steps: list[dict], context,
    *, attach_step_key: str = "attach_module",
) -> dict:
    return attach_assignment_type_module_item(
        course_id=course_id,
        content_id=assignment_id,
        title=name,
        module_name=module_name,
        steps=steps,
        context=context,
        attach_step_key=attach_step_key,
        returned_object_id=assignment_id,
        deterministic_failure_state="failed",
    )


def _ordered_steps(target: dict) -> list[dict]:
    existing = {
        step.get("step_key"): step
        for step in target.get("steps", [])
    }
    if any(":" in str(key) for key in existing):
        def tier_order(step):
            key = str(step.get("step_key") or "")
            if key == "create_module":
                return (-1, 0)
            prefix, _, suffix = key.partition(":")
            rank = {
                "create_tier_assignment": 0,
            }.get(prefix, 9)
            return (int(suffix) if suffix.isdigit() else 999999, rank)
        return sorted(existing.values(), key=tier_order)
    order = ("create_assignment", "create_module", "attach_module")
    return [existing[key] for key in order if key in existing]


def _read_modules(course_id: str):
    return canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/modules", {"per_page": 100}
    )
