"""Feedback tools bundle and artifact writing helpers."""
import json
import os

from api.nq_report import constructed_responses, html_to_text
from api.feedback_vault import IdentityVault
from api import feedback_scrub
from api.mirror import new_quizzes
from api.powergrader import student_attachments, writing_timeline
from api.platform_services import workspace
from api.feedback_contract import (
    CONTRACT_VERSION,
    REVIEW_NOTE,
    safe as safe_filename,
)


def _attachment_meta(attachment: dict) -> dict:
    """Copy local evidence metadata without any URL/token-bearing fields."""
    allowed = {
        "filename", "display_name", "local_path", "declared_size", "actual_size", "size",
        "detected_media_type", "media_type", "download_status", "extraction_status",
        "extracted_text_path", "attempt", "item_id", "item_link", "ai_eligible",
        "local_only", "warnings", "error_code", "error_message",
        "writing_timeline",
        "media_recording",
    }
    return {k: attachment.get(k) for k in allowed if k in attachment}


def pseudonymize(parsed: dict, vault: IdentityVault, quiz_title: str) -> dict:
    """Build an LLM-safe bundle: one entry per student (by pseudonym) with their
    constructed (written) responses only — no names, ids, or sections. Mutates the
    vault (assigns pseudonyms); caller wraps the operation in a vault transaction."""
    students = []
    for s in parsed.get("students", []):
        written = constructed_responses(s)
        if not written:
            continue
        require_stable = getattr(vault, "require_stable", None)
        if require_stable is not None:
            require_stable(s.get("canvas_id"))
        pseudo = vault.get_or_assign(s.get("canvas_id"), s.get("name", ""), s.get("sis_id", ""))
        students.append({
            "pseudonym": pseudo,
            "responses": [{
                "item_id":  it.get("item_id"),
                "prompt":   it.get("prompt", ""),
                "response": html_to_text(it.get("response", "")),
                "possible": it.get("points_possible_est"),
            } for it in written],
        })
    return {"contract_version": CONTRACT_VERSION, "quiz_title": quiz_title,
            "review_required": True, "note": REVIEW_NOTE, "students": students}


def _is_upload_item(item: dict) -> bool:
    kind = str(item.get("type") or "").lower().replace("-", "_")
    return "upload" in kind


def _extracted_upload_text(files: list) -> str | None:
    """Join locally-extracted text for one item's uploads.

    Returns None unless EVERY upload on the item has readable local text —
    a partially-readable upload item must not be scored as if it were complete.
    """
    texts = []
    for file in files or []:
        if not isinstance(file, dict):
            return None
        path = file.get("extracted_text_path")
        text = ""
        if path and os.path.isfile(workspace.extended_path(path)):
            try:
                with open(workspace.extended_path(path), encoding="utf-8") as f:
                    text = f.read().strip()
            except OSError:
                text = ""
        if not text:
            return None
        texts.append(f"[Uploaded file text: {file.get('filename') or 'upload'}]\n{text}")
    return "\n\n".join(texts) if texts else None


def _new_quiz_ai_response(item: dict) -> dict | None:
    """Map one New Quiz item to an AI-scorable response, or None to keep it local.

    File-upload items are never sent as files or filenames: a TXT/DOCX-style
    upload with locally-extracted text is packaged like an essay response;
    anything else (image, PDF, PPTX, failed download) is excluded from the AI
    payload entirely and stays in the local queue for teacher review.
    """
    if not new_quizzes.is_teacher_scorable_item(item):
        return None
    if _is_upload_item(item):
        response_text = _extracted_upload_text(item.get("files"))
        if response_text is None:
            return None
    else:
        response_text = html_to_text(item.get("raw_html_answer") or "")
        extra = _extracted_upload_text(item.get("files"))
        if extra:
            response_text = "\n\n".join(part for part in (response_text, extra) if part)
    return {
        "item_id": str(item.get("item_id") or ""),
        "prompt": html_to_text(item.get("prompt") or ""),
        "response": response_text,
        "possible": item.get("possible"),
    }


