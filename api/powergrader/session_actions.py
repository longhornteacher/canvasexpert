"""PowerGrader session actions — save grades and guarded Canvas pushes."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from api import operational_log
from api.powergrader import attribution
from api.powergrader import blind_first
from api.powergrader import session_store


REVIEW_TTL = timedelta(minutes=15)


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


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def invalidate_pending_review(session: dict) -> None:
    session.pop("pending_push_review", None)
    session.pop("pending_new_quiz_review", None)


def _new_quiz_decisions(raw: str) -> tuple[list[dict] | None, str | None]:
    try:
        values = json.loads(raw)
    except Exception:
        return None, "invalid_item_decisions"
    if not isinstance(values, list):
        return None, "invalid_item_decisions"
    clean = []
    for value in values:
        if not isinstance(value, dict) or not value.get("item_id"):
            return None, "invalid_item_decisions"
        try:
            score = float(value.get("score"))
        except (TypeError, ValueError):
            return None, "invalid_item_decisions"
        clean.append({
            "item_id": str(value["item_id"]), "score": score,
            "teacher_feedback": str(value.get("teacher_feedback") or ""),
            "ta_feedback": str(value.get("ta_feedback") or ""),
        })
    return clean, None


@_session_locked
def review_new_quiz_finalization(session_id: str, *, user_id: str, decisions_json: str,
                                 load_session, save_session, preflight) -> tuple[dict, int]:
    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404
    if not session.get("new_quiz_item_finalization_supported"):
        return {"ok": False, "code": "new_quiz_finalization_unavailable", "error": "This session does not support New Quiz item finalization."}, 200
    student = next((item for item in session.get("students", []) if str(item.get("user_id")) == str(user_id)), None)
    if not student:
        return {"ok": False, "code": "student_not_found", "error": "Student not found in session."}, 200
    provenance = session.get("new_quiz_csv_provenance") or {}
    if provenance and str(user_id) not in (provenance.get("bindings") or {}):
        return {"ok": False, "code": "csv_provenance_unresolved", "error": "Resolve this CSV row against the current New Quiz result before finalizing."}, 200
    if student.get("speedgrader_required"):
        return {"ok": False, "code": "speedgrader_required", "error": "This student's New Quiz evidence requires SpeedGrader review."}, 200
    decisions, error = _new_quiz_decisions(decisions_json)
    if error:
        return {"ok": False, "code": error, "error": "Enter one valid teacher score for every reviewed item."}, 200
    try:
        baseline = preflight(session, student, decisions)
    except Exception as exc:
        code = getattr(exc, "code", "new_quiz_preflight_failed")
        return {"ok": False, "code": code, "error": "Canvas could not freeze this student's current New Quiz result."}, 200
    if provenance:
        binding = (provenance.get("bindings") or {}).get(str(user_id)) or {}
        if (str(baseline.get("result_id") or "") != str(binding.get("result_id") or "")
                or str(baseline.get("state_digest") or "") != str(binding.get("state_digest") or "")):
            student["speedgrader_required"] = True
            student["csv_provenance_stale"] = True
            invalidate_pending_review(session)
            save_session(session)
            return {"ok": False, "code": "csv_provenance_stale", "error": "The current New Quiz result changed; resolve it again or use SpeedGrader."}, 200
    pending = {
        "token": secrets.token_urlsafe(32), "created_at": _iso(_now()),
        "expires_at": _iso(_now() + REVIEW_TTL), "user_id": str(user_id),
        "result_id": baseline["result_id"], "state_digest": baseline["state_digest"],
        "decision_digest": baseline["decision_digest"],
    }
    session["pending_new_quiz_review"] = pending
    save_session(session)
    return {"ok": True, "review_token": pending["token"], "expires_at": pending["expires_at"], "item_count": len(decisions)}, 200


@_session_locked
def resolve_new_quiz_csv_provenance(session_id: str, *, user_id: str,
                                    load_session, save_session, resolver) -> tuple[dict, int]:
    """Resolve one CSV row with the existing signed authoritative-read chain."""
    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404
    provenance = session.get("new_quiz_csv_provenance") or {}
    if not provenance:
        return {"ok": False, "code": "csv_provenance_unavailable", "error": "This is not a CSV fallback session."}, 200
    if (str(provenance.get("course_id")) != str(session.get("course_id"))
            or str(provenance.get("assignment_id")) != str(session.get("assignment_id"))):
        return {"ok": False, "code": "csv_session_scope_mismatch", "error": "The CSV session scope is no longer valid; use SpeedGrader."}, 200
    student = next((item for item in session.get("students", []) if str(item.get("user_id")) == str(user_id)), None)
    row = next((item for item in provenance.get("rows", []) if str(item.get("user_id")) == str(user_id)), None)
    if not student or not row or str(student.get("new_quiz_attempt")) != str(row.get("attempt")):
        return {"ok": False, "code": "csv_student_mismatch", "error": "The CSV student record cannot be safely bound; use SpeedGrader."}, 200
    try:
        binding = resolver(session, student, row)
    except Exception as exc:
        student["speedgrader_required"] = True
        invalidate_pending_review(session)
        save_session(session)
        return {"ok": False, "code": getattr(exc, "code", "csv_provenance_unavailable"), "error": "The current New Quiz result could not be safely matched; use SpeedGrader."}, 200
    if not isinstance(binding, dict) or not binding.get("result_id") or not binding.get("state_digest"):
        student["speedgrader_required"] = True
        save_session(session)
        return {"ok": False, "code": "csv_provenance_unavailable", "error": "The current New Quiz result could not be safely matched; use SpeedGrader."}, 200
    try:
        bound_attempt = int(binding.get("attempt"))
    except (TypeError, ValueError):
        student["speedgrader_required"] = True
        save_session(session)
        return {"ok": False, "code": "csv_provenance_mismatch", "error": "The current New Quiz result could not be safely matched; use SpeedGrader."}, 200
    provenance.setdefault("bindings", {})[str(user_id)] = {
        "result_id": str(binding["result_id"]), "state_digest": str(binding["state_digest"]),
        "attempt": bound_attempt,
    }
    # Native/manual evidence remains a whole-student SpeedGrader requirement.
    student["speedgrader_required"] = bool(student.get("csv_native_manual_evidence"))
    student.pop("csv_provenance_stale", None)
    invalidate_pending_review(session)
    save_session(session)
    return {"ok": True, "status": "resolved" if not student["speedgrader_required"] else "speedgrader_required"}, 200


@_session_locked
def finalize_new_quiz(session_id: str, *, user_id: str, review_token: str, decisions_json: str,
                      load_session, save_session, apply) -> tuple[dict, int]:
    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404
    pending = session.get("pending_new_quiz_review") or {}
    decisions, error = _new_quiz_decisions(decisions_json)
    decision_digest = _digest(decisions) if decisions else ""
    if not error and session.setdefault("new_quiz_finalized_digests", {}).get(str(user_id)) == decision_digest:
        return {"ok": True, "status": "already_applied", "code": "already_applied"}, 200
    if error or not pending or pending.get("user_id") != str(user_id) or pending.get("token") != review_token:
        return _review_error("review_mismatch")
    try:
        if _now() >= datetime.fromisoformat(pending["expires_at"]):
            invalidate_pending_review(session); save_session(session)
            return _review_error("review_expired")
    except (KeyError, ValueError, TypeError):
        return _review_error("review_required")
    if _digest(decisions) != pending.get("decision_digest"):
        return _review_error("payload_changed")
    key = _digest({"user_id": str(user_id), "decision_digest": pending["decision_digest"], "state_digest": pending["state_digest"]})
    if session.setdefault("new_quiz_idempotency", {}).get(str(user_id)) == key:
        return {"ok": True, "status": "already_applied", "code": "already_applied"}, 200
    student = next((item for item in session.get("students", []) if str(item.get("user_id")) == str(user_id)), None)
    if not student or student.get("speedgrader_required"):
        return {"ok": False, "code": "speedgrader_required", "error": "This student's New Quiz evidence requires SpeedGrader review."}, 200
    try:
        result = apply(session, student, decisions, pending)
    except Exception as exc:
        code = getattr(exc, "code", "write_unknown")
        session.setdefault("new_quiz_receipts", []).append({"ts": _iso(_now()), "user_id": str(user_id), "outcome": code, "content_minimized": True})
        # An ambiguous or rejected write must never be retried through the
        # same frozen approval.  The teacher reviews Canvas first, then starts
        # a new explicit review if another attempt is warranted.
        invalidate_pending_review(session)
        save_session(session)
        return {"ok": False, "code": code, "error": "New Quiz finalization was not verified; review in SpeedGrader before retrying."}, 409
    session["new_quiz_idempotency"][str(user_id)] = key
    session["new_quiz_finalized_digests"][str(user_id)] = decision_digest
    student["new_quiz_finalized"] = True
    session.setdefault("new_quiz_receipts", []).append({"ts": _iso(_now()), "user_id": str(user_id), "outcome": "verified", "result_digest": result.get("state_digest"), "content_minimized": True})
    invalidate_pending_review(session)
    save_session(session)
    return {"ok": True, "status": "finalized", "code": "finalized"}, 200


def converge_new_quiz_after_finalize(session, user_id, *, notify_write_through) -> None:
    """After a verified New Quiz finalize, converge both freshness surfaces:
    the gradebook submission (write-through refresh) and the separate New Quiz
    response snapshot (stale-invalidate so the next read re-fetches live via the
    existing native chain). Best-effort; never fails the finalize that landed.

    ``notify_write_through`` is injected because the gradebook refresh hook is
    a webui-layer concern (it reaches CanvasMirror scheduling); this module
    stays free of any import on ``api.webui``.
    """
    notify_write_through(session, [user_id])
    try:
        course_id = (session or {}).get("course_id")
        assignment_id = (session or {}).get("assignment_id")
        if course_id and assignment_id:
            from api.mirror import new_quizzes
            new_quizzes.invalidate_responses(course_id, assignment_id)
    except Exception as exc:
        operational_log.emit("new_quizzes.response_invalidate", "failed", error_class=type(exc))


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


def _writeback_mode(session: dict) -> str:
    """What this session may write to Canvas.

    "full" — grades and comments through the Submissions API.
    "comments_only" — assignment-level comments only (New Quiz sessions:
    item scores finalize through the gated two-phase review lane; assignment
    comments are ordinary Canvas data — verified live 2026-07-14).
    "none" — legacy New Quiz sessions created before the comment lane.
    """
    if session.get("canvas_writeback_supported", True):
        return "full"
    if session.get("comment_writeback_supported") is True:
        return "comments_only"
    return "none"


_DRAFT_BANNER_RE = re.compile(
    r"^-{4,}[ \t]+AI draft[ \t]+-{4,}[ \t]*\r?\n"
    r"[ \t]*AI score:[^\r\n]*(?:\r?\n|$)",
    re.IGNORECASE,
)


def _strip_draft_banner(feedback: str) -> str:
    """Remove only the legacy leading AI banner from outbound feedback."""
    return _DRAFT_BANNER_RE.sub("", feedback, count=1).strip()


def _payload(student: dict, *, comments_only: bool = False) -> dict:
    # Single grading surface: PowerGrader is the only writer of an AI-feedback
    # submission comment (``comment[text_comment]``). Gradebook may adjust
    # ``posted_grade`` (curve/late/extension) but never writes feedback here.
    # See docs/reference/powergrader-scoring-map.md (Guardrails: single grading surface).
    score = student.get("teacher_score")
    feedback = _strip_draft_banner((student.get("teacher_feedback") or "").strip())
    # Words the assistant drafted must not reach a student under the teacher's
    # name. The box holds both kinds, so provenance is decided by comparison.
    if attribution.is_assistant_authored(feedback, student.get("ai_feedback")):
        feedback = attribution.attribute(feedback)
    payload: dict = {}
    if comments_only:
        # Never touch the score or lateness of a quiz-engine-owned grade.
        if feedback:
            payload["comment"] = {"text_comment": feedback}
        return payload
    if score is not None:
        payload["submission"] = {"posted_grade": str(score)}
    if feedback:
        payload["comment"] = {"text_comment": feedback}
    return payload


def _snapshot(data: dict) -> dict:
    submission = data.get("submission") if isinstance(data.get("submission"), dict) else data
    comments_key = "submission_comments" if "submission_comments" in data else "comments" if "comments" in data else None
    comments = data.get(comments_key) if comments_key else None
    comments = comments if isinstance(comments, list) else []
    latest = {}
    if comments:
        latest = sorted(comments, key=lambda item: str(item.get("created_at") or ""))[-1] or {}
    return {
        "score": submission.get("score"),
        "grade": submission.get("grade"),
        "graded_at": submission.get("graded_at"),
        "updated_at": submission.get("updated_at"),
        "workflow_state": submission.get("workflow_state"),
        "comments_available": comments_key is not None,
        "comment_count": len(comments),
        "latest_comment": {
            "id": latest.get("id"),
            "created_at": latest.get("created_at"),
        },
    }


def _same_score_baseline(before: dict, after: dict) -> bool:
    return all(before.get(key) == after.get(key) for key in ("score", "grade", "graded_at", "updated_at"))


def _same_comments(before: dict, after: dict) -> bool:
    return (
        before.get("comments_available")
        and after.get("comments_available")
        and before.get("comment_count") == after.get("comment_count")
        and before.get("latest_comment") == after.get("latest_comment")
    )


def _path(session: dict, user_id: str) -> str:
    return (
        f"/api/v1/courses/{session['course_id']}/assignments/{session['assignment_id']}"
        f"/submissions/{user_id}"
    )


def _fetch_snapshot(session: dict, user_id: str, canvas_get) -> tuple[dict | None, str | None]:
    data, error = canvas_get(
        _path(session, user_id),
        params={"include[]": "submission_comments"},
        timeout=5,
    )
    if error or not isinstance(data, dict):
        return None, "review_fetch_failed"
    return _snapshot(data), None


def _eligible_students(session: dict, *, comments_only: bool = False) -> dict[str, dict]:
    return {
        str(student.get("user_id")): student
        for student in session.get("students", [])
        if student.get("user_id") is not None
        and student.get("status") == "approved"
        and not student.get("posted")
        and _payload(student, comments_only=comments_only)
    }


@_session_locked
def review_push(
    session_id: str,
    *,
    user_ids: str,
    load_session,
    save_session,
    canvas_get,
) -> tuple[dict, int]:
    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404
    mode = _writeback_mode(session)
    if mode == "none":
        return {"ok": False, "code": "canvas_writeback_unsupported", "error": "This session was created before New Quiz comment posting. Start a new session to post feedback comments."}, 200
    comments_only = mode == "comments_only"
    requested, error = _parse_ids(user_ids)
    if error:
        return {"ok": False, "code": error, "error": "Select valid submissions for review."}, 200
    eligible = _eligible_students(session, comments_only=comments_only)
    ordered_ids = requested or [str(student.get("user_id")) for student in session.get("students", []) if str(student.get("user_id")) in eligible]
    if not ordered_ids or any(user_id not in eligible for user_id in ordered_ids):
        error_text = ("Selected submissions have no approved feedback to post. New Quiz sessions post feedback comments only; scores are entered in Canvas."
                      if comments_only else "Selected submissions are no longer eligible.")
        return {"ok": False, "code": "invalid_selection", "error": error_text}, 200

    baselines = {}
    payload_digests = {}
    target_digests = {}
    targets = []
    for user_id in ordered_ids:
        student = eligible[user_id]
        baseline, fetch_error = _fetch_snapshot(session, user_id, canvas_get)
        if fetch_error:
            return {"ok": False, "code": fetch_error, "error": "Could not capture the Canvas review baseline."}, 200
        payload = _payload(student, comments_only=comments_only)
        baselines[user_id] = baseline
        payload_digests[user_id] = _digest(payload)
        target_digests[user_id] = _digest({"user_id": user_id, "payload": payload, "baseline": baseline})
        targets.append({
            "user_id": user_id,
            "has_score": "submission" in payload,
            "has_feedback": "comment" in payload,
            "current_score": baseline.get("score"),
            "current_grade": baseline.get("grade"),
            "comment_count": baseline.get("comment_count"),
            "comments_available": baseline.get("comments_available"),
        })

    pending = {
        "token": secrets.token_urlsafe(32),
        "created_at": _iso(_now()),
        "expires_at": _iso(_now() + REVIEW_TTL),
        "user_ids": ordered_ids,
        "payload_digests": payload_digests,
        "target_digests": target_digests,
        "baselines": baselines,
        "overall_digest": _digest({"user_ids": ordered_ids, "payload_digests": payload_digests, "baselines": baselines}),
    }
    session["pending_push_review"] = pending
    save_session(session)
    return {
        "ok": True,
        "review_token": pending["token"],
        "expires_at": pending["expires_at"],
        "user_ids": ordered_ids,
        "overall_digest": pending["overall_digest"],
        "targets": targets,
        "writeback_mode": mode,
    }, 200


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
    """Save one student's grade into a session and invalidate any review."""
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
            invalidate_pending_review(session)
            save_session(session)
            approved = sum(1 for item in session["students"] if item.get("status") == "approved")
            return {"ok": True, "approved": approved}, 200

    return {"ok": False, "error": "Student not found in session."}, 200


