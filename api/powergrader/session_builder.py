"""PowerGrader session builder — constructs student list and session dict."""

from datetime import datetime

from api.powergrader.student_attachments import eligibility_decision
from api.powergrader import media_recordings
from api.powergrader import oral_reading
from api.mirror.attempt_text import digest as attempt_text_digest
from api.powergrader.attempt_history import summarize as summarize_attempt_history


def _attachment_metadata(attachment: dict) -> dict:
    """Persist review metadata only; never persist a Canvas/signed URL."""
    if attachment.get("media_recording"):
        metadata = media_recordings.review_metadata(attachment)
        report = attachment.get("oral_reading")
        if isinstance(report, dict):
            metadata["oral_reading"] = oral_reading.review_projection(report)
            metadata["oral_reading_private"] = report
        return metadata
    allowed = (
        "filename", "display_name", "local_path", "declared_size", "actual_size",
        "size", "detected_media_type", "media_type", "download_status",
        "extraction_status", "extracted_text_path", "attempt", "item_id",
        "item_link", "ai_eligible", "local_only", "warnings", "error_code",
        "error_message",
        "writing_timeline",
    )
    return {key: attachment.get(key) for key in allowed if key in attachment}


def build_students(
    *,
    submitted: list[dict],
    ai_by_uid: dict,
    ai_item_by_uid: dict | None = None,
    monitored: dict,
    extra_time_map: dict,
    ai_failures: dict | None = None,
) -> list[dict]:
    """Build the sorted student list for a session from Canvas submission data."""
    students = []
    for s in submitted:
        uid = str(s.get("user_id", ""))
        user = s.get("user") or {}
        real_name = user.get("name") or user.get("sortable_name") or uid

        mon = monitored.get(uid)
        extra_days = extra_time_map.get(uid, 0)

        body = s.get("body") or ""
        attempt_summary = summarize_attempt_history(
            s.get("_attempt_records") or (), s.get("attempt"))
        first_attempt = attempt_summary.get("first_meaningful")
        latest_attempt = attempt_summary.get("latest")
        entered_score = s.get("entered_score")
        if entered_score is None and s.get("score") is not None:
            try:
                entered_score = float(s.get("score")) + float(s.get("points_deducted") or 0)
            except (TypeError, ValueError):
                entered_score = None
        attachments = [
            _attachment_metadata({**a, "filename": a.get("filename") or a.get("display_name", "")})
            for a in (s.get("attachments") or [])
            if a.get("filename") or a.get("display_name")
        ]
        code_files = [
            {"filename": cf.get("filename", ""), "text": cf.get("text", "")}
            for cf in (s.get("code_files") or [])
        ]
        new_quiz_items = []
        for item in (s.get("new_quiz_items") or []):
            new_quiz_items.append({
                key: item.get(key) for key in (
                    "item_id", "type", "prompt", "possible", "earned_score",
                    "status", "files",
                )
                if key in item
            })
        expected_count = s.get("expected_attachment_count")
        if expected_count is None and not attachments:
            expected_count = 0
        eligibility = eligibility_decision(attachments, expected_count=expected_count)
        has_media_recording = any(item.get("media_recording") for item in attachments)
        requires_speedgrader = any(
            "upload" in str(item.get("type") or "").lower().replace("_", "-")
            or (str(item.get("type") or "").lower() != "essay" and item.get("earned_score") is None)
            for item in new_quiz_items
        )

        ai = ai_by_uid.get(uid, {})
        ai_items = (ai_item_by_uid or {}).get(uid, [])
        ai_failure = (ai_failures or {}).get(uid)
        students.append({
            "user_id":       uid,
            "real_name":     real_name,
            "body":          body,
            "attachments":   attachments,
            "attachment_expected_count": expected_count,
            "new_quiz_items": new_quiz_items,
            "attachment_eligibility": eligibility,
            "has_media_recording": has_media_recording,
            "analysis_unavailable": "" if has_media_recording else "",
            "new_quiz_files_error": s.get("new_quiz_files_error"),
            "speedgrader_required": requires_speedgrader,
            "code_files":    code_files,
            "current_score": s.get("score"),
            "submission_baseline": {
                "attempt": s.get("attempt"),
                # Keep the current-attempt timestamp for refresh/resubmission
                # detection. Late-day math uses first_attempt_at separately.
                "submitted_at": s.get("submitted_at"),
                "first_attempt_at": (first_attempt or {}).get("submitted_at"),
                "latest_attempt_at": (latest_attempt or {}).get("submitted_at") or s.get("submitted_at"),
                "attempt_count": attempt_summary.get("count", 0),
                "latest_attempt": (latest_attempt or {}).get("attempt") or s.get("attempt"),
                "attempts_complete": attempt_summary.get("complete", False),
                "attempts_known": attempt_summary.get("known", True),
                "entered_score": entered_score,
                "canvas_score": s.get("score"),
                "points_deducted": s.get("points_deducted"),
                "submission_digest": s.get("submission_digest") or attempt_text_digest(s.get("body")),
            },
            "status":        "pending",
            "ai_score":      ai.get("score"),
            "ai_feedback":   ai.get("feedback"),
            "agent_commentary": ai.get("agent_commentary", ""),
            "ai_item_results": [dict(item) for item in ai_items],
            **({"ai_scoring_error": ai_failure} if ai_failure else {}),
            "teacher_score": None,
            "teacher_feedback": "",
            "posted":        False,
            "is_monitored":  bool(mon),
            "monitored_note": (mon or {}).get("note", ""),
            "extra_time_days": extra_days,
            # Every submission carries its due-date/late facts now, not only
            # New Quiz rows -- Scoring Sessions need them for effort credit
            # and teacher-confirmed late days (grading-policy-contract.md).
            "cached_due_date": s.get("cached_due_date"),
            "canvas_late":   bool(s.get("late")),
            "seconds_late":  s.get("seconds_late"),
            **({"new_quiz_attempt": s.get("new_quiz_attempt")} if s.get("new_quiz_attempt") is not None else {}),
        })

    students.sort(key=lambda x: x["real_name"].lower())
    return students