def pseudonymize_submissions(submissions: list, vault: IdentityVault,
                              assignment_title: str) -> dict:
    """Build an LLM-safe bundle from Canvas API submissions (assignments path).

    Each submission is keyed on the vault by canvas_id (student user_id). One entry
    per student with the assignment as a single response item. Mutates the vault;
    caller wraps the operation in a vault transaction.

    Args:
        submissions: list of Canvas submission objects from /students/submissions
        vault: Vault instance
        assignment_title: display name for the assignment
    """
    # Group submissions by student (latest per assignment). The real name/sis come
    # from include[]=user on the fetch; they go ONLY into the vault, never the bundle.
    by_student: dict = {}
    for s in submissions:
        uid = str(s.get("user_id", ""))
        if not uid:
            continue
        a = s.get("assignment") or {}
        user = s.get("user") or {}
        new_quiz_items = s.get("new_quiz_items") or []
        attachment_values = [
            _attachment_meta(a) for a in (s.get("attachments") or [])
            if isinstance(a, dict) and (a.get("local_path") or a.get("download_status"))
        ]
        if new_quiz_items:
            existing = by_student.get(uid)
            if existing is None:
                # New Quiz uploads never enter the AI lane as files: readable
                # local text is inlined per item below, everything else stays
                # local-only for teacher review. No attachments, no media.
                by_student[uid] = {
                    "responses": [], "attachments": [],
                    "real_name": user.get("name") or user.get("sortable_name") or "",
                    "sis_id": str(user.get("sis_user_id") or ""),
                    "_expected_attachment_count": 0,
                }
            by_student[uid]["responses"] = [
                entry for entry in (_new_quiz_ai_response(item) for item in new_quiz_items)
                if entry is not None
            ]
            continue
        prompt = html_to_text(a.get("description") or "")
        body_text = html_to_text(s.get("body") or "")
        response = body_text.strip()
        if not response and not (s.get("attachments") or []) and not s.get("_mirror_unreadable"):
            continue
        entry = {
            "item_id":  str(a.get("id", "")),
            "prompt":   prompt,
            "response": response,
            "attempt": s.get("attempt"),
            "possible": a.get("points_possible"),
            "submitted_at": s.get("submitted_at", ""),
            "score":    s.get("score"),
            "real_name": user.get("name") or user.get("sortable_name") or "",
            "sis_id":    str(user.get("sis_user_id") or ""),
            "attachments": attachment_values,
            "_expected_attachment_count": s.get("expected_attachment_count", len(attachment_values)),
            "_mirror_unreadable": bool(s.get("_mirror_unreadable")),
        }
        # Keep latest submission per assignment
        existing = by_student.get(uid)
        if existing is None or (entry["submitted_at"] or "") > (existing.get("_submitted_at") or ""):
            by_student[uid] = {**entry, "_submitted_at": entry["submitted_at"]}

    students = []
    for uid, entry in by_student.items():
        if "responses" in entry and not entry["responses"]:
            # New Quiz student whose only items were excluded from the AI lane
            # (e.g. image/PDF uploads): keep them out of the packet entirely.
            continue
        require_stable = getattr(vault, "require_stable", None)
        if require_stable is not None:
            require_stable(uid)
        pseudo = vault.get_or_assign(uid, entry["real_name"], entry["sis_id"])
        responses = entry.get("responses") or [{
            "item_id":  entry["item_id"],
            "prompt":   entry["prompt"],
            "response": entry["response"],
            **({"attempt": entry["attempt"]} if entry.get("attempt") is not None else {}),
            "possible": entry["possible"],
        }]
        students.append({
            "pseudonym": pseudo,
            "responses": responses,
            "local_attachments": entry.get("attachments") or [],
            "_expected_attachment_count": entry.get("_expected_attachment_count", 0),
            "_mirror_unreadable": bool(entry.get("_mirror_unreadable")),
        })

    return {"contract_version": CONTRACT_VERSION,
            "quiz_title": assignment_title,
            "source": "assignment",
            "review_required": True, "note": REVIEW_NOTE, "students": students}


def _scrub_bundle(bundle: dict, vault: IdentityVault,
                  protected: set[str] | None = None) -> dict:
    """Deep-scrub every text field in a bundle. Returns a new bundle dict
    with prompts and responses scrubbed."""
    import copy
    out = copy.deepcopy(bundle)
    shared = out.get("shared_context")
    source_parts = []
    if isinstance(shared, dict):
        source_parts.append(str(shared.get("assignment_description") or ""))
        source_parts.extend(
            str(material.get("text") or "")
            for material in shared.get("materials") or []
            if isinstance(material, dict)
        )
    source_protected = set(protected or set())
    source_protected.update(
        feedback_scrub.protected_proper_nouns("\n\n".join(source_parts))
    )
    rmap = feedback_scrub.build_replacement_map(vault.entries(), source_protected)
    for s in out.get("students", []):
        for r in s.get("responses", []):
            if r.get("prompt"):
                r["prompt"] = feedback_scrub.scrub_text_with_protected_spans(
                    r["prompt"], rmap, source_protected
                )
            if r.get("response"):
                r["response"] = feedback_scrub.scrub_text_with_protected_spans(
                    r["response"], rmap, source_protected, quoted_only=True
                )
    if isinstance(shared, dict):
        if shared.get("assignment_description"):
            shared["assignment_description"] = feedback_scrub.scrub_text_with_protected_spans(
                shared.get("assignment_description") or "", rmap, source_protected
            )
        for material in shared.get("materials") or []:
            if isinstance(material, dict) and material.get("text"):
                material["text"] = feedback_scrub.scrub_text_with_protected_spans(
                    material.get("text") or "", rmap, source_protected
                )
    return out