def _review_error(code: str) -> tuple[dict, int]:
    messages = {
        "review_required": "Review required before push.",
        "review_expired": "The review expired. Review again before pushing.",
        "review_mismatch": "The selected submissions do not match the review.",
        "payload_changed": "The reviewed grade or feedback changed. Review again.",
        "drift_detected": "Canvas changed after review. Review again before pushing.",
        "comments_unavailable": "Canvas comments could not be verified for feedback.",
        "review_fetch_failed": "Canvas could not be checked before pushing.",
    }
    return {"ok": False, "code": code, "error": messages.get(code, "Push review is no longer valid.")}, 409


@_session_locked
def push_grades(
    session_id: str,
    *,
    user_ids: str,
    review_token: str,
    load_session,
    save_session,
    canvas_send,
    canvas_get,
) -> tuple[dict, int]:
    """Apply only a still-valid, drift-checked frozen review."""
    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404
    mode = _writeback_mode(session)
    if mode == "none":
        return {"ok": False, "code": "canvas_writeback_unsupported", "error": "This session was created before New Quiz comment posting. Start a new session to post feedback comments."}, 200
    comments_only = mode == "comments_only"
    pending = session.get("pending_push_review")
    if not pending or not review_token:
        return _review_error("review_required")
    try:
        if _now() >= datetime.fromisoformat(pending["expires_at"]):
            invalidate_pending_review(session)
            save_session(session)
            return _review_error("review_expired")
    except (KeyError, ValueError, TypeError):
        return _review_error("review_required")
    requested, error = _parse_ids(user_ids, allow_empty=False)
    if error or review_token != pending.get("token") or requested != pending.get("user_ids"):
        return _review_error("review_mismatch")

    students = {str(student.get("user_id")): student for student in session.get("students", [])}
    idempotency = session.setdefault("push_idempotency", {})
    preflight = []
    for user_id in requested:
        student = students.get(user_id)
        if not student or not _payload(student, comments_only=comments_only):
            return _review_error("payload_changed")
        payload = _payload(student, comments_only=comments_only)
        payload_digest = _digest(payload)
        if payload_digest != pending.get("payload_digests", {}).get(user_id):
            return _review_error("payload_changed")
        target_digest = pending.get("target_digests", {}).get(user_id)
        if idempotency.get(user_id) == target_digest:
            preflight.append((user_id, student, payload, target_digest, "already_applied"))
            continue
        if student.get("status") != "approved" or student.get("posted"):
            return _review_error("payload_changed")
        current, fetch_error = _fetch_snapshot(session, user_id, canvas_get)
        if fetch_error:
            return _review_error(fetch_error)
        baseline = pending.get("baselines", {}).get(user_id) or {}
        if "comment" in payload:
            if not _same_comments(baseline, current):
                return _review_error("comments_unavailable" if not current.get("comments_available") else "drift_detected")
        if "submission" in payload and not _same_score_baseline(baseline, current):
            return _review_error("drift_detected")
        preflight.append((user_id, student, payload, target_digest, "pending"))

    results = []
    pushed = 0
    for user_id, student, payload, target_digest, state in preflight:
        if state == "already_applied":
            student["posted"] = True
            student["status"] = "posted"
            results.append({"user_id": user_id, "status": "already_applied", "code": "already_applied"})
            continue
        _, send_error = canvas_send("PUT", _path(session, user_id), payload)
        if send_error:
            results.append({"user_id": user_id, "status": "failed", "code": "canvas_rejected"})
            continue
        student["posted"] = True
        student["status"] = "posted"
        idempotency[user_id] = target_digest
        pushed += 1
        results.append({"user_id": user_id, "status": "pushed", "code": "pushed"})

    if results:
        session.setdefault("push_log", []).append({
            "ts": _iso(_now()),
            "user_ids": requested,
            "results": results,
        })
        save_session(session)
    failed = sum(1 for result in results if result["status"] == "failed")
    return {
        "ok": failed == 0,
        "pushed": pushed,
        "results": results,
        "errors": [result["code"] for result in results if result["status"] == "failed"],
    }, 200
