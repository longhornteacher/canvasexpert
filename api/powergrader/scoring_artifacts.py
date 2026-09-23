"""Canonical two-artifact builder for assignment-scoped Scoring Sessions."""

from __future__ import annotations

import json
import os

from api import feedback_safety
from api.shared_vault import PseudonymProvisionalError
from api import feedback_scrub
from api.feedback_contract import safe as safe_filename
from api.platform_services import workspace
from api.powergrader import context, privacy
from api.feedback_artifacts import (
    _prepare_attachment_safe_bundle,
    _scrub_bundle,
    _shared_context_blob,
    oral_reading_text,
    pseudonymize_submissions,
)


def _step(steps: list[dict], step_id: str, label: str, status: str, detail: str = "") -> None:
    steps.append(privacy.privacy_step(step_id, label, status, detail))


def build_scoring_artifacts(
    *, submitted: list[dict], assignment_name: str, assignment_description: str,
    course_id: str, course_name: str, assignment_id: str, session_id: str,
    source_text: str = "", source_files_json: str = "", source_uploads=None,
    protected: set[str] | None = None,
) -> dict:
    """Build and persist the SAFE source consumed by packet paging.

    The private session is the only private copy. This builder owns the vault
    transaction and all outbound scrub/safety gates, while returning only
    identity-free aggregate facts to its caller.
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

        prepared, attachment_excluded, attachment_log, media_holds = (
            _prepare_attachment_safe_bundle(bundle, safe_dir)
        )
        safe = _scrub_bundle(prepared, vault, protected=protected)
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
                + " " + oral_reading_text(response.get("oral_reading"))
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

        stem = safe_filename(assignment_name or "assignment")
        safe_path = os.path.join(safe_dir, f"{stem}__bundle.json")
        with open(workspace.extended_path(safe_path), "w", encoding="utf-8") as handle:
            json.dump(safe, handle, indent=2, ensure_ascii=False)
        _step(steps, "safe_bundle", "Saved SAFE scoring bundle", "ok")

        attachment_only_count = sum(
            1 for row in submitted
            if not (row.get("body") or "").strip()
            and not row.get("code_files")
            and any(not a.get("ai_eligible") or a.get("download_status") != "downloaded"
                    for a in (row.get("attachments") or []))
        )
        failures = {}
        for hold in media_holds:
            who = vault.reverse(str(hold.get("pseudonym") or ""))
            if who and who.get("canvas_id"):
                failures[str(who["canvas_id"])] = {
                    "code": "media_oral_reading_hold",
                    "message": str(hold.get("message") or "Media evidence requires teacher review."),
                }
        return {
            "ok": True,
            "privacy_steps": steps,
            "privacy_artifacts": {
                "safe_folder": safe_dir,
                "safe_bundle": safe_path,
                "safe_students": len(safe.get("students") or []),
                "attachment_only_count": attachment_only_count,
                "excluded_count": len(attachment_excluded) + len(survivor_excluded),
                "media_hold_count": len(media_holds),
                "shared_context_excluded": shared_excluded,
            },
            "ai_by_uid": {}, "ai_item_by_uid": {}, "ai_failures": failures,
            "copilot_packet": None,
        }
    except PseudonymProvisionalError:
        return {"ok": False, "code": "pseudonym_provisional", "privacy_steps": steps}
    except Exception:
        return {"ok": False, "privacy_steps": steps}
