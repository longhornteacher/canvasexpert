"""PowerGrader session actions — save grades and guarded Canvas pushes."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from api.powergrader import attribution
from api.powergrader import blind_first
from api.powergrader import session_store
from api.student_text import normalize_student_text


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


def _payload(student: dict) -> dict:
    # Single grading surface: PowerGrader is the only writer of an AI-feedback
    # submission comment (``comment[text_comment]``). Gradebook may adjust
    # ``posted_grade`` (curve/late/extension) but never writes feedback here.
    # See docs/reference/powergrader-scoring-map.md (Guardrails: single grading surface).
    score = student.get("teacher_score")
    feedback = normalize_student_text(
        attribution.attribute(
            _strip_draft_banner((student.get("teacher_feedback") or "").strip())
        )
    )
    payload: dict = {}
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


def _numbers_equal(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _same_postcondition(payload: dict, before: dict, after: dict) -> bool:
    """Prove the exact score/comment payload landed in the Canvas read-back."""
    if "submission" in payload:
        posted_grade = (payload.get("submission") or {}).get("posted_grade")
        if not _numbers_equal(posted_grade, after.get("score")):
            return False
    if "comment" in payload:
        if not after.get("comments_available"):
            return False
        if not isinstance(before.get("comment_count"), int) or not isinstance(
            after.get("comment_count"), int
        ):
            return False
        if after["comment_count"] != before["comment_count"] + 1:
            return False
        if after.get("latest_comment") == before.get("latest_comment"):
            return False
    return True


def _explicit_canvas_rejection(error: object) -> bool:
    """HTTP responses are explicit rejection; transport errors may have landed."""
    return str(error or "").lstrip().upper().startswith("HTTP ")


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


def _eligible_students(session: dict) -> dict[str, dict]:
    return {
        str(student.get("user_id")): student
        for student in session.get("students", [])
        if student.get("user_id") is not None
        and student.get("status") == "approved"
        and not student.get("posted")
        and _payload(student)
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
    requested, error = _parse_ids(user_ids)
    if error:
        return {"ok": False, "code": error, "error": "Select valid submissions for review."}, 200
    eligible = _eligible_students(session)
    ordered_ids = requested or [str(student.get("user_id")) for student in session.get("students", []) if str(student.get("user_id")) in eligible]
    if not ordered_ids or any(user_id not in eligible for user_id in ordered_ids):
        return {"ok": False, "code": "invalid_selection", "error": "Selected submissions are no longer eligible."}, 200

    baselines = {}
    payload_digests = {}
    target_digests = {}
    targets = []
    for user_id in ordered_ids:
        student = eligible[user_id]
        baseline, fetch_error = _fetch_snapshot(session, user_id, canvas_get)
        if fetch_error:
            return {"ok": False, "code": fetch_error, "error": "Could not capture the Canvas review baseline."}, 200
        payload = _payload(student)
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
        if not student or not _payload(student):
            return _review_error("payload_changed")
        payload = _payload(student)
        payload_digest = _digest(payload)
        if payload_digest != pending.get("payload_digests", {}).get(user_id):
            return _review_error("payload_changed")
        target_digest = pending.get("target_digests", {}).get(user_id)
        if idempotency.get(user_id) == target_digest:
            preflight.append((
                user_id, student, payload, target_digest, "already_applied", None,
                payload_digest,
            ))
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
        preflight.append((
            user_id, student, payload, target_digest, "pending", baseline,
            payload_digest,
        ))

    results = []
    pushed = 0
    for user_id, student, payload, target_digest, state, baseline, payload_digest in preflight:
        if state == "already_applied":
            student["posted"] = True
            student["status"] = "posted"
            results.append({
                "user_id": user_id, "status": "already_applied", "code": "already_applied",
                "request_digest": payload_digest,
                "target_digest": target_digest,
                "postcondition_digest": _digest(baseline or {}),
            })
            continue
        _response, send_error = canvas_send("PUT", _path(session, user_id), payload)
        if send_error:
            if _explicit_canvas_rejection(send_error):
                results.append({
                    "user_id": user_id, "status": "failed", "code": "canvas_rejected",
                    "request_digest": payload_digest, "target_digest": target_digest,
                    "postcondition_digest": "",
                })
                continue
            student["posted"] = False
            student["status"] = "attention"
            student["push_state"] = "sent_unknown"
            save_session(session)
            results.append({
                "user_id": user_id, "status": "attention", "code": "sent_unknown",
                "request_digest": payload_digest, "target_digest": target_digest,
                "postcondition_digest": "",
            })
            continue
        # Persist the ambiguous state before the verification GET. A process
        # failure after a successful PUT must never make a later retry blind.
        student["posted"] = False
        student["status"] = "attention"
        student["push_state"] = "sent_unknown"
        save_session(session)
        after, verify_error = _fetch_snapshot(session, user_id, canvas_get)
        if verify_error or not _same_postcondition(payload, baseline or {}, after or {}):
            results.append({
                "user_id": user_id, "status": "attention", "code": "sent_unknown",
                "request_digest": payload_digest, "target_digest": target_digest,
                "postcondition_digest": _digest(after or {}),
            })
            continue
        student.pop("push_state", None)
        student["posted"] = True
        student["status"] = "posted"
        idempotency[user_id] = target_digest
        pushed += 1
        results.append({
            "user_id": user_id, "status": "pushed", "code": "pushed",
            "request_digest": payload_digest, "target_digest": target_digest,
            "postcondition_digest": _digest(after or {}),
        })

    if results:
        session.setdefault("push_log", []).append({
            "ts": _iso(_now()),
            "user_ids": requested,
            "results": results,
        })
        save_session(session)
    failed = sum(1 for result in results if result["status"] == "failed")
    attention = sum(1 for result in results if result["status"] == "attention")
    return {
        "ok": failed == 0 and attention == 0,
        "pushed": pushed,
        "results": results,
        "errors": [result["code"] for result in results
                   if result["status"] in {"failed", "attention"}],
    }, 200
