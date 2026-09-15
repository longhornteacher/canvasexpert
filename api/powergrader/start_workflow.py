"""PowerGrader start-workflow helpers shared by route orchestration."""

import uuid
from datetime import datetime

from api import gradebook_snapshot
from api.nq_report import html_to_text
from api.platform_services import config, workspace
from api.powergrader import (
    assignment_refresh,
    ai_workflow,
    oral_reading,
    privacy,
    session_builder,
    session_store,
    student_attachments,
    writing_timeline,
)
from api.powergrader.helpers import (
    build_start_error_payload,
    build_start_success_payload,
)

MAX_TEACHER_SCORING_GUIDANCE_CHARS = 12000


def scoring_guidance_length_error(scoring_basis: dict | None, rubric_text: str) -> dict | None:
    if (scoring_basis or {}).get("source") != "teacher_guidance":
        return None
    if len(str(rubric_text or "")) <= MAX_TEACHER_SCORING_GUIDANCE_CHARS:
        return None
    return {
        "ok": False,
        "error": (
            "Scoring guidance is too long; provide at most "
            f"{MAX_TEACHER_SCORING_GUIDANCE_CHARS:,} characters."
        ),
        "code": "scoring_guidance_too_long",
    }


def build_extra_time_map(extra_time_list: list[dict]) -> dict:
    return {str(entry["id"]): entry.get("days", 0) for entry in extra_time_list}


def append_privacy_audit_step(
    *,
    privacy_artifacts: dict,
    assignment_name: str,
    session_id: str,
    course_id: str,
    assignment_id: str,
    mode: str,
    selected_model: str,
    privacy_steps: list[dict],
    write_privacy_audit_file,
    privacy_step,
) -> tuple[dict, list[dict]]:
    if not privacy_artifacts.get("private_folder"):
        return privacy_artifacts, privacy_steps

    audit_path = write_privacy_audit_file(
        privacy_artifacts["private_folder"],
        assignment_name,
        session_id,
        course_id,
        assignment_id,
        selected_model if mode == "assisted" else "",
        privacy_steps,
        privacy_artifacts,
    )
    if audit_path:
        privacy_artifacts["privacy_audit"] = audit_path
        privacy_steps.append(privacy_step(
            "privacy_audit", "Saved PowerGrader privacy audit", "ok",
            "Private decoder folder includes a JSON record of these privacy steps.",
            path=audit_path,
        ))
    else:
        privacy_steps.append(privacy_step(
            "privacy_audit", "Saved PowerGrader privacy audit", "warn",
            "Could not write the optional privacy audit JSON; session still records these steps.",
        ))
    return privacy_artifacts, privacy_steps


def _auto_post_block(mode: str, auto_post_enabled: bool) -> dict:
    """Build the auto_post block for a new session."""
    from datetime import timezone
    if not auto_post_enabled or mode not in ("assisted", "packet"):
        return {
            "enabled": False,
            "authorized_at": None,
            "disabled_at": None,
            "policy_version": 2,
        }
    return {
        "enabled": True,
        "authorized_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "disabled_at": None,
        "policy_version": 2,
    }


def build_start_session(
    *,
    build_session,
    session_id: str,
    course_id: str,
    assignment_id: str,
    assignment_name: str,
    points_possible: float,
    mode: str,
    rubric_name: str,
    persona_id: str,
    selected_model: str,
    assignment_description: str,
    response_kind: str,
    privacy_steps: list[dict],
    privacy_artifacts: dict,
    students: list[dict],
    mode_label: str,
    copilot_packet: dict | None,
    late_watch: dict,
    evidence_manifest: str | None = None,
    evidence_status: str = "unknown",
    auto_post_enabled: bool = False,
    oral_reading_passage: dict | None = None,
    parent_scoring_session_id: str = "",
) -> dict:
    session = build_session(
        session_id=session_id,
        course_id=course_id,
        assignment_id=assignment_id,
        assignment_name=assignment_name,
        points_possible=points_possible,
        mode=mode,
        rubric_name=rubric_name,
        persona_id=persona_id,
        selected_model=selected_model,
        assignment_description=assignment_description,
        response_kind=response_kind,
        privacy_steps=privacy_steps,
        privacy_artifacts=privacy_artifacts,
        students=students,
        mode_label=mode_label,
        copilot_packet=copilot_packet,
        late_watch=late_watch,
        evidence_manifest=evidence_manifest,
        evidence_status=evidence_status,
        oral_reading_passage=oral_reading_passage,
        parent_scoring_session_id=parent_scoring_session_id,
    )
    session["auto_post"] = _auto_post_block(mode, auto_post_enabled)
    session["auto_post_log"] = []
    session["auto_post_summary"] = None
    return session


