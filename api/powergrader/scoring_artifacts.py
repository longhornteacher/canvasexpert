"""Canonical two-artifact builder for assignment-scoped Scoring Sessions."""

from __future__ import annotations

import copy
import json
import logging
import os

from api import feedback_safety
from api.shared_vault import PseudonymProvisionalError
from api import feedback_scrub
from api.feedback_contract import safe as safe_filename
from api.platform_services import workspace
from api.powergrader import context, privacy
from api.feedback_artifacts import (
    _scrub_bundle,
    _shared_context_blob,
    pseudonymize_submissions,
)


def _step(steps: list[dict], step_id: str, label: str, status: str, detail: str = "") -> None:
    steps.append(privacy.privacy_step(step_id, label, status, detail))


def build_scoring_artifacts(
    *, submitted: list[dict], assignment_name: str, assignment_description: str,
    course_id: str, course_name: str, assignment_id: str, session_id: str,
    source_text: str = "", source_files_json: str = "", source_uploads=None,
    protected: set[str] | None = None,
    base_bundle: dict | None = None, drop_canvas_ids=(), file_label: str = "",
) -> dict:
    """Build and persist the SAFE source consumed by packet paging.

    The private session is the only private copy. This builder owns the vault
    transaction and all outbound scrub/safety gates, while returning only
    identity-free aggregate facts to its caller.

    ``base_bundle`` is the refresh path: ``submitted`` holds only the new rows,
    which pass the same pseudonymize/scrub/safety gates and are appended after
    the base bundle's surviving students. Students named by ``drop_canvas_ids``
    are removed from the base first (their replacement is appended). The whole
    merged bundle is scanned again, and ``file_label`` gives the merged result
    its own file so the earlier bundle file is never overwritten.
    """
    steps: list[dict] = []
    try:
        workspace.ensure_workspace()
        source_context = context.build_source_context(
            source_text, source_files_json, source_uploads, strict=True
        )
        if source_context.get("materials"):
            _step(steps, "source_context", "Loaded shared source material", "ok")
        vault = context.vault()
        with vault.transaction():
            bundle = pseudonymize_submissions(submitted, vault, assignment_name)
            bundle = context.apply_shared_context(bundle, assignment_description, source_context)
            # Freeze attempt identity from the submission used to create this
            # bundle before consulting the independently published evidence index.
            rows_by_uid = {str(row.get("user_id") or ""): row for row in submitted}
            submission_attachments = {}
            for safe_student in bundle.get("students") or []:
                reverse = vault.reverse(str(safe_student.get("pseudonym") or "")) or {}
                source = rows_by_uid.get(str(reverse.get("canvas_id") or ""), {})
                for response in safe_student.get("responses") or []:
                    response.setdefault("attempt", source.get("attempt"))
                    response.setdefault("submitted_at", source.get("submitted_at") or None)
                    response["_needs_speedgrader"] = any(
                        "upload" in str(item.get("type") or "").lower().replace("_", "-")
                        or (str(item.get("type") or "").lower() != "essay"
                            and item.get("earned_score") is None)
                        for item in source.get("new_quiz_items") or []
                    )
                    response["_media_recording"] = any(
                        item.get("media_recording") for item in source.get("attachments") or []
                    )
                    submission_attachments[(str(safe_student.get("pseudonym") or ""),
                                            response.get("attempt"))] = [
                        item for item in source.get("attachments") or [] if isinstance(item, dict)
                    ]
            # One decision owner merges complete extracted text and marks holds.
            try:
                from api.mirror import evidence_scoring
                from api.platform_services import config as _config
                evidence_result = evidence_scoring.merge_into_bundle(
                    bundle, course_id=course_id, assignment_id=assignment_id,
                    workspace_root=workspace.workspace_root(),
                    canvas_base=_config.get_canvas_base(),
                    submission_attachments=submission_attachments)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Scoring evidence decision failed; bundle build refused.")
                return {"ok": False, "privacy_steps": steps}
            if not evidence_result.get("available"):
                logging.getLogger(__name__).warning(
                    "Scoring evidence index unavailable; file-bearing responses are held.")
            for safe_student in bundle.get("students") or []:
                safe_student.pop("local_attachments", None)
                safe_student.pop("_expected_attachment_count", None)
                for response in safe_student.get("responses") or []:
                    response.pop("_needs_speedgrader", None)
                    response.pop("_media_recording", None)
            verdict = feedback_safety.scan_payload(bundle, vault) if bundle.get("students") else None
        if not bundle.get("students"):
            _step(steps, "pseudonymize", "Prepared eligible responses", "warn",
                  "No submitted responses were eligible for the SAFE packet.")
        else:
            _step(steps, "pseudonymize", "Assigned pseudonyms and separated identities", "ok")
            _step(steps, "safety_scan", "Checked pseudonymized response data",
                  "warn" if not verdict["green"] else "ok")

        safe_dir, _private_dir = privacy.feedback_artifact_dirs(
            course_name=course_name or course_id, course_id=course_id,
            assignment_name=assignment_name, assignment_id=assignment_id,
            mode="scoring-session",
        )
        if not safe_dir:
            return {"ok": False, "privacy_steps": steps}
        os.makedirs(workspace.extended_path(safe_dir), exist_ok=True)

        safe = _scrub_bundle(bundle, vault, protected=protected)
        receipt = feedback_safety.assert_scrubbed(safe, vault)
        if not receipt["green"]:
            _step(steps, "safety_gate", "Validated SAFE bundle", "error")
            return {"ok": False, "privacy_steps": steps}
        _step(steps, "safety_gate", "Validated SAFE bundle", "ok")

        clean_students = []
        survivor_excluded: list[str] = []
        for student in safe.get("students", []):
            blob = " ".join(
                " ".join((response.get("prompt") or "", response.get("response") or ""))
                for response in student.get("responses", [])
            )
            survivors = feedback_scrub.verify_clean(blob, vault)
            if survivors:
                survivor_excluded.append(str(student.get("pseudonym") or ""))
            else:
                clean_students.append(student)
        safe["students"] = clean_students
        shared_excluded = False
        shared_blob = _shared_context_blob(safe.get("shared_context"))
        if shared_blob:
            if feedback_scrub.verify_clean(shared_blob, vault):
                safe.pop("shared_context", None)
                shared_excluded = True
        appended = [str(student.get("pseudonym") or "") for student in clean_students]

        if base_bundle is not None:
            dropped = {str(entry.get("pseudonym") or "") for entry in vault.entries()
                       if str(entry.get("canvas_id")) in {str(v) for v in drop_canvas_ids}}
            merged = copy.deepcopy(base_bundle)
            merged["students"] = [
                student for student in merged.get("students") or []
                if str(student.get("pseudonym") or "") not in dropped
            ] + clean_students
            safe = merged
            shared_excluded = "shared_context" not in safe
            receipt = feedback_safety.assert_scrubbed(safe, vault)
            if not receipt["green"]:
                _step(steps, "safety_gate", "Validated merged SAFE bundle", "error")
                return {"ok": False, "privacy_steps": steps}

        stem = safe_filename(assignment_name or "assignment")
        label = f"-{safe_filename(file_label)}" if file_label else ""
        safe_path = os.path.join(safe_dir, f"{stem}__bundle{label}.json")
        with open(workspace.extended_path(safe_path), "w", encoding="utf-8") as handle:
            json.dump(safe, handle, indent=2, ensure_ascii=False)
        _step(steps, "safe_bundle", "Saved SAFE scoring bundle", "ok")

        return {
            "ok": True,
            "privacy_steps": steps,
            "privacy_artifacts": {
                "safe_folder": safe_dir,
                "safe_bundle": safe_path,
                "safe_students": len(safe.get("students") or []),
                "excluded_count": len(survivor_excluded),
                "shared_context_excluded": shared_excluded,
            },
            "ai_by_uid": {}, "ai_item_by_uid": {}, "ai_failures": {},
            "appended_pseudonyms": appended,
        }
    except PseudonymProvisionalError:
        return {"ok": False, "code": "pseudonym_provisional", "privacy_steps": steps}
    except Exception:
        return {"ok": False, "privacy_steps": steps}