def _prepare_attachment_safe_bundle(bundle: dict, safe_dir: str,
                                    compact: bool = False) -> tuple[dict, list[str], list[str], list[dict]]:
    """Convert downloaded local evidence into safe text/media or hold the student."""
    import copy

    out = copy.deepcopy(bundle)
    excluded: list[str] = []
    log: list[str] = []
    media_holds: list[dict] = []
    kept_students = []
    for student in out.get("students") or []:
        pseudo = student.get("pseudonym") or "unknown"
        if student.get("_mirror_unreadable"):
            # Mirror-only preparation deliberately downloads no evidence. Keep
            # the empty response in SAFE so the packet's held count remains
            # honest, while attachment metadata stays in the private run.
            student.pop("local_attachments", None)
            student.pop("_expected_attachment_count", None)
            student.pop("_mirror_unreadable", None)
            kept_students.append(student)
            continue
        attachments = [a for a in student.get("local_attachments") or [] if isinstance(a, dict)]
        if not attachments:
            student.pop("local_attachments", None)
            student.pop("_expected_attachment_count", None)
            kept_students.append(student)
            continue
        expected_count = student.get("_expected_attachment_count", len(attachments))
        media = [attachment for attachment in attachments if attachment.get("media_recording")]
        ordinary = [attachment for attachment in attachments if not attachment.get("media_recording")]
        if media:
            excluded.append(pseudo)
            hold_reason = "Media recording requires teacher review."
            media_holds.append({"pseudonym": pseudo, "message": hold_reason})
            log.append(f"!! HELD {pseudo} — {hold_reason}")
            continue
        attachments = ordinary
        if not attachments:
            student.pop("local_attachments", None)
            student.pop("_expected_attachment_count", None)
            kept_students.append(student)
            continue
        expected_count = max(0, int(student.get("_expected_attachment_count", len(attachments))) - len(media))
        decision = student_attachments.eligibility_decision(attachments, expected_count=expected_count)
        if not decision["eligible"]:
            excluded.append(pseudo)
            log.append(f"!! HELD {pseudo} — attachment evidence needs teacher review: {decision['reasons'][0]}")
            continue
        if compact:
            media_dir = os.path.join(safe_dir, "S", pseudo.replace(" ", "-"))
        else:
            media_dir = os.path.join(safe_dir, "Students", pseudo.replace(" ", "-"))
        for index, attachment in enumerate(attachments, start=1):
            local_path = attachment.get("local_path")
            if not local_path or not os.path.isfile(workspace.extended_path(local_path)):
                excluded.append(pseudo)
                log.append(f"!! HELD {pseudo} — downloaded attachment is no longer available locally")
                break
            try:
                with open(workspace.extended_path(local_path), "rb") as source:
                    raw_attachment = source.read()
                routed = student_attachments.route_bytes(
                    f"attachment-{index}{os.path.splitext(attachment.get('filename') or '')[1]}",
                    raw_attachment,
                )
            except Exception as exc:
                excluded.append(pseudo)
                log.append(f"!! HELD {pseudo} — attachment validation failed ({type(exc).__name__})")
                break
            routed_decision = student_attachments.eligibility_decision([routed])
            if not routed_decision["eligible"]:
                excluded.append(pseudo)
                log.append(
                    f"!! HELD {pseudo} — attachment evidence changed or failed validation "
                    f"({routed_decision['reasons'][0]})"
                )
                break
            for response in student.get("responses") or []:
                if attachment.get("item_id") and str(response.get("item_id")) != str(attachment.get("item_id")):
                    continue
                timeline = writing_timeline.safe_projection(attachment.get("writing_timeline"))
                if timeline is not None:
                    response.setdefault("writing_timeline", {"documents": []})[
                        "documents"
                    ].append(timeline)
                if routed.get("text"):
                    response["response"] = (response.get("response") or "") + "\n\n" + (
                        f"Attachment {index} text:\n{routed['text']}"
                    )
                for output in student_attachments.write_safe_derivatives(
                    routed, media_dir, pseudonym=pseudo, item_id=attachment.get("item_id") or index,
                    compact=compact,
                ):
                    response.setdefault("media", []).append({
                        "item_id": str(attachment.get("item_id") or ""),
                        "filename": f"attachment-{index}",
                        "local_path": output["local_path"],
                        "media_type": output["media_type"],
                    })
        student.pop("local_attachments", None)
        student.pop("_expected_attachment_count", None)
        if pseudo not in excluded:
            kept_students.append(student)
    out["students"] = kept_students
    return out, excluded, log, media_holds


def _shared_context_blob(shared) -> str:
    if not isinstance(shared, dict):
        return ""
    chunks = []
    if shared.get("assignment_description"):
        chunks.append(str(shared.get("assignment_description") or ""))
    for material in shared.get("materials") or []:
        if isinstance(material, dict):
            chunks.append(str(material.get("text") or ""))
    return "\n\n".join(chunks)
