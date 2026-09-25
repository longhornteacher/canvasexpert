"""AssignmentForge adapter for the crash-safe ``content.assignment`` kind.

Parse and safely apply whole-class or differentiated assignments.

Excluded from this adapter:
- Rubric association (slice 11c1).
- Placeholder resolution.
"""
import requests
from pathlib import Path
import mimetypes

from engine.rendering.forge.canvas_html import render_assignment
from engine.rendering.forge.printable import render_assignment_printable
from engine.rendering.physical.emit_pdf import html_to_pdf
from engine.utils.text_utils import safe_filename_component

from .. import models
from . import assignment_tiered, assignment_whole, differentiated_bridge
from . import forge_files
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
from api.student_text import normalize_author_model, normalize_student_text
from api.webui import af


KIND = "content.assignment"

_RENDER_FIELDS = (
    "title", "points", "submission", "overview", "directions", "sections",
    "rubric", "supports", "extras", "unit_info",
)


def _render_model(data: dict) -> dict:
    return {key: normalize_author_model(data[key]) for key in _RENDER_FIELDS if key in data}


def _generate_printable(model: dict, *, palette_key: str, public_tag: str | None,
                        tier: str | None, tracked: bool, attachment_labels: list[str], sub_folder: str,
                        filename: str) -> dict:
    from api import runtime_paths

    try:
        root = Path(runtime_paths.printables_dir()).resolve()
        output_dir = (root / sub_folder).resolve()
        output_dir.relative_to(root)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = (output_dir / filename).resolve()
        output.relative_to(root)
        # A stale artifact at the canonical name must never survive a failed render.
        output.unlink(missing_ok=True)
        printable_html = render_assignment_printable(
            model, palette_key=palette_key, public_tag=public_tag,
            tracked=tracked, attachment_labels=attachment_labels,
        )
        html_to_pdf(printable_html, "", str(output))
        if not output.is_file():
            raise RuntimeError("PDF renderer did not create an output file")
        return {
            "available": True, "path": str(output), "filename": filename,
            "sha256": forge_files.sha256_file(output),
            "palette_key": palette_key, "tier": tier,
            "tag": public_tag, "label": public_tag, "public_tag": public_tag,
        }
    except Exception:
        return {
            "available": False, "filename": filename,
            "palette_key": palette_key, "tier": tier,
            "tag": public_tag, "label": public_tag, "public_tag": public_tag,
            "warning": "printable_unavailable",
        }


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

        authored_tiers = data.get("tiers") or []
        attachments = forge_files.resolve_attachments(data.get("attachments") or [])
        attachment_slots = [
            {"href": forge_files.link_slot("attachment", index), "label": row["label"]}
            for index, row in enumerate(attachments)
        ]
        model = _render_model(data)
        tier_colors = config.get_tier_colors()
        model["title"] = name
        assignment_group = str(prepare_request.get("assignment_group_name") or "").strip()
        sub_fields = af.submission_fields(data)
        tracked = (
            sub_fields.get("submission_types") == ["online_upload"]
            and sub_fields.get("allowed_extensions") == ["docx"]
        )
        external_tool = sub_fields.get("submission_types") == ["external_tool"]
        title_part = safe_filename_component(name)
        tiers = []
        printables = []
        if authored_tiers:
            tags = differentiated_bridge.resolve_public_tags(
                [row["label"] for row in authored_tiers]
            )
            base_title = differentiated_bridge.normalize_base_title(name)
            for authored, resolved in zip(authored_tiers, tags):
                tier_model = normalize_author_model(model)
                for field in ("overview", "directions"):
                    if field in authored:
                        tier_model[field] = normalize_author_model(authored[field])
                if "supports" in authored:
                    tier_model["tier_supports"] = normalize_author_model(authored["supports"])
                printable = None
                if not external_tool:
                    tag_part = safe_filename_component(resolved["tag"])
                    filename = f"{title_part} - {tag_part} - Printable.pdf"
                    printable = _generate_printable(
                        tier_model,
                        palette_key=tier_colors[resolved["tier"]],
                        public_tag=resolved["tag"], tier=resolved["tier"], tracked=tracked,
                        attachment_labels=[row["label"] for row in attachments],
                        sub_folder=title_part, filename=filename,
                    )
                    printables.append(printable)
                tiers.append({
                    "label": resolved["tier"],
                    **resolved,
                    "title": differentiated_bridge.source_title(base_title, resolved["tag"]),
                    "description": render_assignment(
                        tier_model,
                        palette_key=tier_colors[resolved["tier"]],
                        tier=resolved["tier"],
                        public_tag=resolved["tag"],
                        assignment_group=assignment_group,
                        printable_link=(forge_files.link_slot("printable", len(printables) - 1)
                                        if printable and printable["available"] else None),
                        attachment_slots=attachment_slots,
                    ),
                    "printable": printable,
                })
            description = tiers[0]["description"]
        else:
            printable = None
            if not external_tool:
                filename = f"{title_part} - Printable.pdf"
                printable = _generate_printable(
                    model, palette_key=tier_colors["untiered"], public_tag=None,
                    tier=None, tracked=tracked,
                    attachment_labels=[row["label"] for row in attachments],
                    sub_folder=title_part, filename=filename,
                )
                printables.append(printable)
            description = render_assignment(
                model,
                palette_key=tier_colors["untiered"],
                tier=None,
                public_tag="",
                assignment_group=assignment_group,
                printable_link=(forge_files.link_slot("printable", 0)
                                if printable and printable["available"] else None),
                attachment_slots=attachment_slots,
            )

        points = data.get("points")
        payload = {
            "name": name,
            "description": description,
            "points": float(points),
            "submission_types": sub_fields.get("submission_types", ["online_text_entry"]),
            "published": bool(prepare_request.get("published")),
            "post_to_sis": bool(prepare_request.get("post_to_sis")),
            "source_path": path,
            "attachments": attachments,
            "printables": printables,
        }
        if tiers:
            payload["tiers"] = tiers
            payload["base_title"] = base_title
            payload["unrestricted_tiers"] = True
            if prepare_request.get("post_to_sis") is True:
                raise ValueError(
                    "tiered AssignmentForge sources are SIS-disabled; omit post_to_sis"
                )
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

        if assignment_group:
            payload["assignment_group_name"] = assignment_group

        # Module placement
        mod_name = prepare_request.get("module_name")
        if mod_name:
            payload["module_name"] = str(mod_name).strip()
        mod_id = prepare_request.get("module_id")
        if mod_id:
            payload["module_id"] = str(mod_id).strip()
        if prepare_request.get("create_module"):
            payload["create_module"] = True

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
            "attachments": payload.get("attachments"),
            "printables": payload.get("printables"),
            "module_name": payload.get("module_name"),
            "module_id": payload.get("module_id"),
            "create_module": payload.get("create_module"),
            "tiers": payload.get("tiers"),
            "base_title": payload.get("base_title"),
            "corrections": payload.get("corrections"),
            "unrestricted_tiers": payload.get("unrestricted_tiers"),
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
            if payload.get("assignment_group_name"):
                assignment_group_id = _find_assignment_group(
                    course_id, payload["assignment_group_name"]
                )
                if assignment_group_id is None:
                    return {"blocking_error": "assignment_group_not_found"}
                payload["assignment_group_id"] = assignment_group_id
            due_at, module_name, bridge_due_at = differentiated_bridge.require_family_delivery(
                payload.get("due_at"), payload.get("module_name"), payload.get("module_id"),
                require_exact_module=True,
                create_module=bool(payload.get("create_module")),
            )
            payload["due_at"] = due_at
            payload["module_name"] = module_name
            payload["bridge_due_at"] = bridge_due_at
            payload["bridge_description"] = differentiated_bridge.bridge_description()
            if payload.get("module_id"):
                module, module_error = canvas_client.canvas_get(
                    f"/api/v1/courses/{course_id}/modules/{payload['module_id']}"
                )
                if module_error or not module or str(module.get("id")) != str(payload["module_id"]):
                    return {"blocking_error": "module_exact_id_unverified"}
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
        for record in payload.get("attachments", []):
            if not _frozen_file_matches(record, attachments=True):
                return True
        for record in payload.get("printables", []):
            if record.get("available") and (
                not _frozen_file_matches(record, attachments=False)
            ):
                return True
        if payload.get("tiers"):
            if baseline is None or "canvas_error" in baseline:
                return True
            fresh = self.capture_baseline(payload, target)
            if "canvas_error" in fresh or fresh.get("blocking_error"):
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
        attachments = [{"file": row.get("file"), "label": row.get("label"),
                        "sha256": row.get("sha256")}
                       for row in payload.get("attachments", [])]
        printables = [{
            "tier": row.get("tier"), "tag": row.get("tag"),
            "label": row.get("label"), "public_tag": row.get("public_tag"),
            "filename": row.get("filename"), "available": bool(row.get("available")),
            "palette_key": row.get("palette_key"), "sha256": row.get("sha256"),
            **({"warning": "printable_unavailable"} if row.get("warning") else {}),
        } for row in payload.get("printables", [])]
        if payload.get("module_name") or payload.get("module_id") or payload.get("create_module"):
            dependencies.append({
                "type": "module",
                "name": payload.get("module_name"),
                **({"id": payload["module_id"]} if payload.get("module_id") else {}),
                **({"create": True} if payload.get("create_module") else {}),
            })
        review = {
            "course_name": course_name,
            "assignment_name": payload.get("name"),
            "points": payload.get("points"),
            "description": payload.get("description"),
            "description_preview": (payload.get("description") or "")[:120],
            "submission_types": sub,
            "published": payload.get("published"),
            "post_to_sis": payload.get("post_to_sis"),
            "due_at": payload.get("due_at"),
            "baseline_has_existing": existing is not None,
            "baseline_existing_id": existing.get("id") if existing else None,
            "baseline_existing_url": existing.get("html_url") if existing else None,
            "dependencies": dependencies,
            "attachments": attachments,
            "printables": printables,
        }
        if payload.get("tiers"):
            review.update({
                "tiered": True,
                "tier_count": len(payload["tiers"]),
                "tiers": [{
                    "label": row.get("label"),
                    "public_tag": row.get("tag"),
                    "source_title": row.get("title"),
                    "description": row.get("description"),
                    "printable": ({
                        "available": bool((row.get("printable") or {}).get("available")),
                        "filename": (row.get("printable") or {}).get("filename"),
                        "public_tag": (row.get("printable") or {}).get("public_tag"),
                        "palette_key": (row.get("printable") or {}).get("palette_key"),
                        **({"warning": "printable_unavailable"}
                           if (row.get("printable") or {}).get("warning") else {}),
                    } if row.get("printable") else None),
                } for row in payload["tiers"]],
                "tier_warning": (
                    "Canvas will create one published, unrestricted source assignment per tier "
                    "and one whole-course grade bridge. Only the sources appear in the selected module."
                ),
                "teacher_action": (
                    "Review the exact family in Canvas Live; tier placement is manual and teacher-owned. "
                    "Canvas Expert will not use Canvas Groups or perform Canvas Grade Sync."
                ),
            })
            review["published"] = True
            review["module"] = {
                "module_id": payload.get("module_id"),
                "module_name": payload.get("module_name"),
            }
            review["bridge"] = {
                "title": differentiated_bridge.bridge_title(payload.get("base_title")),
                "due_at": payload.get("bridge_due_at"),
                "post_to_sis": True,
            }
            review["baseline_has_existing"] = bool(baseline.get("existing_assignments"))
            review["baseline_existing_id"] = None
            review["baseline_existing_url"] = None
        return review

    # ── Execute ──────────────────────────────────────────────────────────

    def execute(
        self, payload: dict, target: dict, baseline: dict,
        claim: dict, context,
    ) -> dict:
        steps = _ordered_steps(target)
        attachment_infos = []
        for index, record in enumerate(payload.get("attachments", [])):
            record = {**record, "filename": record.get("file"),
                      "content_type": mimetypes.guess_type(record.get("file", ""))[0]}
            file_info, failure = forge_files.ensure_uploaded_file(
                record=record, step_key=f"upload_attachment:{index}",
                folder="Canvas Expert Attachments", course_id=target["course_id"],
                steps=steps, context=context,
                upload_file=_upload_course_file,
                get_file=_get_course_file,
            )
            if failure:
                return _build_result(
                    failure["state"], steps=steps,
                    error_code=failure.get("error_code"),
                    private_diagnostic=failure.get("private_diagnostic"),
                )
            attachment_infos.append(file_info)

        prepared_payload = dict(payload)
        if attachment_infos:
            if payload.get("tiers"):
                prepared_payload["tiers"] = [
                    {**tier, "description": forge_files.bind_link_slots(
                        tier["description"], "attachment", attachment_infos)}
                    for tier in payload["tiers"]
                ]
                prepared_payload["description"] = prepared_payload["tiers"][0]["description"]
            else:
                prepared_payload["description"] = forge_files.bind_link_slots(
                    payload.get("description", ""), "attachment", attachment_infos)
        target = {**target, "steps": steps}
        if prepared_payload.get("tiers"):
            return assignment_tiered.execute(
                prepared_payload,
                target,
                baseline,
                context,
                ordered_steps=_ordered_steps,
                find_assignment_group=_find_assignment_group,
                prepare_tier=_prepare_tier_printable,
            )
        return assignment_whole.execute(
            prepared_payload,
            target,
            context,
            ordered_steps=_ordered_steps,
            upload_course_file=_upload_course_file,
            find_assignment_group=_find_assignment_group,
            read_modules=_read_modules,
            prepare_description=_prepare_whole_printable,
        )

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, payload: dict, target: dict, baseline: dict) -> dict:
        file_steps = [step for step in target.get("steps", [])
                      if str(step.get("step_key", "")).startswith(("upload_attachment:", "upload_printable:"))
                      or step.get("step_key") == "upload_printable"]
        for step in file_steps:
            file_id = step.get("returned_object_id")
            if not file_id:
                if step.get("outbound_started_at"):
                    return {"state": "sent_unknown", "error_code": "file_upload_unresolved"}
                continue
            verified, error = _get_course_file(target["course_id"], str(file_id))
            if error or not isinstance(verified, dict) or str(verified.get("id")) != str(file_id):
                return {"state": "sent_unknown", "error_code": "file_exact_id_unverified"}
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