def run_start_session(
    *,
    course_id: str,
    assignment_id: str,
    mode: str,
    watch_late: str,
    auto_post: str,
    rubric_name: str,
    persona_id: str,
    feedback_pattern_id: str,
    model_id: str,
    response_kind: str,
    source_text: str,
    source_files_json: str,
    source_uploads,
    oral_reading_passage: str,
    oral_reading_enabled: str,
    save_session,
    scoring_session: bool = False,
    scoring_guidance: str = "",
    parent_scoring_session_id: str = "",
    mirror_only: bool = False,
) -> dict:
    """Run the PowerGrader session-start orchestration for one assignment.

    Owns the course/assignment guard checks, assignment refresh, submitted/media
    filtering, oral-reading analysis, writing-timeline attachment, late-watch
    state, the AI workflow call, roster context, session construction, saving,
    and the success payload.

    The caller (the HTTP route) keeps Form/UploadFile parsing, the assisted-mode
    auto-post trigger, and building the JSONResponse.

    Returns a dict with key "ok". On failure, "payload" is the exact error
    payload the route should return as-is. On success, "payload" is the base
    success payload (the route may still add "auto_post_summary" to it),
    plus "session_id", "mode" (normalized), and "auto_post_enabled" for the
    route's own auto-post trigger step.
    """
    if not course_id or not assignment_id:
        return {"ok": False, "payload": {"ok": False, "error": "course_id and assignment_id are required."}}
    if not workspace.workspace_root():
        return {"ok": False, "payload": {"ok": False, "error": "No workspace configured — finish setup first."}}
    if not scoring_session:
        return {"ok": False, "payload": {"ok": False,
            "error": "Only chat-first Scoring Sessions can start from this workflow."}}
    mode = "packet"
    session_id = str(uuid.uuid4())
    course_name = config.course_display_name(course_id)

    # Scoring Session preparation is mirror-only: the agent-facing path never
    # invokes the focused Canvas/evidence owner. Existing web starts retain the
    # focused owner and its live evidence behavior.
    if mirror_only:
        subs, adata, refresh = assignment_refresh.prepare_assignment_from_mirror(
            course_id, assignment_id)
    else:
        subs, adata, refresh = assignment_refresh.refresh_assignment(
            course_id, assignment_id, session_id=session_id)
    if refresh.get("error"):
        return {"ok": False, "payload": {"ok": False, "error": refresh["error"], "privacy_steps": []}}
    if not isinstance(adata, dict):
        return {"ok": False, "payload": {"ok": False,
            "error": "The assignment could not be prepared safely; refresh the course and retry.",
            "privacy_steps": []}}

    assignment_name = adata.get("name") or assignment_id
    assignment_description = html_to_text(adata.get("description") or "")
    points_possible = float(adata.get("points_possible") or 100)
    is_new_quiz = (
        adata.get("is_quiz_lti_assignment") is True
        or adata.get("quiz_kind") == "new_quiz"
    )

    if is_new_quiz:
        return {"ok": False, "payload": {
            "ok": False,
            "code": "new_quiz_writing_requires_assignment",
            "error": (
                "Grade this New Quiz writing in Canvas. For future assessments, "
                "author each writing portion as a separate 100-point AssignmentForge assignment."
            ),
            "assignment_name": str(assignment_name),
        }}

    if not subs:
        return {"ok": False, "payload": {"ok": False, "error": "No submissions found for this assignment.",
                                          "privacy_steps": []}}

    submitted = [s for s in subs if gradebook_snapshot.needs_grading(s)]
    if not submitted:
        return {"ok": False, "payload": {
            "ok": False,
            "code": "nothing_to_grade",
            "assignment_name": str(assignment_name),
        }}
    rubric_text_override = None
    scoring_basis = None
    if scoring_session:
        canvas_rubric = scoring_rubric_text(adata.get("rubric"))
        if canvas_rubric:
            rubric_name = "Canvas rubric"
            rubric_text_override = canvas_rubric
            scoring_basis = {"source": "canvas_rubric", "label": "Canvas rubric"}
        elif rubric_name:
            rubric_text_override = ai_workflow.context.load_rubric_text(rubric_name)
            if not rubric_text_override.strip():
                return {"ok": False, "payload": {"ok": False,
                    "error": "The selected Canvas Expert rubric is unavailable. Choose a listed rubric or provide scoring guidance.",
                    "code": "rubric_unavailable"}}
            scoring_basis = {"source": "canvas_expert_rubric", "label": rubric_name}
        elif scoring_guidance.strip():
            rubric_text_override = scoring_guidance.strip()
            rubric_name = "Teacher scoring guidance"
            scoring_basis = {"source": "teacher_guidance", "label": "Teacher scoring guidance"}
        else:
            from api.webui.deps import list_rubric_files
            labels = [str(item.get("label") or "") for item in list_rubric_files()
                      if str(item.get("label") or "").strip()]
            return {"ok": False, "payload": {"ok": False,
                "code": "needs_scoring_norms",
                "error": "No usable Canvas rubric is attached. Choose a Canvas Expert rubric or provide scoring guidance.",
                "assignment_name": str(assignment_name),
                "rubric_labels": labels}}
        scoring_guidance_error = scoring_guidance_length_error(
            scoring_basis, rubric_text_override,
        )
        if scoring_guidance_error:
            return {"ok": False, "payload": scoring_guidance_error}

    media_submissions = [s for s in submitted if s.get("submission_type") == "media_recording"]
    oral_enabled = str(oral_reading_enabled).lower() in {"1", "true", "yes", "on"}
    if oral_enabled and not media_submissions:
        return {"ok": False, "payload": {"ok": False, "error": "Read-aloud analysis needs at least one submitted media recording.", "code": "oral_reading_media_required", "privacy_steps": []}}
    passage_tokens, passage_error = oral_reading.validate_passage(oral_reading_passage) if oral_enabled else (None, None)
    if oral_enabled and passage_error:
        return {"ok": False, "payload": {"ok": False, "error": passage_error, "code": "oral_reading_passage_required", "privacy_steps": []}}
    oral_passage = " ".join(passage_tokens or [])
    if oral_enabled:
        try:
            transcriber = oral_reading.construct_transcriber()
            construction_error = None
        except oral_reading.LocalTranscriberUnavailable as exc:
            transcriber, construction_error = None, exc
        for submission in media_submissions:
            for attachment in submission.get("attachments") or []:
                if attachment.get("media_recording"):
                    if construction_error:
                        attachment["oral_reading"] = {"status": "unavailable", "error_code": construction_error.code, "error_message": construction_error.message}
                    else:
                        attachment["oral_reading"] = oral_reading.analyze_recording(attachment, oral_passage, transcribe=transcriber)
    writing_timeline_tracked = writing_timeline.is_tracked_assignment(adata)
    if writing_timeline_tracked:
        student_attachments.attach_writing_timelines(
            submitted,
            roster_submissions=subs,
        )

    submitted_user_ids = sorted({str(s.get("user_id", "")) for s in submitted if s.get("user_id")})

    late_watch = {
        "enabled": False,
        "supported": False,
        "reason": "A Scoring Session is a snapshot; start a new session for later work.",
        "known_user_ids": submitted_user_ids,
        "scored_user_ids": [],
        "generated_user_ids": [],
    }
    # AI workflow
    ai_result = ai_workflow.run_ai_workflow(
        mode=mode,
        submitted=submitted,
        assignment_name=assignment_name,
        assignment_description=assignment_description,
        course_id=course_id,
        course_name=course_name,
        assignment_id=assignment_id,
        session_id=session_id,
        rubric_name=rubric_name,
        rubric_text_override=rubric_text_override,
        persona_id=persona_id,
        source_text=source_text,
        source_files_json=source_files_json,
        source_uploads=source_uploads,
    )
    if not ai_result["ok"]:
        return {"ok": False, "payload": build_start_error_payload(
            ai_result["error"],
            privacy_steps=ai_result["privacy_steps"],
            budget=ai_result.get("budget"),
            debug_path=ai_result.get("debug_path"),
        )}
    privacy_steps = ai_result["privacy_steps"]
    privacy_artifacts = ai_result["privacy_artifacts"]
    ai_by_uid = ai_result["ai_by_uid"]
    ai_failures = dict(ai_result.get("ai_failures") or {})

    # Roster context
    roster_settings = config.get_roster_student_settings(course_id)
    tier_map = config.roster_tier_by_id(course_id)
    monitored = config.get_monitored_students()
    extra_time_list = config.get_extra_time(course_id)
    extra_time_map = build_extra_time_map(extra_time_list)

    students = session_builder.build_students(
        submitted=submitted,
        ai_by_uid=ai_by_uid,
        ai_item_by_uid=ai_result.get("ai_item_by_uid") or {},
        ai_failures=ai_failures,
        roster_settings=roster_settings,
        tier_map=tier_map,
        monitored=monitored,
        extra_time_map=extra_time_map,
    )

    privacy_artifacts, privacy_steps = append_privacy_audit_step(
        privacy_artifacts=privacy_artifacts,
        assignment_name=assignment_name,
        session_id=session_id,
        course_id=course_id,
        assignment_id=assignment_id,
        mode=mode,
        selected_model="",
        privacy_steps=privacy_steps,
        write_privacy_audit_file=privacy.write_privacy_audit_file,
        privacy_step=privacy.privacy_step,
    )

    # Canvas Live is the only review surface; this start path never enables
    # unattended posting.
    auto_post_enabled = False

    session = build_start_session(
        build_session=session_builder.build_session,
        session_id=session_id,
        course_id=course_id,
        assignment_id=assignment_id,
        assignment_name=assignment_name,
        points_possible=points_possible,
        mode=mode,
        rubric_name=rubric_name,
        persona_id=persona_id,
        selected_model="",
        assignment_description=assignment_description,
        response_kind=response_kind,
        privacy_steps=privacy_steps,
        privacy_artifacts=privacy_artifacts,
        students=students,
        mode_label=session_store.mode_label(mode),
        copilot_packet=ai_result.get("copilot_packet"),
        late_watch=late_watch,
        evidence_manifest=refresh.get("manifest_path"),
        evidence_status=refresh.get("status", "unknown"),
        auto_post_enabled=auto_post_enabled,
        oral_reading_passage=({"enabled": True, "passage": oral_passage, "digest": oral_reading.passage_digest(passage_tokens)} if oral_enabled else {"enabled": False}),
        parent_scoring_session_id=parent_scoring_session_id,
    )
    session["writing_timeline_tracked"] = writing_timeline_tracked
    session["feedback_pattern_id"] = str(feedback_pattern_id or "").strip() or "basic"
    if scoring_session:
        session["scoring_basis"] = scoring_basis
        session["scoring_rubric_text"] = rubric_text_override
    save_session(session)

    payload = build_start_success_payload(
        session_id=session_id,
        students=students,
        assignment_name=assignment_name,
        mode=mode,
        mode_label=session_store.mode_label(mode),
        ai_by_uid=ai_by_uid,
        privacy_steps=privacy_steps,
        privacy_artifacts=privacy_artifacts,
        copilot_packet=ai_result.get("copilot_packet"),
        evidence_status=refresh.get("status", "unknown"),
    )
    return {
        "ok": True,
        "payload": payload,
        "session_id": session_id,
        "mode": mode,
        "auto_post_enabled": auto_post_enabled,
    }


def scoring_rubric_text(value) -> str:
    """Render a usable Canvas assignment rubric without exposing its raw shape."""
    if not isinstance(value, list) or not value:
        return ""
    lines = []
    for index, criterion in enumerate(value, 1):
        if not isinstance(criterion, dict):
            continue
        description = str(criterion.get("description") or "").strip()
        points = criterion.get("points")
        if not description or not isinstance(points, (int, float)):
            continue
        lines.append(f"{index}. {description} ({points:g} points)")
        ratings = criterion.get("ratings") or []
        if isinstance(ratings, list):
            for rating in ratings:
                if not isinstance(rating, dict):
                    continue
                label = str(rating.get("description") or "").strip()
                rating_points = rating.get("points")
                if label and isinstance(rating_points, (int, float)):
                    lines.append(f"   - {rating_points:g}: {label}")
    return "\n".join(lines)
