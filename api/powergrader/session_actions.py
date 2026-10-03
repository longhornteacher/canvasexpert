"""PowerGrader session actions — save grades and the narrow Canvas write.

The ordinary Assignment write is deliberately narrow: send the reviewed raw
score and plain-text comment to the Canvas Submissions endpoint once, then
record the transport outcome. This module does not read the resulting grade
back, compare it, or interpret any gradebook or late-policy adjustment Canvas
applies (the apply layer verifies every posted numeric score once, in scoring_apply).
A Canvas HTTP success means the write was accepted; a connection loss
is transport-unknown and is never automatically retried or re-read.

See docs/contracts/feedback-scoring-contract.md (Session consumption and write
safety) and docs/reference/powergrader-scoring-map.md.
"""

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from functools import wraps

from api import grading_policy
from api import score_ledger
from api.feedback_results import _format_number
from api.powergrader import attribution
from api.powergrader import blind_first
from api.powergrader import session_store
from api.student_text import normalize_student_text


def _session_locked(func):
    """Keep injected-loader actions inside the authoritative session lock."""
    @wraps(func)
    def wrapped(session_id, *args, **kwargs):
        with session_store.session_lock(session_id):
            return func(session_id, *args, **kwargs)
    return wrapped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _plain_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(value)[:10] or None


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _feedback_digest(value) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _finite_number(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _late_days_in_payload(payload: dict) -> int | None:
    submission = (payload or {}).get("submission") or {}
    status = submission.get("late_policy_status")
    if status == "none":
        return 0
    if status == "late":
        try:
            return max(0, int(submission.get("seconds_late_override") or 0) // 86400)
        except (TypeError, ValueError):
            return None
    return None


def correction_changed(student: dict, session=None, *, grade_mode="post_score", waive_late=None) -> bool:
    """Whether the current staged values differ from a verified prior push."""
    prior = student.get("last_posted")
    if (not isinstance(prior, dict) or not prior.get("event_id")
            or _finite_number(prior.get("entered_score")) is None):
        return False
    if waive_late is None:
        waive_late = late_waived(session or {}, student, grade_mode=grade_mode)
    full_student = dict(student)
    full_student["correction_pending"] = False
    if full_student.get("teacher_score") is None:
        full_student["teacher_score"] = full_student.get("ai_score")
    if not (full_student.get("teacher_feedback") or "").strip():
        full_student["teacher_feedback"] = full_student.get("ai_feedback") or ""
    current = _payload(full_student, grade_mode=grade_mode, waive_late=waive_late)
    sent_score = (current.get("submission") or {}).get("posted_grade")
    try:
        score_changed = (sent_score is None or prior.get("entered_score") is None
                         or abs(float(sent_score) - float(prior["entered_score"])) > 1e-9)
    except (TypeError, ValueError):
        score_changed = True
    old_days = prior.get("late_days")
    new_days = _late_days_in_payload(current)
    feedback = (current.get("comment") or {}).get("text_comment") or ""
    feedback_changed = _feedback_digest(feedback) != str(prior.get("feedback_digest") or "")
    return score_changed or old_days != new_days or feedback_changed


def hydrate_last_posted(session: dict, student: dict, events=None) -> dict | None:
    """Recover only a same-session push joined to a verified ledger event."""
    student.pop("last_posted", None)
    try:
        events = events if events is not None else score_ledger.list_events(
            str(session.get("course_id") or ""), str(session.get("assignment_id") or ""))
    except Exception:
        return None
    baseline = student.get("submission_baseline") or {}
    uid = str(student.get("user_id") or "")
    attempt = str(baseline.get("attempt") or student.get("current_attempt") or "")
    verified = [event for event in events
                if event.get("source") == "ce_apply" and event.get("action") == "verified"
                and str(event.get("session_id") or "") == str(session.get("session_id") or "")
                and str(event.get("student_id") or "") == uid
                and str(event.get("attempt") or "") == attempt]
    verified.sort(key=lambda event: (str(event.get("timestamp") or ""),
                                     str(event.get("event_id") or "")))
    successful = sorted(
        [(str(entry.get("ts") or ""), row)
         for entry in session.get("push_log") or []
         for row in entry.get("results") or []
         if str(row.get("user_id") or "") == uid
         and row.get("status") == "pushed" and row.get("request_digest")
         and _finite_number(row.get("entered_score")) is not None],
        key=lambda pair: (pair[0], str(pair[1].get("target_digest") or "")))
    if not successful:
        return None
    _logged_at, pushed = successful[-1]
    expected_key = (f"verify:{session.get('session_id')}:{pushed.get('target_digest') or pushed.get('request_digest')}"
                    + (f":corrects:{pushed.get('corrects_event_id')}"
                       if pushed.get("corrects_event_id") else "")
                    + ":verified")
    event = next((value for value in reversed(verified)
                  if str(value.get("logical_event_key") or "") == expected_key), None)
    if event is None:
        return None
    try:
        agrees = (abs(float(event.get("entered_score"))
                      - float(pushed.get("entered_score"))) <= 1e-6)
    except (TypeError, ValueError):
        agrees = False
    if not agrees:
        return None
    prior = {
        "event_id": event.get("event_id"),
        "payload_digest": str(pushed.get("request_digest") or ""),
        "entered_score": event.get("entered_score"),
        "late_days": ((pushed.get("late") or {}).get("late_days")
                      if (pushed.get("late") or {}).get("decision") != "waived"
                      else 0),
        "feedback_digest": event.get("feedback_sha256") or _feedback_digest(event.get("feedback")),
        "attempt": event.get("attempt"),
    }
    student["last_posted"] = prior
    return prior


def _parse_ids(raw: str, *, allow_empty: bool = True) -> tuple[list[str] | None, str | None]:
    if not raw.strip():
        return ([] if allow_empty else None), None if allow_empty else "invalid_selection"
    try:
        values = json.loads(raw)
    except Exception:
        return None, "invalid_selection"
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        return None, "invalid_selection"
    ids = [value for value in values if value.strip()]
    if len(ids) != len(set(ids)):
        return None, "invalid_selection"
    return ids, None


_DRAFT_BANNER_RE = re.compile(
    r"^-{4,}[ \t]+AI draft[ \t]+-{4,}[ \t]*\r?\n"
    r"[ \t]*AI score:[^\r\n]*(?:\r?\n|$)",
    re.IGNORECASE,
)


def _strip_draft_banner(feedback: str) -> str:
    """Remove only the legacy leading AI banner from outbound feedback."""
    return _DRAFT_BANNER_RE.sub("", feedback, count=1).strip()


def late_decision(student: dict, *, waive: bool = False,
                  grade_mode: str = "post_score") -> dict | None:
    """Return the teacher's late-day decision for one late Canvas row."""
    if grade_mode == "feedback_only" or not student.get("canvas_late"):
        return None
    if waive:
        return {"decision": "waived", "late_days": 0, "basis": "teacher_set"}
    grading = student.get("grading")
    if grading:
        days = grading.get("late_days")
        if days is None:
            days = grading.get("suggested_late_days")
        if days is not None:
            basis = "teacher_set" if grading.get("late_days") is not None else "first_meaningful_attempt"
            return {"decision": "set", "late_days": days, "basis": basis}
    return {"decision": "unknown", "late_days": None, "basis": "unknown"}


def late_waived(session: dict, student: dict, waive_user_ids=(),
                grade_mode: str = "post_score") -> bool:
    """Whether the session's late policy or a ``waive_late`` answer waives this row.

    Never in ``feedback_only`` mode: no late fields are sent, so the session's
    ``late_policy`` is ignored there.
    """
    if grade_mode == "feedback_only":
        return False
    return (str((session or {}).get("late_policy") or "ask") == "waive"
            or str(student.get("user_id")) in {str(uid) for uid in waive_user_ids})


def _payload(student: dict, *, grade_mode: str = "post_score",
             waive_late: bool = False) -> dict:
    # Single grading surface: PowerGrader is the only writer of an AI-feedback
    # submission comment (``comment[text_comment]``). Gradebook may adjust
    # ``posted_grade`` (curve/late/extension) but never writes feedback here.
    # See docs/reference/powergrader-scoring-map.md (Guardrails: single grading surface).
    score = student.get("teacher_score")
    grading = student.get("grading")
    if student.get("_teacher_authored_feedback"):
        # Scoring Session feedback has already passed the outbound privacy gate.
        # Preserve every authored character, including signatures and Markdown.
        feedback = student.get("teacher_feedback") or ""
    else:
        feedback = normalize_student_text(
            attribution.attribute(
                _strip_draft_banner((student.get("teacher_feedback") or "").strip())
            )
        )

    # Feedback-only scoring keeps the numeric draft in the rendered comment,
    # but must never send a grade or policy field to Canvas.
    decision = None
    if grade_mode == "feedback_only":
        posted_grade = None
        days = None
    # This is the only place the mark and late fields are computed, so the
    # projected payload in a plan digest is exactly what gets sent (see
    # docs/contracts/grading-policy-contract.md section 5). Without a
    # ``grading`` stamp this function is byte-identical to today.
    else:
        decision = late_decision(student, waive=waive_late)
        days = (decision.get("late_days")
                if decision and decision["decision"] == "set" else None)
        if grading and grading.get("floor_percent") is not None:
            points_possible = grading.get("points_possible")
            posted_grade = grading_policy.mark(
                score, points_possible, grading.get("floor_percent"), bool(grading.get("insincere")),
            )
            if (not student.get("_teacher_authored_feedback")
                    and posted_grade is not None and score is not None
                    and float(posted_grade) != float(score)):
                line = (f"Entered in the gradebook: {_format_number(posted_grade)}"
                        f"/{_format_number(points_possible)}.")
                feedback = f"{feedback}\n\n{line}" if feedback else line
        else:
            posted_grade = score

        frozen_curve = student.get("frozen_curve")
        if posted_grade is not None and isinstance(frozen_curve, dict):
            posted_grade = frozen_curve.get("entered_score")
            raw_value = frozen_curve.get("raw_score")
            entered_value = frozen_curve.get("entered_score")
            line = (f"Raw {_format_number(raw_value)} -> Entered "
                    f"{_format_number(entered_value)}.")
            if line not in feedback:
                feedback = f"{feedback}\n\n{line}" if feedback else line

    payload: dict = {}
    if posted_grade is not None:
        payload["submission"] = {"posted_grade": _format_number(posted_grade)}
        if decision and decision["decision"] == "waived":
            payload["submission"]["late_policy_status"] = "none"
        elif days is not None:
            if days > 0:
                payload["submission"]["late_policy_status"] = "late"
                payload["submission"]["seconds_late_override"] = days * 86400
            else:
                payload["submission"]["late_policy_status"] = "none"
    if feedback:
        payload["comment"] = {"text_comment": feedback}
    if student.get("correction_pending") and isinstance(student.get("last_posted"), dict):
        prior = student["last_posted"].get("feedback_digest")
        if prior and prior == _feedback_digest((payload.get("comment") or {}).get("text_comment")):
            payload.pop("comment", None)
    return payload


def _explicit_canvas_rejection(error: object) -> bool:
    """HTTP responses are explicit rejection; transport errors may have landed."""
    return str(error or "").lstrip().upper().startswith("HTTP ")


def _path(session: dict, user_id: str) -> str:
    return (
        f"/api/v1/courses/{session['course_id']}/assignments/{session['assignment_id']}"
        f"/submissions/{user_id}"
    )


@_session_locked
def save_grade(
    session_id: str,
    *,
    user_id: str,
    teacher_score: str,
    teacher_feedback: str,
    status: str,
    load_session,
    save_session,
) -> tuple[dict, int]:
    """Save one student's grade into a session."""
    session = load_session(session_id)
    if not session:
        return {"ok": False, "error": "Session not found."}, 404

    score_val = None
    if teacher_score.strip():
        try:
            score_val = float(teacher_score.strip())
        except ValueError:
            return {"ok": False, "error": "Invalid score value."}, 200

    for student in session["students"]:
        if student["user_id"] == user_id:
            student["teacher_score"] = score_val
            student["teacher_feedback"] = teacher_feedback.strip()
            resolved = status if status in ("approved", "skipped", "pending") else "approved"
            student["status"] = resolved
            # A teacher who scores and approves without ever revealing has produced
            # the cleanest blind datapoint there is; record it before it is lost.
            # Unlocked call: we already hold this session's lock.
            blind_first.record_implicit_blind(
                session, student, score_val, student["teacher_feedback"], resolved,
            )
            save_session(session)
            approved = sum(1 for item in session["students"] if item.get("status") == "approved")
            return {"ok": True, "approved": approved}, 200

    return {"ok": False, "error": "Student not found in session."}, 200


@_session_locked
def push_grades(
    session_id: str,
    *,
    user_ids: str,
    load_session,
    save_session,
    canvas_send,
    idempotency_key: str = "",
    grade_mode: str = "post_score",
    waive_late_user_ids=(),
) -> tuple[dict, int]:
    """Send the reviewed raw score and comment once, then record the outcome.

    No Canvas read happens here, before or after the send (the apply layer makes
    its own score verification). ``waive_late_user_ids`` are rows a ``waive_late``
    answer waived; the session's ``late_policy`` can waive every late row. A Canvas HTTP success
    finalizes the exact local idempotency slot; a non-HTTP transport error is
    ``write_transport_unknown`` and is never re-verified or automatically
    repeated; an explicit Canvas HTTP rejection is a failed write. Neither
    outcome may trigger a second submission comment write.
    """
    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404
    requested, error = _parse_ids(user_ids, allow_empty=False)
    if error:
        return {"ok": False, "code": error, "error": "Select valid submissions to post."}, 200

    students = {str(student.get("user_id")): student for student in session.get("students", [])}
    if any(
        user_id in students
        and ((students[user_id].get("teacher_score") is not None)
             or (students[user_id].get("ai_score") is not None))
        for user_id in requested
    ):
        try:
            score_ledger.validate_scope(str(session.get("course_id") or ""),
                                        str(session.get("assignment_id") or ""))
        except Exception:
            return {"ok": False, "code": "score_ledger_unavailable",
                    "error": "Private score evidence is unavailable. Nothing was sent."}, 200
    idempotency = session.setdefault("push_idempotency", {})
    request_key = str(idempotency_key or "")

    results = []
    pushed = 0
    for user_id in requested:
        student = students.get(user_id)
        waive = bool(student) and late_waived(
            session, student, waive_late_user_ids, grade_mode)
        if not student or not _payload(student, grade_mode=grade_mode, waive_late=waive):
            return {"ok": False, "code": "payload_changed",
                    "error": "The reviewed grade or feedback changed. Review again."}, 409
        if (student.get("status") != "approved"
                or (student.get("posted") and not student.get("correction_pending"))):
            return {"ok": False, "code": "payload_changed",
                    "error": "The reviewed grade or feedback changed. Review again."}, 409
        payload = _payload(student, grade_mode=grade_mode, waive_late=waive)
        payload_digest = _digest(payload)
        target_digest = _digest({"user_id": user_id, "payload": payload})
        idem_slot = f"{request_key}:{user_id}" if request_key else user_id
        if idempotency.get(idem_slot) == target_digest:
            student["posted"] = True
            student["status"] = "posted"
            results.append({
                "user_id": user_id, "status": "already_applied", "code": "already_applied",
                "request_digest": payload_digest, "target_digest": target_digest,
            })
            continue

        sent_score = (payload.get("submission") or {}).get("posted_grade")
        raw_score = (student.get("teacher_score") if student.get("teacher_score") is not None
                     else student.get("ai_score"))
        evidence_key = f"scoring:{session_id}:{idem_slot}:{target_digest}"
        corrects_event_id = ((student.get("last_posted") or {}).get("event_id")
                             if student.get("correction_pending") else None)
        if corrects_event_id:
            evidence_key += f":corrects:{corrects_event_id}"
        if sent_score is not None:
            baseline = student.get("submission_baseline") or {}
            try:
                score_ledger.append_event({
                    "source": "ce_apply", "action": "intent",
                    "course_id": session.get("course_id"),
                    "assignment_id": session.get("assignment_id"),
                    "student_id": user_id,
                    "attempt": baseline.get("attempt") or student.get("current_attempt"),
                    "submission_digest": baseline.get("submission_digest"),
                    "raw_score": (student.get("frozen_curve") or {}).get("raw_score", raw_score),
                    "entered_score": sent_score,
                    "late_days": _late_days_in_payload(payload),
                    "corrects_event_id": ((student.get("last_posted") or {}).get("event_id")
                                          if student.get("correction_pending") else None),
                    "curve_rule_id": (student.get("frozen_curve") or {}).get("rule_id"),
                    "feedback": (payload.get("comment") or {}).get("text_comment") or "",
                    "session_id": session_id, "stage_id": (session.get("staged_scoring_apply") or {}).get("stage_digest"),
                }, idempotency_key=evidence_key + ":intent")
            except Exception:
                return {"ok": False, "code": "score_ledger_unavailable",
                        "error": "Private score evidence is unavailable. Nothing was sent."}, 200

        _response, send_error = canvas_send("PUT", _path(session, user_id), payload)
        if send_error:
            if _explicit_canvas_rejection(send_error):
                if sent_score is not None:
                    _append_score_outcome(evidence_key, "failed", session, student, sent_score,
                                          raw_score, payload)
                results.append({
                    "user_id": user_id, "status": "failed", "code": "canvas_rejected",
                    "request_digest": payload_digest, "target_digest": target_digest,
                })
                continue
            # The write may have landed. Record the ambiguity and stop: no
            # read-back, no idempotency, no automatic repeat.
            student["posted"] = False
            student["status"] = "attention"
            student["push_state"] = "sent_unknown"
            save_session(session)
            if sent_score is not None:
                _append_score_outcome(evidence_key, "unknown", session, student, sent_score,
                                      raw_score, payload)
            results.append({
                "user_id": user_id, "status": "transport_unknown",
                "code": "write_transport_unknown",
                "request_digest": payload_digest, "target_digest": target_digest,
            })
            continue

        # Canvas accepted the write. That is the whole postcondition.
        student.pop("push_state", None)
        student["posted"] = True
        student["status"] = "posted"
        idempotency[idem_slot] = target_digest
        if sent_score is not None:
            _append_score_outcome(evidence_key, "accepted", session, student, sent_score,
                                  raw_score, payload)
        pushed += 1
        result = {
            "user_id": user_id, "status": "pushed", "code": "pushed",
            "request_digest": payload_digest, "target_digest": target_digest,
            "comment_sent": bool(payload.get("comment")),
            "corrects_event_id": corrects_event_id,
        }
        if student.get("correction_pending"):
            result["corrected"] = True
        if sent_score is not None:
            result["entered_score"] = float(sent_score)
        result["comment_sent"] = bool(payload.get("comment"))
        decision = late_decision(student, waive=waive, grade_mode=grade_mode)
        if decision:
            baseline = student.get("submission_baseline") or {}
            result["late"] = {
                **decision,
                "first_attempt_at": _plain_date(baseline.get("first_attempt_at")),
                "latest_attempt_at": _plain_date(baseline.get("latest_attempt_at")),
                "sent_status": (payload.get("submission") or {}).get("late_policy_status"),
            }
        results.append(result)

    if results:
        session.setdefault("push_log", []).append({
            "ts": _iso(_now()),
            "stage_id": (session.get("staged_scoring_apply") or {}).get("stage_digest"),
            "user_ids": requested,
            "results": results,
        })
        save_session(session)
    failed = sum(1 for result in results if result["status"] == "failed")
    unknown = sum(1 for result in results if result["status"] == "transport_unknown")
    outcome = {
        "ok": failed == 0 and unknown == 0,
        "pushed": pushed,
        "results": results,
        "posted_rows": [result["user_id"] for result in results
                         if result["status"] in {"pushed", "already_applied"}],
        "remaining_rows": [str(student.get("user_id")) for student in session.get("students", [])
                           if not student.get("posted") and student.get("user_id") is not None],
        "errors": [result["code"] for result in results
                   if result["status"] in {"failed", "transport_unknown"}],
    }
    if unknown:
        # The write may have landed. Name the ambiguity; never re-verify or repeat.
        outcome["code"] = "write_transport_unknown"
    elif failed:
        outcome["code"] = "canvas_rejected"
    return outcome, 200


def _append_score_outcome(key, action, session, student, entered, raw, payload):
    baseline = student.get("submission_baseline") or {}
    feedback_payload = payload
    if student.get("correction_pending") and not payload.get("comment"):
        intended = dict(student)
        intended["correction_pending"] = False
        feedback_payload = _payload(intended, grade_mode="post_score")
    try:
        score_ledger.append_event({
            "source": "ce_apply", "action": action,
            "course_id": session.get("course_id"), "assignment_id": session.get("assignment_id"),
            "student_id": student.get("user_id"),
            "attempt": baseline.get("attempt") or student.get("current_attempt"),
            "submission_digest": baseline.get("submission_digest"),
            "raw_score": (student.get("frozen_curve") or {}).get("raw_score", raw),
            "entered_score": entered,
            "late_days": _late_days_in_payload(payload),
            "curve_rule_id": (student.get("frozen_curve") or {}).get("rule_id"),
            "feedback": (feedback_payload.get("comment") or {}).get("text_comment") or "",
            "session_id": session.get("session_id"),
            "stage_id": (session.get("staged_scoring_apply") or {}).get("stage_digest"),
            "corrects_event_id": ((student.get("last_posted") or {}).get("event_id")
                                  if student.get("correction_pending") else None),
        }, idempotency_key=key + ":" + action)
    except Exception:
        # The accepted Canvas write remains accepted. The durable intent is
        # still evidence, and a later reconciliation can disclose the gap.
        return False
    return True