def _upload_course_file(course_id: str, pdf_path, *, folder="Canvas Expert Printables"):
    assignment_whole.requests = requests
    return assignment_whole.upload_course_file(
        course_id,
        pdf_path,
        allowed_roots=_allowed_printable_roots,
        folder=folder,
        validate_pdf=(folder == "Canvas Expert Printables" and Path(pdf_path).suffix.casefold() == ".pdf"),
    )


def _get_course_file(course_id: str, file_id: str):
    return canvas_client.canvas_get(f"/api/v1/courses/{course_id}/files/{file_id}")


def _frozen_file_matches(record: dict, *, attachments: bool) -> bool:
    if not forge_files.verify_private_record_path(record, attachments=attachments):
        return False
    try:
        return forge_files.sha256_file(Path(record["path"])) == record.get("sha256")
    except OSError:
        return False


def _upload_printable(*, record, step_key, course_id, steps, context):
    file_info, failure = forge_files.ensure_uploaded_file(
        record={**record, "filename": record.get("filename"), "content_type": "application/pdf"},
        step_key=step_key, folder="Canvas Expert Printables", course_id=course_id,
        steps=steps, context=context, upload_file=_upload_course_file,
        get_file=_get_course_file,
    )
    return file_info, failure


def _prepare_whole_printable(*, payload, course_id, steps, context):
    records = payload.get("printables", [])
    if not records or not records[0].get("available"):
        return payload.get("description", ""), None
    record = records[0]
    info, failure = _upload_printable(
        record=record, step_key="upload_printable", course_id=course_id,
        steps=steps, context=context,
    )
    if failure:
        return None, _build_result(
            failure["state"], steps=steps, error_code=failure.get("error_code"),
            private_diagnostic=failure.get("private_diagnostic"),
        )
    return forge_files.bind_link_slots(payload["description"], "printable", [info]), None


