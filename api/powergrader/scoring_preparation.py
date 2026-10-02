"""Single owner for assignment-scoped Scoring Session preparation."""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import uuid
from datetime import datetime, timezone
from api.nq_report import html_to_text
from api.platform_services import config, workspace
from api.powergrader import (
    assignment_refresh,
    assignmentforge,
    context,
    media_recordings,
    scoring_artifacts,
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


GUIDANCE_PROVENANCES = frozenset({"teacher_authored", "inherited", "default", "unknown"})


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


def _guidance_units(text: str) -> list[str]:    return [unit.strip() for unit in re.split(r"(?:\r?\n){1,2}", text) if unit.strip()]


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


def _safe_bundle_path(session: dict) -> str:
    raw = (session.get("privacy_artifacts") or {}).get("safe_bundle") or ""
    resolved = workspace.extended_path(raw) if raw else ""
    return resolved if resolved and os.path.isfile(resolved) else ""


def _ready_payload(session: dict) -> dict:
    """Return only identity-free facts after the private session is persisted."""
    from api.powergrader import scoring_packet

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
            "packet_missing", "prepare", retryable=True,
            user_action="The SAFE scoring packet could not be verified. Retry preparation.",
        )
    try:
        with open(bundle_path, encoding="utf-8") as handle:
            safe_bundle = json.load(handle)
        verdict = scoring_packet.validate_safe_bundle(safe_bundle)
        if not verdict.get("ok"):
            return _typed_failure(
                "packet_invalid", "prepare", retryable=True,
                user_action="The SAFE scoring packet is invalid. Retry preparation.",
            )
        page = scoring_packet.build_packet(
            session=session, safe_bundle=safe_bundle, offset=0, limit=1,
            include_context=False,
        )
    except FileNotFoundError:
        return _typed_failure(
            "packet_missing", "prepare", retryable=True,
            user_action="The SAFE scoring packet could not be verified. Retry preparation.",
        )
    except scoring_packet.ContractTooLarge as error:
        return _typed_failure(
            error.code, "packet", retryable=False,
            user_action=(
                "Edit or replace the selected feedback contract so it fits the page-zero "
                "transport budget, then prepare this exact assignment again."
            ),
            error=str(error), contract_file=error.contract_file,
            projected_tokens=error.projected_tokens,
            token_limit=scoring_packet._TOKEN_BUDGET,
        )
    except Exception:
        return _typed_failure(
            "packet_invalid", "prepare", retryable=True,
            user_action="The SAFE scoring packet could not be verified. Retry preparation.",
        )
    result.update({
        "student_count": len(session.get("students") or []),
        "response_count": int(page.get("total") or 0),
        "held": int(page.get("held") or 0),
        "next": "Call get_scoring_packet with scoring_session_id and read every page.",
    })
    result.update({
        "mirror_revision": session.get("mirror_revision"),
        "snapshot_id": session.get("mirror_snapshot_id") or "",
        "submission_snapshot": session.get("submission_snapshot") or "",
        "packet_health": scoring_packet.validate_safe_bundle(safe_bundle),
    })
    return result


def _freshness_refusal(freshness: dict, use_existing_mirror: bool) -> dict | None:
    """The one freshness gate shared by preparation and session refresh."""
    if (str(freshness.get("projection_state", freshness.get("state")) or "").casefold() != "current"
            or not str(freshness.get("last_success_at") or "").strip()):
        return _typed_failure(
            "mirror_projection_unavailable", "freshness", retryable=True,
            user_action="Refresh the Current course mirror, then retry this exact assignment.",
            error="The local CanvasMirror projection is unavailable or not current.",
        )
    if (freshness.get("requires_teacher_confirmation")
            and not bool(use_existing_mirror)):
        return _typed_failure(
            "mirror_freshness_confirmation_required", "freshness", retryable=True,
            user_action=(
                "Ask whether relevant Canvas work changed since this snapshot. If not, "
                "retry this exact call with use_existing_mirror=true; if yes or "
                "unsure, wait for an explicit teacher request to refresh."
            ),
            error="The local CanvasMirror snapshot needs teacher freshness confirmation.",
            freshness={
                "last_success_at": str(freshness.get("last_success_at") or ""),
                "age_minutes": int(freshness.get("age_minutes") or 0),
                "requires_teacher_confirmation": True,
            },
        )
    return None