def freeze_score_provenance(students: list[dict], course_id: str,
                            assignment_id: str, *, pseudonyms=None,
                            safe_bundle=None) -> None:
    """Bind current raw-score links to this session's exact mirror baseline."""
    for student in students:
        baseline = student.get("submission_baseline")
        if not isinstance(baseline, dict):
            continue
        entered = baseline.get("entered_score")
        baseline.update({"raw_score": None,
                         "basis": "entered" if entered is not None else None,
                         "rule_id": None, "event_id": None,
                         "consistency": "entered_only" if entered is not None else "unknown",
                         "text_consistency": "source_unknown"})
    if pseudonyms:
        try:
            from api.mirror import submission_history
            from api.platform_services import workspace
            manifest = submission_history.read_history(
                str(course_id), str(assignment_id), root=workspace.workspace_root())
            history_attempts = manifest.get("attempts") or {}
            for student in students:
                baseline = student.get("submission_baseline") or {}
                uid = str(student.get("user_id") or "")
                pseudonym = str(pseudonyms.get(uid) or "")
                attempt = str(baseline.get("attempt") or "")
                submitted_at = str(baseline.get("submitted_at") or "")
                key = f"{pseudonym}|{attempt}|{submitted_at}"
                doc = history_attempts.get(key)
                observations = (doc or {}).get("observations") or []
                expected = str(baseline.get("submission_digest") or "")
                observed = {str(item.get("text_digest") or "") for item in observations}
                if doc and doc.get("conflict"):
                    baseline["text_consistency"] = "conflicting"
                elif expected and expected in observed:
                    baseline["text_consistency"] = "consistent"
                elif observations:
                    baseline["text_consistency"] = "digest_mismatch"
                else:
                    baseline["text_consistency"] = "missing_text"
        except Exception:
            pass
    if isinstance(safe_bundle, dict):
        from api.mirror.attempt_text import normalize as normalize_attempt_text
        safe_by_pseudonym = {str(row.get("pseudonym") or ""): row
                             for row in safe_bundle.get("students") or []
                             if isinstance(row, dict)}
        for student in students:
            baseline = student.get("submission_baseline") or {}
            uid = str(student.get("user_id") or "")
            pseudonym = str((pseudonyms or {}).get(uid) or "")
            source_body = normalize_attempt_text(student.get("body"))
            responses = (safe_by_pseudonym.get(pseudonym) or {}).get("responses") or []
            packet_text = "\n".join(str(row.get("response") or "") for row in responses
                                      if isinstance(row, dict))
            if not source_body or not packet_text:
                baseline["packet_text_consistency"] = "source_unknown"
            else:
                baseline["packet_text_consistency"] = (
                    "consistent" if source_body == normalize_attempt_text(packet_text)
                    else "packet_source_mismatch")
    try:
        from api import score_ledger
        events = score_ledger.list_events(str(course_id), str(assignment_id))
    except Exception:
        return
    for student in students:
        baseline = student.get("submission_baseline") or {}
        uid = str(student.get("user_id") or "")
        verified_pushes = [event for event in events
                           if event.get("source") == "ce_apply"
                           and event.get("action") == "verified"
                           and str(event.get("student_id") or "") == uid
                           and event.get("attempt") not in (None, "")]
        if verified_pushes:
            latest_push = max(verified_pushes,
                              key=lambda event: (str(event.get("timestamp") or ""),
                                                 str(event.get("event_id") or "")))
            student["posted_attempt"] = latest_push.get("attempt")
        else:
            student["posted_attempt"] = None
        attempt = str(baseline.get("attempt") or "")
        entered, canvas = baseline.get("entered_score"), baseline.get("canvas_score")
        if not uid or not attempt or entered is None or canvas is None:
            continue
        links = [event for event in events
                 if event.get("source") in {"ce_apply", "ce_curve", "ce_adjustment"}
                 and event.get("action") == "verified"
                 and event.get("curve_rule_id")
                 and event.get("raw_score") is not None
                 and str(event.get("student_id") or "") == uid
                 and str(event.get("attempt") or "") == attempt
                 and event.get("entered_score") is not None
                 and abs(float(event["entered_score"]) - float(entered)) <= 1e-6
                 and event.get("canvas_score") is not None
                 and abs(float(event["canvas_score"]) - float(canvas)) <= 1e-6]
        if not links:
            continue
        link = max(links, key=lambda event: str(event.get("timestamp") or ""))
        invalidated = any(
            event.get("source") == "canvas_external"
            and event.get("action") == "external_change"
            and str(event.get("student_id") or "") == uid
            and str(event.get("attempt") or "") == attempt
            and str(event.get("timestamp") or "") > str(link.get("timestamp") or "")
            for event in events)
        if invalidated:
            baseline.update({"consistency": "external_change", "event_id": link.get("event_id"),
                             "rule_id": link.get("curve_rule_id")})
            continue
        baseline.update({"raw_score": link.get("raw_score"), "basis": "raw",
                         "rule_id": link.get("curve_rule_id"), "event_id": link.get("event_id"),
                         "consistency": "current_linked"})