def _prepare_tier_printable(*, tier, tier_index, payload, course_id, steps, context):
    record = tier.get("printable")
    if not record or not record.get("available"):
        return tier.get("description", ""), None
    info, failure = _upload_printable(
        record=record, step_key=f"upload_printable:{tier_index}",
        course_id=course_id, steps=steps, context=context,
    )
    if failure:
        return None, _build_result(
            failure["state"], steps=steps, error_code=failure.get("error_code"),
            private_diagnostic=failure.get("private_diagnostic"),
        )
    # Tier-local marker indexes are the printable list index in author order.
    return forge_files.bind_link_slots(
        tier["description"], "printable", [info], start_index=tier_index,
    ), None


def _attach_to_module(
    course_id: str, assignment_id: str, name: str,
    module_name: str = "", steps: list[dict] = None, context=None,
    module_id: str | None = None, create_module: bool = False,
    *, attach_step_key: str = "attach_module",
) -> dict:
    return attach_assignment_type_module_item(
        course_id=course_id,
        content_id=assignment_id,
        title=name,
        module_name=module_name, module_id=module_id,
        create_module=create_module,
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
                return (-1, 0, 0)
            if key.startswith("upload_attachment:"):
                suffix = key.rsplit(":", 1)[-1]
                return (-3, int(suffix) if suffix.isdigit() else 999999, 0)
            if key.startswith("upload_printable:"):
                suffix = key.rsplit(":", 1)[-1]
                return (int(suffix) if suffix.isdigit() else 999999, -1, 0)
            if key == "upload_printable":
                return (-2, 0, 0)
            prefix, _, suffix = key.partition(":")
            rank = {
                "upload_attachment": -2,
                "upload_printable": -1,
                "create_tier_assignment": 0,
                "restrict_assignment": 1,
                "create_override": 2,
                "publish_assignment": 3,
            }.get(prefix, 9)
            family_rank = {
                "create_bridge": 100,
                "create_module": 101,
                "activate_bridge": 103,
                "register_family": 104,
            }.get(key)
            if key.startswith("attach_source_module:"):
                source_index = key.rsplit(":", 1)[-1]
                return (999999, 102, int(source_index) if source_index.isdigit() else 0)
            if family_rank is not None:
                return (999999, family_rank, 0)
            return (int(suffix) if suffix.isdigit() else 999999, rank, 0)
        return sorted(existing.values(), key=tier_order)
    upload_steps = sorted(
        (key for key in existing if str(key).startswith("upload_attachment:")),
        key=lambda key: int(str(key).split(":")[-1]) if str(key).split(":")[-1].isdigit() else 999999,
    )
    order = (*upload_steps, "upload_printable", "create_assignment", "create_module", "attach_module")
    return [existing[key] for key in order if key in existing]


def _read_modules(course_id: str):
    return canvas_client.canvas_get_all(
        f"/api/v1/courses/{course_id}/modules", {"per_page": 100}
    )