def prepare_scoring_session(
    course_id: str,
    assignment_id: str,
    scoring_guidance: str = "",
    *,
    use_existing_mirror: bool = False,
    scoring_guidance_provenance: str = "",
    feedback_contract_id: str = "",
    late_policy: str = "ask",
    save_session=None,
    activate_session=None,
) -> dict:
    """Prepare one exact assignment from the local CanvasMirror.

    The successful save goes through the lifecycle owner so the newest
    preparation becomes the one current session and earlier actionable records
    for the same exact scope are superseded. ``save_session`` remains an
    injectable seam for focused tests; it is passed to the owner rather than
    used directly, so production preparation cannot bypass activation.
    """
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

    # Guidance, provenance, and the selected contract are teacher-authored
    # private state. Keep them available across a freshness decision; a retry
    # need not ask the same question again or silently promote inherited
    # guidance to teacher-authored.
    preparation_state = session_store.load_preparation_state(course_id, assignment_id)
    supplied_guidance = str(scoring_guidance or "").strip()
    supplied_provenance = str(scoring_guidance_provenance or "").strip().casefold()
    supplied_contract_id = str(feedback_contract_id or "").strip()
    if not (supplied_guidance or supplied_contract_id):
        # Provenance describes guidance; with none supplied it means nothing and
        # must not be stored, replayed, or rejected.
        supplied_provenance = ""
    if supplied_provenance and supplied_provenance not in GUIDANCE_PROVENANCES:
        # Reject before save_preparation_state persists it; a bad value saved
        # here would be replayed by every later bare retry.
        return _typed_failure(
            "invalid_scoring_guidance_provenance", "basis", retryable=False,
            user_action=("Use one of: " + ", ".join(sorted(GUIDANCE_PROVENANCES))
                         + " as the guidance provenance."),
            error="The scoring guidance provenance is invalid.",
        )
    if supplied_guidance or supplied_contract_id:
        if not supplied_provenance:
            supplied_provenance = "teacher_authored" if supplied_guidance else ""
        session_store.save_preparation_state(
            course_id, assignment_id, scoring_guidance=supplied_guidance,
            scoring_guidance_provenance=supplied_provenance,
            feedback_contract_id=supplied_contract_id,
        )
        scoring_guidance = supplied_guidance
        scoring_guidance_provenance = supplied_provenance
    else:
        scoring_guidance = preparation_state.get("scoring_guidance") or ""
        if not supplied_provenance:
            scoring_guidance_provenance = preparation_state.get("scoring_guidance_provenance") or ""
        if not supplied_contract_id:
            feedback_contract_id = preparation_state.get("feedback_contract_id") or ""

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
    raw_points_possible = assignment.get("points_possible")
    try:
        points_possible = float(raw_points_possible)
    except (TypeError, ValueError, OverflowError):
        points_possible = None
    if (isinstance(raw_points_possible, bool) or points_possible is None
            or not math.isfinite(points_possible) or points_possible < 0):
        return _typed_failure(
            "assignment_points_unavailable", "points", retryable=True,
            user_action="Refresh the Current course mirror and retry after Canvas provides a finite, nonnegative points_possible value.",
            error="Canvas assignment points are missing or invalid; no total was inferred.",
            assignment_name=assignment_name,
        )

    is_new_quiz = (
        assignment.get("is_quiz_lti_assignment") is True
        or assignment.get("quiz_kind") == "new_quiz"
    )
    if is_new_quiz:
        message = (
            "Grade this New Quiz writing in Canvas. For future assessments, "
            "author each writing portion as a separate AssignmentForge assignment with teacher-chosen points."
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

    submitted = session_store.eligible_submission_rows(submissions)
    if not submitted:
        return _typed_failure(
            "nothing_to_grade", "select", retryable=False,
            user_action="Choose an assignment with current submitted writing to score.",
            error="This assignment has no current submitted work needing grading.",
            assignment_name=assignment_name,
        )

    freshness = mirror_result.get("freshness") or {}
    freshness_refusal = _freshness_refusal(freshness, use_existing_mirror)
    if freshness_refusal:
        return freshness_refusal

    rubric_text_override = None
    complete_guidance = None
    guidance_projection = None
    guidance = str(scoring_guidance or "").strip()
    provenance = str(scoring_guidance_provenance or "").strip().casefold()
    if guidance and not provenance:
        provenance = "teacher_authored"
    if provenance and provenance not in GUIDANCE_PROVENANCES:
        return _typed_failure(
            "invalid_scoring_guidance_provenance", "basis", retryable=False,
            user_action=("Use one of: " + ", ".join(sorted(GUIDANCE_PROVENANCES))
                         + " as the guidance provenance."),
            error="The scoring guidance provenance is invalid.",
            assignment_name=assignment_name,
        )
    authoritative_guidance = guidance if provenance in {"", "teacher_authored"} else ""

    # The base Glows/Grows shape is product-owned (api/feedback_contract.py)
    # and always present; it is never selected here. An explicit workspace
    # file is an optional layer on top of it, under the TEACHER GUIDANCE
    # heading. Conversational scoring guidance is not a second layer here: it
    # already layers onto the scoring basis, further down, as the existing
    # TEACHER DIRECTIVE block. It is never a substitute feedback contract.
    contract_id = str(feedback_contract_id or "").strip()
    contract_source = ""
    contract_name = ""
    contract_filename = ""
    contract_body = ""
    if contract_id:
        selected_contract = config.get_feedback_contract(contract_id)
        if not selected_contract:
            return _typed_failure(
                "feedback_contract_not_found", "contract", retryable=False,
                user_action="Choose one of the contracts returned by list_feedback_contracts().",
                error="The selected feedback contract was not found in the private workspace.",
                assignment_name=assignment_name,
            )
        contract_source = "file"
        contract_name = str(selected_contract.get("name") or contract_id)
        contract_filename = os.path.basename(str(selected_contract.get("path") or ""))
        contract_body = str(selected_contract.get("body") or "")

    canvas_rubric = scoring_rubric_text(assignment.get("rubric"))
    basis_sections = []
    if assignment_description.strip():
        basis_sections.append(("ASSIGNMENT DIRECTIONS AND CONTENT", assignment_description.strip()))
    if canvas_rubric:
        basis_sections.append(("CANVAS RUBRIC", canvas_rubric))
    if authoritative_guidance:
        complete_guidance = authoritative_guidance
        projected_guidance, guidance_projection = project_teacher_scoring_guidance(complete_guidance)
        basis_sections.append(("TEACHER DIRECTION", projected_guidance))
    if not basis_sections:
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
    rubric_name = "Scoring basis"
    rubric_text_override = "\n\n".join(
        f"--- {label} ---\n{text}" for label, text in basis_sections
    )
    scoring_basis = {
        "source": "teacher_directed" if authoritative_guidance else "assignment_context",
        "label": "Teacher direction and available assignment context" if authoritative_guidance
        else "Assignment directions and Canvas rubric",
        "components": [
            {"source": label.casefold().replace(" ", "_"), "label": label}
            for label, _text in basis_sections
        ],
        "teacher_direction_precedence": bool(authoritative_guidance),
    }

    writing_timeline_tracked = writing_timeline.is_tracked_assignment(assignment)
    if writing_timeline_tracked:
        student_attachments.attach_writing_timelines(submitted, roster_submissions=submissions)

    session_id = str(uuid.uuid4())
    course_name = config.course_display_name(course_id)
    try:
        ai_result = scoring_artifacts.build_scoring_artifacts(
            submitted=submitted, assignment_name=assignment_name,
            assignment_description=assignment_description, course_id=course_id,
            course_name=course_name, assignment_id=assignment_id, session_id=session_id,
            protected=config.active_protected_names(),
        )
    except Exception:
        ai_result = {"ok": False}
    if not isinstance(ai_result, dict) or not ai_result.get("ok"):
        if isinstance(ai_result, dict) and ai_result.get("code") == "pseudonym_provisional":
            return _typed_failure(
                "pseudonym_provisional", "privacy", retryable=False,
                user_action="Resolve the provisional pseudonym in the local roster before preparing this scoring packet.",
                error="A student has a provisional pseudonym assignment.",
                assignment_name=assignment_name,
            )
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
        monitored=config.get_monitored_students(),
        extra_time_map=_extra_time_map(config.get_extra_time(course_id)),
    )
    session = session_builder.build_session(
        session_id=session_id, course_id=course_id, assignment_id=assignment_id,
        assignment_name=assignment_name, points_possible=points_possible, mode="packet",
        rubric_name=rubric_name, selected_model="",
        assignment_description=assignment_description, response_kind="scr",
        privacy_steps=privacy_steps, privacy_artifacts=privacy_artifacts,
        students=students, mode_label=session_store.mode_label("packet"),
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
    # New sessions use shared immutable snapshots and a cross-device lease.
    # Legacy in-flight records remain readable/writable in their existing
    # store until they reach a terminal state.
    session["storage_model"] = "shared_work.v1"
    session["assignment"] = {"points_possible": points_possible}
    session["writing_timeline_tracked"] = writing_timeline_tracked
    session["feedback_contract_id"] = contract_id
    session["feedback_contract_source"] = contract_source
    session["feedback_contract_name"] = contract_name
    session["feedback_contract_filename"] = contract_filename
    session["feedback_contract_text"] = contract_body
    session["feedback_contract_digest"] = hashlib.sha256(
        contract_body.encode("utf-8")
    ).hexdigest()
    session["scoring_basis"] = scoring_basis
    session["scoring_guidance_provenance"] = provenance or None
    session["late_policy"] = str(late_policy or "ask")
    session["scoring_freshness"] = {
        "state": str(freshness.get("state") or "current"),
        "last_success_at": str(freshness.get("last_success_at") or ""),
        "age_minutes": int(freshness.get("age_minutes") or 0),
        "requires_teacher_confirmation": bool(freshness.get("requires_teacher_confirmation")),
        "use_existing_mirror": bool(use_existing_mirror),
    }
    session["mirror_revision"] = (mirror_result.get("mirror_revision")
                                   or mirror_result.get("revision")
                                   or mirror_result.get("snapshot_id"))
    session["mirror_snapshot_id"] = str(mirror_result.get("snapshot_id") or "")
    session["submission_snapshot"] = session_store.eligible_submission_snapshot_digest(submissions)
    session["submission_snapshot_count"] = len(submitted)
    session["scoring_rubric_text"] = rubric_text_override
    session["teacher_scoring_guidance"] = complete_guidance or ""
    assignmentforge_metadata = assignmentforge.for_assignment(course_id, assignment_id)
    if assignmentforge_metadata.get("corrections"):
        session["assignmentforge_corrections"] = assignmentforge_metadata["corrections"]
        if assignmentforge_metadata.get("tier"):
            session["assignmentforge_tier"] = assignmentforge_metadata["tier"]
    if guidance_projection is not None:
        session["effective_scoring_rubric_text"] = rubric_text_override
        session["scoring_guidance_projection"] = guidance_projection

    # Verify the SAFE packet, contract, and required page envelope before the
    # session becomes the current actionable record. In particular, an
    # oversized contract must not leave an apparently usable session that
    # blocks the teacher from replacing it.
    ready = _ready_payload(session)
    if not ready.get("ok"):
        return ready
    try:
        _activate(session, activate_session=activate_session, save_session=save_session)
    except Exception:
        return _typed_failure(
            "session_store_unavailable", "persist", retryable=True,
            user_action="The private Scoring Session could not be saved. Retry this exact assignment.",
            error="The private Scoring Session could not be saved.",
            assignment_name=assignment_name,
        )
    session_store.clear_preparation_state(course_id, assignment_id)
    return ready


def _activate(session: dict, *, activate_session, save_session) -> list[dict]:
    """Save a prepared session through the single lifecycle owner."""
    if activate_session is not None:
        return activate_session(session) or []
    if save_session is None:
        save_session = session_store.save_session
    return session_store.activate_scoring_session(session, save_session=save_session)


_REFRESHABLE_STATUSES = session_store.ACTIONABLE_STATUSES
_TERMINAL_STATUSES = frozenset({"completed", "completed_with_holds"})


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_time(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _is_resubmission(row: dict, baseline) -> bool:
    """True when a mirror row is a newer submission than the session's baseline.

    The baseline is the ``submission_baseline`` stored on each session student
    ({attempt, submitted_at}). A record that stored neither is an unknown
    baseline and is never reported as resubmitted.
    """
    if not isinstance(baseline, dict):
        return False
    base_attempt, base_at = _as_int(baseline.get("attempt")), _as_time(baseline.get("submitted_at"))
    if base_attempt is None and base_at is None:
        return False
    attempt, at = _as_int(row.get("attempt")), _as_time(row.get("submitted_at"))
    if attempt is not None and base_attempt is not None and attempt != base_attempt:
        return attempt > base_attempt
    if at is not None and base_at is not None:
        return at > base_at
    return False


def _first_new_offset(session: dict, bundle: dict, new_pseudonyms: set[str]) -> int | None:
    """Bundle row offset of the first appended student's first scorable row."""
    from api.powergrader import scoring_packet

    if not new_pseudonyms:
        return None
    offset = 0
    while True:
        page = scoring_packet.build_packet(
            session=session, safe_bundle=bundle, offset=offset, limit=500,
            include_context=False,
        )
        for index, row in enumerate(page.get("students") or []):
            if row.get("pseudonym") in new_pseudonyms:
                return offset + index
        if page.get("next_offset") is None:
            return None
        offset = int(page["next_offset"])


def _pseudonyms_by_user_id() -> dict[str, str]:
    return {str(entry.get("canvas_id")): str(entry.get("pseudonym") or "")
            for entry in context.vault().entries() if entry.get("pseudonym")}


def refresh_scoring_session(
    scoring_session_id: str,
    *,
    use_existing_mirror: bool = False,
    replace_resubmitted: bool = False,
    load_session=None,
    save_session=None,
) -> dict:
    """Bring late and resubmitted mirror work into one open Scoring Session.

    Local mirror only: no Canvas call and no mirror refresh. The caller holds the
    scope lock and then the session lock (the same order as staging). Rows
    already staged or posted, ``push_idempotency``, ``push_log``, and history are
    never rewritten; new students and replaced resubmissions are appended to a
    new merged SAFE bundle file while the earlier bundle file stays on disk.
    """
    from api.powergrader import scoring_packet

    load_session = load_session or session_store.load_session
    save_session = save_session or session_store.save_session

    def refuse(code, user_action, *, retryable=False, **extra):
        return _typed_failure(code, "refresh", retryable=retryable,
                              user_action=user_action, **extra)

    session = load_session(str(scoring_session_id or ""))
    if (not isinstance(session, dict)
            or session.get("session_kind") != SCORING_SESSION_KIND
            or str(session.get("session_id") or "") != str(scoring_session_id or "")):
        return refuse("session_not_found",
                      "Use a scoring_session_id from list_scoring_sessions().")
    status = str(session.get("status") or "")
    if status == session_store.SUPERSEDED_STATUS:
        return refuse("session_superseded",
                      "Use the current session listed by list_scoring_sessions().")
    if status in _TERMINAL_STATUSES:
        return refuse("session_completed",
                      "This session is finished; call prepare_scoring_session for this "
                      "exact assignment instead.")
    if status not in _REFRESHABLE_STATUSES:
        return refuse("session_not_refreshable",
                      "This session has no open packet to refresh; list_scoring_sessions() "
                      "shows the usable session.")
    students = [s for s in session.get("students") or [] if isinstance(s, dict)]
    if any(s.get("push_state") == "sent_unknown" for s in students):
        return refuse("canvas_write_attention",
                      "A previous Canvas write could not be confirmed. Review Canvas "
                      "before refreshing this session.")
    if not workspace.workspace_root():
        return refuse("workspace_unavailable",
                      "Open Canvas Expert on this computer, then retry.", retryable=True)

    course_id = str(session.get("course_id") or "")
    assignment_id = str(session.get("assignment_id") or "")
    try:
        submissions, assignment, mirror_result = assignment_refresh.prepare_assignment_from_mirror(
            course_id, assignment_id)
    except Exception:
        submissions, assignment, mirror_result = None, None, {"code": "mirror_projection_unavailable"}
    mirror_result = mirror_result if isinstance(mirror_result, dict) else {}
    if mirror_result.get("error") or not isinstance(assignment, dict):
        return refuse(str(mirror_result.get("code") or "mirror_projection_unavailable"),
                      "Refresh the Current course mirror, then retry this exact call.",
                      retryable=True)
    freshness = mirror_result.get("freshness") or {}
    freshness_refusal = _freshness_refusal(freshness, use_existing_mirror)
    if freshness_refusal:
        return freshness_refusal

    old_bundle_path = _safe_bundle_path(session)
    try:
        with open(old_bundle_path, encoding="utf-8") as handle:
            base_bundle = json.load(handle)
    except (OSError, ValueError):
        base_bundle = None
    if not scoring_packet.validate_safe_bundle(base_bundle).get("ok"):
        return refuse("packet_invalid", "The SAFE scoring packet is missing or invalid.",
                      retryable=True)

    eligible = session_store.eligible_submission_rows(submissions)
    rows_by_uid = {str(row.get("user_id")): row for row in eligible if row.get("user_id")}
    session_uids = {str(s.get("user_id")) for s in students if s.get("user_id") is not None}
    added_rows = [row for row in eligible
                  if row.get("user_id") and str(row["user_id"]) not in session_uids]
    resubmitted, posted_resubmitted, replaced_uids = [], [], []
    for student in students:
        uid = str(student.get("user_id"))
        row = rows_by_uid.get(uid)
        if row is None or not _is_resubmission(row, student.get("submission_baseline")):
            continue
        if student.get("posted") or student.get("status") == "posted":
            posted_resubmitted.append(uid)
        elif replace_resubmitted:
            replaced_uids.append(uid)
        else:
            resubmitted.append(uid)

    mirror_fields = {
        "mirror_revision": (mirror_result.get("mirror_revision") or mirror_result.get("revision")
                            or mirror_result.get("snapshot_id")),
        "mirror_snapshot_id": str(mirror_result.get("snapshot_id") or ""),
        "submission_snapshot": session_store.eligible_submission_snapshot_digest(submissions),
        "submission_snapshot_count": len(eligible),
    }

    def labels(uids, names):
        return sorted(names.get(str(uid)) or "(unknown student)" for uid in uids)

    try:
        names = _pseudonyms_by_user_id()
    except Exception:
        return refuse("safe_refresh_failed", "Retry the refresh.", retryable=True)
    report = {
        "ok": True, "scoring_session_id": session["session_id"],
        "resubmitted_not_replaced": labels(resubmitted, names),
        "posted_resubmitted": labels(posted_resubmitted, names),
    }

    if not added_rows and not replaced_uids:
        if any(session.get(key) != value for key, value in mirror_fields.items()):
            session.update(mirror_fields)
            save_session(session)
        return {**report, "changed": False, "added": [], "replaced": [], "held_added": 0,
                "first_new_offset": None,
                "packet_digest": scoring_packet.packet_digest(
                    session["session_id"], base_bundle, course_id=course_id,
                    assignment_id=assignment_id)}

    replaced = set(replaced_uids)
    added_ids = {str(row["user_id"]) for row in added_rows}
    delta_rows = [row for row in eligible
                  if row.get("user_id") and str(row["user_id"]) in (replaced | added_ids)]
    if writing_timeline.is_tracked_assignment(assignment):
        student_attachments.attach_writing_timelines(delta_rows, roster_submissions=submissions)
    history = list(session.get("scoring_refreshes") or [])
    try:
        ai_result = scoring_artifacts.build_scoring_artifacts(
            submitted=delta_rows, assignment_name=str(session.get("assignment_name") or assignment_id),
            assignment_description=str(session.get("assignment_description") or ""),
            course_id=course_id, course_name=config.course_display_name(course_id),
            assignment_id=assignment_id, session_id=session["session_id"],
            protected=config.active_protected_names(),
            base_bundle=base_bundle, drop_canvas_ids=replaced_uids,
            file_label=f"refresh-{len(history) + 1}",
        )
    except Exception:
        ai_result = {"ok": False}
    if not isinstance(ai_result, dict) or not ai_result.get("ok"):
        if isinstance(ai_result, dict) and ai_result.get("code") == "pseudonym_provisional":
            return refuse("pseudonym_provisional",
                          "Resolve the provisional pseudonym in the local roster, then retry.")
        return refuse("safe_refresh_failed",
                      "The SAFE scoring packet could not be refreshed. Retry this call.",
                      retryable=True)
    try:
        names = _pseudonyms_by_user_id()
    except Exception:
        return refuse("safe_refresh_failed", "Retry the refresh.", retryable=True)

    new_students = session_builder.build_students(
        submitted=delta_rows, ai_by_uid={}, ai_item_by_uid={},
        ai_failures=dict(ai_result.get("ai_failures") or {}),
        monitored=config.get_monitored_students(),
        extra_time_map=_extra_time_map(config.get_extra_time(course_id)),
    )
    fresh_by_uid = {str(s["user_id"]): s for s in new_students}
    session["students"] = [
        fresh_by_uid[str(s.get("user_id"))] if str(s.get("user_id")) in replaced else s
        for s in session.get("students") or []
    ] + [fresh_by_uid[str(row["user_id"])] for row in added_rows
         if str(row["user_id"]) in fresh_by_uid]

    artifacts = dict(session.get("privacy_artifacts") or {})
    fresh_artifacts = dict(ai_result.get("privacy_artifacts") or {})
    history.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "previous_safe_bundle": artifacts.get("safe_bundle"),
                    "added": len(added_rows), "replaced": len(replaced_uids)})
    for key in ("attachment_only_count", "excluded_count", "media_hold_count"):
        artifacts[key] = int(artifacts.get(key) or 0) + int(fresh_artifacts.get(key) or 0)
    artifacts.update({key: fresh_artifacts[key]
                      for key in ("safe_folder", "safe_bundle", "safe_students")
                      if key in fresh_artifacts})
    session["privacy_artifacts"] = artifacts
    session["scoring_refreshes"] = history
    session.update(mirror_fields)
    session.pop("staged_scoring_apply", None)
    session["status"] = "ready"

    ready = _ready_payload(session)
    if not ready.get("ok"):
        return ready
    with open(_safe_bundle_path(session), encoding="utf-8") as handle:
        merged = json.load(handle)
    first_offset = _first_new_offset(
        session, merged, set(ai_result.get("appended_pseudonyms") or []))
    try:
        save_session(session)
    except Exception:
        return refuse("session_store_unavailable",
                      "The refreshed Scoring Session could not be saved. Retry this call.",
                      retryable=True)
    scorable = {
        str(s.get("pseudonym")) for s in merged.get("students") or []
        if any(str(r.get("response") or "").strip() or r.get("oral_reading")
               for r in s.get("responses") or [])
    }
    return {
        **report, "changed": True,
        "added": labels(added_ids, names),
        "replaced": labels(replaced_uids, names),
        "held_added": sum(1 for uid in [*added_ids, *replaced_uids]
                          if names.get(uid) not in scorable),
        "first_new_offset": first_offset,
        "packet_digest": scoring_packet.packet_digest(
            session["session_id"], merged, course_id=course_id, assignment_id=assignment_id),
    }
