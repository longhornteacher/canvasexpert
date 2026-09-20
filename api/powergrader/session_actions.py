"""PowerGrader session actions — save grades and the narrow Canvas write.

The ordinary Assignment write is deliberately narrow: send the reviewed raw
score and plain-text comment to the Canvas Submissions endpoint once, then
record the transport outcome. Canvas Expert does not read the resulting grade
back, compare it, or interpret any gradebook or late-policy adjustment Canvas
applies. A Canvas HTTP success means the write was accepted; a connection loss
is transport-unknown and is never automatically retried or re-read.

See docs/contracts/feedback-scoring-contract.md (Session consumption and write
safety) and docs/reference/powergrader-scoring-map.md.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from functools import wraps

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


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
) -> tuple[dict, int]:
    """Send the reviewed raw score and comment once, then record the outcome.

    No Canvas read happens before or after the send. A Canvas HTTP success
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
    idempotency = session.setdefault("push_idempotency", {})
    request_key = str(idempotency_key or "")

    results = []
    pushed = 0
    for user_id in requested:
        student = students.get(user_id)
        if not student or not _payload(student):
            return {"ok": False, "code": "payload_changed",
                    "error": "The reviewed grade or feedback changed. Review again."}, 409
        if student.get("status") != "approved" or student.get("posted"):
            return {"ok": False, "code": "payload_changed",
                    "error": "The reviewed grade or feedback changed. Review again."}, 409
        payload = _payload(student)
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

        _response, send_error = canvas_send("PUT", _path(session, user_id), payload)
        if send_error:
            if _explicit_canvas_rejection(send_error):
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
        pushed += 1
        results.append({
            "user_id": user_id, "status": "pushed", "code": "pushed",
            "request_digest": payload_digest, "target_digest": target_digest,
        })

    if results:
        session.setdefault("push_log", []).append({
            "ts": _iso(_now()),
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
