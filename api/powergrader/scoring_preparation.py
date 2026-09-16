"""Single owner for assignment-scoped Scoring Session preparation.

The MCP layer supplies the course gate and one full CanvasMirror refresh
callback. This module then consumes only the resulting local projections,
constructs the existing SAFE artifact, and saves exactly one
``scoring_assignment`` session record.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

from api import gradebook_snapshot
from api.nq_report import html_to_text
from api.platform_services import config, workspace
from api.powergrader import (
    ai_workflow,
    assignment_refresh,
    media_recordings,
    oral_reading,
    privacy,
    session_builder,
    session_store,
    student_attachments,
    writing_timeline,
)


MAX_TEACHER_SCORING_GUIDANCE_CHARS = 12000
SCORING_SESSION_KIND = "scoring_assignment"

_GUIDANCE_DIRECTIVE_RE = re.compile(
    r"\b(?:scor(?:e|ing|ed)|rubric|criteria|criterion|points?|evidence|"
    r"feedback|grade|grades|graded|grading|level|levels|scale|weight(?:ed|ing)?|"
    r"must|should|do[- ]not)\b",
    re.IGNORECASE,
)


def _typed_failure(code: str, stage: str, *, retryable: bool, user_action: str,
                   error: str | None = None, **extra) -> dict:
    """Return the identity-safe preparation result shape."""
    result = {
        "ok": False,
        "code": str(code),
        "stage": str(stage),
        "retryable": bool(retryable),
        "user_action": str(user_action),
        "error": str(error or user_action),
    }
    result.update(extra)
    return result


def _guidance_units(text: str) -> list[str]:
    return [unit.strip() for unit in re.split(r"(?:\r?\n){1,2}", text) if unit.strip()]


def _guidance_snippet(
    unit: str, budget: int, *, tail: bool = False, directive: bool = False,
) -> str:
    if len(unit) <= budget:
        return unit
    if budget <= 1:
        return unit[-budget:] if tail else unit[:budget]
    if directive:
        match = _GUIDANCE_DIRECTIVE_RE.search(unit)
        tokens = list(re.finditer(r"\S+", unit))
        if match and tokens:
            anchor = next((i for i, token in enumerate(tokens)
                           if token.start() <= match.start() < token.end()), 0)
            if tokens[anchor].end() - tokens[anchor].start() > budget:
                return unit[match.start():match.end()][:budget]
            left = right = anchor
            next_side = "left"
            while True:
                candidates = []
                if left > 0:
                    candidates.append((tokens[left - 1].start(), tokens[right].end(), "left"))
                if right + 1 < len(tokens):
                    candidates.append((tokens[left].start(), tokens[right + 1].end(), "right"))
                preferred = next((candidate for candidate in candidates
                                  if candidate[2] == next_side and candidate[1] - candidate[0] <= budget), None)
                alternate_side = "right" if next_side == "left" else "left"
                alternate = next((candidate for candidate in candidates
                                  if candidate[2] == alternate_side and candidate[1] - candidate[0] <= budget), None)
                candidate = preferred or alternate
                if candidate is None:
                    break
                _start, _end, side = candidate
                if side == "left":
                    left -= 1
                else:
                    right += 1
                next_side = alternate_side
            return unit[tokens[left].start():tokens[right].end()]
    snippet = unit[-budget:] if tail else unit[:budget]
    if tail:
        boundary = re.search(r"\s", snippet)
        if boundary:
            snippet = snippet[boundary.end():]
    else:
        boundary = list(re.finditer(r"\s", snippet))[-1] if re.search(r"\s", snippet) else None
        if boundary:
            snippet = snippet[:boundary.start()]
    return snippet or (unit[-budget:] if tail else unit[:budget])


def project_teacher_scoring_guidance(text: str) -> tuple[str, dict]:
    """Keep full teacher guidance private and deterministically project transport text."""
    text = str(text or "").strip()
    original_chars = len(text)
    if original_chars <= MAX_TEACHER_SCORING_GUIDANCE_CHARS:
        return text, {
            "compacted": False,
            "original_chars": original_chars,
            "effective_chars": original_chars,
            "omitted_chars": 0,
            "omitted_units": 0,
        }

    units = _guidance_units(text) or [text]
    boundary_count = 1 if len(units) < 8 else (2 if len(units) < 20 else 3)
    first = list(range(min(boundary_count, len(units))))
    last = list(range(max(0, len(units) - boundary_count), len(units)))
    middle = [i for i, unit in enumerate(units)
              if i not in first and i not in last and _GUIDANCE_DIRECTIVE_RE.search(unit)]
    middle = middle[:10]
    selected = sorted(set(first + middle + last))
    marker_template = (
        "[Teacher scoring guidance compacted: original_chars={original}; "
        "effective_chars={effective}; omitted_chars={omitted}; "
        "omitted_units={units}]"
    )
    reserve = len(marker_template.format(original=original_chars, effective=12000,
                                         omitted=original_chars, units=len(units))) + 2
    content_budget = max(1, MAX_TEACHER_SCORING_GUIDANCE_CHARS - reserve)
    if len(units) == 1:
        separators = 2
        per_unit = max(1, (content_budget - separators) // 2)
        snippets = [_guidance_snippet(units[0], per_unit),
                    _guidance_snippet(units[0], per_unit, tail=True)]
    else:
        separators = max(0, len(selected) - 1) * 2
        per_unit = max(1, (content_budget - separators) // max(1, len(selected)))
        snippets = [_guidance_snippet(
            units[index], per_unit,
            tail=index in last and index not in first,
            directive=index in middle,
        ) for index in selected]
    content = "\n\n".join(snippets)
    omitted_units = len(units) - len(selected)
    if len(units) == 1:
        omitted_units = 0

    def _compose(current_content: str) -> tuple[str, int]:
        effective_length = len(current_content) + 1 + len(marker_template.format(
            original=original_chars, effective=0, omitted=original_chars,
            units=omitted_units,
        ))
        for _ in range(8):
            marker = marker_template.format(
                original=original_chars,
                effective=effective_length,
                omitted=original_chars - effective_length,
                units=omitted_units,
            )
            actual_length = len(marker) + 1 + len(current_content)
            if actual_length == effective_length:
                return marker, actual_length
            effective_length = actual_length
        return marker, effective_length

    marker, effective_length = _compose(content)
    if effective_length > MAX_TEACHER_SCORING_GUIDANCE_CHARS:
        content = content[:max(0, len(content) - (effective_length - MAX_TEACHER_SCORING_GUIDANCE_CHARS))].rstrip()
        marker, effective_length = _compose(content)
    effective = marker + "\n" + content
    effective_chars = len(effective)
    return effective, {
        "compacted": True,
        "original_chars": original_chars,
        "effective_chars": effective_chars,
        "omitted_chars": original_chars - effective_chars,
        "omitted_units": len(units) - len(selected),
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
        for rating in criterion.get("ratings") or []:
            if not isinstance(rating, dict):
                continue
            label = str(rating.get("description") or "").strip()
            rating_points = rating.get("points")
            if label and isinstance(rating_points, (int, float)):
                lines.append(f"   - {rating_points:g}: {label}")
    return "\n".join(lines)


def _extra_time_map(entries: list[dict]) -> dict:
    return {str(entry["id"]): entry.get("days", 0) for entry in entries}


def _append_privacy_audit(
    *, privacy_artifacts: dict, assignment_name: str, session_id: str,
    course_id: str, assignment_id: str, privacy_steps: list[dict],
) -> tuple[dict, list[dict]]:
    if not privacy_artifacts.get("private_folder"):
        return privacy_artifacts, privacy_steps
    audit_path = privacy.write_privacy_audit_file(
        privacy_artifacts["private_folder"], assignment_name, session_id,
        course_id, assignment_id, "", privacy_steps, privacy_artifacts,
    )
    if audit_path:
        privacy_artifacts["privacy_audit"] = audit_path
        privacy_steps.append(privacy.privacy_step(
            "privacy_audit", "Saved PowerGrader privacy audit", "ok",
            "Private decoder folder includes a JSON record of these privacy steps.",
            path=audit_path,
        ))
    else:
        privacy_steps.append(privacy.privacy_step(
            "privacy_audit", "Saved PowerGrader privacy audit", "warn",
            "Could not write the optional privacy audit JSON; session still records these steps.",
        ))
    return privacy_artifacts, privacy_steps


def _safe_bundle_path(session: dict) -> str:
    raw = (session.get("privacy_artifacts") or {}).get("safe_bundle") or ""
    resolved = workspace.extended_path(raw) if raw else ""
    return resolved if resolved and os.path.isfile(resolved) else ""


def _ready_payload(session: dict) -> dict:
    """Return only identity-free facts after the private session is persisted."""
    result = {
        "ok": True,
        "status": "ready",
        "scoring_session_id": session["session_id"],
        "assignment_name": str(session.get("assignment_name") or ""),
        "scoring_basis": session.get("scoring_basis") or {},
    }
    bundle_path = _safe_bundle_path(session)
    if not bundle_path:
        return _typed_failure(
            "safe_packet_unavailable", "prepare", retryable=True,
            user_action="The SAFE scoring packet could not be verified. Retry preparation.",
        )
    try:
        with open(bundle_path, encoding="utf-8") as handle:
            safe_bundle = json.load(handle)
        from api.powergrader import scoring_packet
        page = scoring_packet.build_packet(
            session=session, safe_bundle=safe_bundle, offset=0, limit=1,
            include_context=False,
        )
    except Exception:
        return _typed_failure(
            "safe_packet_unavailable", "prepare", retryable=True,
            user_action="The SAFE scoring packet could not be verified. Retry preparation.",
        )
    result.update({
        "student_count": len(session.get("students") or []),
        "response_count": int(page.get("total") or 0),
        "held": int(page.get("held") or 0),
        "next": "Call get_scoring_packet with scoring_session_id and read every page.",
    })
    return result


def prepare_scoring_session(
    course_id: str,
    assignment_id: str,
    scoring_guidance: str = "",
    *,
    refresh_course,
    save_session=session_store.save_session,
) -> dict:
    """Refresh once, classify and prepare one exact assignment from the mirror."""
    course_id = str(course_id or "").strip()
    assignment_id = str(assignment_id or "").strip()
    if not course_id or not assignment_id:
        return _typed_failure(
            "invalid_scope", "validate", retryable=False,
            user_action="Provide both course_id and assignment_id for one exact assignment.",
            error="course_id and assignment_id are required.",
        )
    if not workspace.workspace_root():
        return _typed_failure(
            "workspace_unavailable", "prepare", retryable=True,
            user_action="Open Canvas Expert on this computer, then retry preparation.",
            error="The private workspace is unavailable.",
        )

    try:
        refreshed = bool(refresh_course(course_id))
    except Exception:
        refreshed = False
    if not refreshed:
        return _typed_failure(
            "mirror_refresh_failed", "refresh", retryable=True,
            user_action="Retry preparation after the Current course mirror refresh completes.",
            error="CanvasMirror could not be refreshed for this assignment.",
        )

    try:
        submissions, assignment, mirror_result = assignment_refresh.prepare_assignment_from_mirror(
            course_id, assignment_id,
        )
    except Exception:
        submissions, assignment, mirror_result = None, None, {
            "code": "mirror_projection_unavailable",
        }
    mirror_result = mirror_result if isinstance(mirror_result, dict) else {}
    if mirror_result.get("error") or not isinstance(assignment, dict):
        code = str(mirror_result.get("code") or "mirror_projection_unavailable")
        return _typed_failure(
            code, "mirror", retryable=True,
            user_action="Refresh the Current course mirror, then retry this exact assignment.",
            error="The refreshed CanvasMirror projections could not safely prepare this assignment.",
            assignment_name=str((assignment or {}).get("name") or assignment_id),
        )

    assignment_name = str(assignment.get("name") or assignment_id)
    assignment_description = html_to_text(assignment.get("description") or "")
    try:
        points_possible = float(assignment.get("points_possible") or 100)
    except (TypeError, ValueError):
        points_possible = 100.0

    is_new_quiz = (
        assignment.get("is_quiz_lti_assignment") is True
        or assignment.get("quiz_kind") == "new_quiz"
    )
    if is_new_quiz:
        message = (
            "Grade this New Quiz writing in Canvas. For future assessments, "
            "author each writing portion as a separate 100-point AssignmentForge assignment."
        )
        return _typed_failure(
            "new_quiz_writing_requires_assignment", "classify", retryable=False,
            user_action=message, error=message, assignment_name=assignment_name,
        )

    if not submissions:
        if mirror_result.get("historical_only"):
            return _typed_failure(
                "nothing_to_grade", "select", retryable=False,
                user_action="Choose an assignment with current submitted writing to score.",
                error="This assignment has no longer-actionable submitted work.",
                assignment_name=assignment_name,
            )
        return _typed_failure(
            "nothing_to_grade", "select", retryable=False,
            user_action="Choose an assignment with current submitted writing to score.",
            error="No current submissions are available for this assignment.",
            assignment_name=assignment_name,
        )

    submitted = [row for row in submissions if gradebook_snapshot.needs_grading(row)]
    if not submitted:
        return _typed_failure(
            "nothing_to_grade", "select", retryable=False,
            user_action="Choose an assignment with current submitted writing to score.",
            error="This assignment has no current submitted work needing grading.",
            assignment_name=assignment_name,
        )

    rubric_text_override = None
    complete_guidance = None
    guidance_projection = None
    canvas_rubric = scoring_rubric_text(assignment.get("rubric"))
    if canvas_rubric:
        rubric_name = "Canvas rubric"
        rubric_text_override = canvas_rubric
        scoring_basis = {"source": "canvas_rubric", "label": "Canvas rubric"}
    elif str(scoring_guidance or "").strip():
        complete_guidance = str(scoring_guidance).strip()
        rubric_text_override, guidance_projection = project_teacher_scoring_guidance(complete_guidance)
        rubric_name = "Teacher scoring guidance"
        scoring_basis = {"source": "teacher_guidance", "label": "Teacher scoring guidance"}
    else:
        question = "What bounded scoring guidance should I follow for this assignment?"
        return {
            "ok": True,
            "status": "needs_teacher_input",
            "code": "needs_scoring_norms",
            "stage": "basis",
            "retryable": True,
            "user_action": question,
            "question": question,
            "assignment_name": assignment_name,
        }

    media_submissions = [row for row in submitted if row.get("submission_type") == "media_recording"]
    oral_passage = ""
    writing_timeline_tracked = writing_timeline.is_tracked_assignment(assignment)
    if writing_timeline_tracked:
        student_attachments.attach_writing_timelines(submitted, roster_submissions=submissions)

    session_id = str(uuid.uuid4())
    course_name = config.course_display_name(course_id)
    try:
        ai_result = ai_workflow.run_ai_workflow(
            mode="packet", submitted=submitted, assignment_name=assignment_name,
            assignment_description=assignment_description, course_id=course_id,
            course_name=course_name, assignment_id=assignment_id, session_id=session_id,
            rubric_name=rubric_name, rubric_text_override=rubric_text_override,
            persona_id="", source_text="", source_files_json="", source_uploads=None,
        )
    except Exception:
        ai_result = {"ok": False}
    if not isinstance(ai_result, dict) or not ai_result.get("ok"):
        return _typed_failure(
            "safe_preparation_failed", "prepare", retryable=True,
            user_action="The SAFE scoring packet could not be prepared. Retry this exact assignment.",
            error="The SAFE scoring packet could not be prepared.",
            assignment_name=assignment_name,
        )

    privacy_steps = list(ai_result.get("privacy_steps") or [])
    privacy_artifacts = dict(ai_result.get("privacy_artifacts") or {})
    students = session_builder.build_students(
        submitted=submitted,
        ai_by_uid=ai_result.get("ai_by_uid") or {},
        ai_item_by_uid=ai_result.get("ai_item_by_uid") or {},
        ai_failures=dict(ai_result.get("ai_failures") or {}),
        roster_settings=config.get_roster_student_settings(course_id),
        tier_map=config.roster_tier_by_id(course_id),
        monitored=config.get_monitored_students(),
        extra_time_map=_extra_time_map(config.get_extra_time(course_id)),
    )
    privacy_artifacts, privacy_steps = _append_privacy_audit(
        privacy_artifacts=privacy_artifacts, assignment_name=assignment_name,
        session_id=session_id, course_id=course_id, assignment_id=assignment_id,
        privacy_steps=privacy_steps,
    )
    session = session_builder.build_session(
        session_id=session_id, course_id=course_id, assignment_id=assignment_id,
        assignment_name=assignment_name, points_possible=points_possible, mode="packet",
        rubric_name=rubric_name, persona_id="", selected_model="",
        assignment_description=assignment_description, response_kind="scr",
        privacy_steps=privacy_steps, privacy_artifacts=privacy_artifacts,
        students=students, mode_label=session_store.mode_label("packet"),
        copilot_packet=ai_result.get("copilot_packet"),
        late_watch={
            "enabled": False, "supported": False,
            "reason": "A Scoring Session is a snapshot; prepare a new exact assignment for later work.",
            "known_user_ids": sorted({str(row.get("user_id")) for row in submitted if row.get("user_id")}),
            "scored_user_ids": [], "generated_user_ids": [],
        }, evidence_manifest=mirror_result.get("manifest_path"),
        evidence_status=mirror_result.get("status", "mirror"),
        oral_reading_passage={"enabled": False}, session_kind=SCORING_SESSION_KIND,
    )
    session["status"] = "ready"
    session["assignment"] = {"points_possible": points_possible}
    session["writing_timeline_tracked"] = writing_timeline_tracked
    session["feedback_pattern_id"] = "basic"
    session["scoring_basis"] = scoring_basis
    session["scoring_rubric_text"] = complete_guidance if complete_guidance is not None else rubric_text_override
    if guidance_projection is not None:
        session["effective_scoring_rubric_text"] = rubric_text_override
        session["scoring_guidance_projection"] = guidance_projection
    try:
        save_session(session)
    except Exception:
        return _typed_failure(
            "session_store_unavailable", "persist", retryable=True,
            user_action="The private Scoring Session could not be saved. Retry this exact assignment.",
            error="The private Scoring Session could not be saved.",
            assignment_name=assignment_name,
        )
    return _ready_payload(session)