def build_session(
    *,
    session_id: str,
    course_id: str,
    assignment_id: str,
    assignment_name: str,
    points_possible: float,
    mode: str,
    rubric_name: str,
    selected_model: str,
    assignment_description: str = "",
    response_kind: str = "",
    privacy_steps: list[dict] | None = None,
    privacy_artifacts: dict | None = None,
    students: list[dict] | None = None,
    mode_label: str = "",
    late_watch: dict | None = None,
    evidence_manifest: str | None = None,
    evidence_status: str = "unknown",
    oral_reading_passage: dict | None = None,
    session_kind: str = "",
) -> dict:
    """Build the session dictionary ready to save."""
    session = {
        "session_id":      session_id,
        "course_id":       course_id,
        "assignment_id":   assignment_id,
        "assignment_name": assignment_name,
        "assignment_description": assignment_description,
        "points_possible": points_possible,
        "created":         datetime.now().isoformat(timespec="seconds"),
        "mode":            mode,
        "mode_label":      mode_label,
        "rubric_name":     rubric_name,
        "model_id":        selected_model if mode == "assisted" else "",
        "response_kind":   response_kind,
        "privacy_steps":   privacy_steps or [],
        "privacy_artifacts": privacy_artifacts or {},
        "late_watch":      late_watch,
        "evidence_manifest": evidence_manifest,
        "evidence_status": evidence_status,
        "oral_reading_passage": oral_reading_passage or {},
        "students":        students or [],
        "push_log":        [],
    }
    if session_kind:
        session["session_kind"] = str(session_kind)
    return session
