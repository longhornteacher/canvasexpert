"""Chat-side scoring apply: plan, questions, and the narrow write.

The ordinary Assignment write is one send: the reviewed raw score and
plain-text comment go to the Canvas Submissions endpoint, and Canvas applies
every gradebook and late-policy adjustment from there. This module therefore
performs no Canvas read at all -- no existing-score lookup, no frozen baseline,
no drift check, and no post-write verification. Canvas Live is the review
surface; the teacher may edit the result there.

What still guards the write: the packet digest, the exact assignment scope, the
session's currentness, result-shape and range validation, the outbound privacy
scan, held-work handling, and explicit teacher answers to the remaining
non-grade questions.

Read-only preview. ``build_plan`` writes nothing, locally or remotely. Every
state change -- copying staged AI values into the teacher fields, approving
rows, sending -- happens in ``apply_plan``, inside one session lock held by the
caller.

Identity: this module speaks Canvas ``user_id``, like the rest of the
powergrader package. Pseudonym resolution belongs to the MCP tool layer above
it, which never lets a ``user_id`` cross the boundary.
"""
from __future__ import annotations

import json

from . import session_actions
from decimal import Decimal, InvalidOperation


def _effective_points_possible(job: dict, assignment: dict, ai_result: dict | None, student: dict):
    """Resolve the assignment maximum from private scoring context."""
    for source in (assignment, (job or {}).get("assignment") or {}, ai_result or {}, student):
        if not isinstance(source, dict):
            continue
        for key in ("points_possible", "max_points", "max_score", "ai_max_score", "points_max"):
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError):
                continue
    return None


# Every question blocks the apply until answered. The first option in each
# tuple is the one that posts the affected rows; the last skips or stops.
# Order matters only for documentation -- an answer must name an option
# exactly, so there is no default and no "most likely" choice.
QUESTION_OPTIONS: dict[str, tuple[str, ...]] = {
    "score_above_possible": ("post_anyway", "skip_those"),
    "missing_score": ("comment_only", "skip_those"),
    "pseudonym_in_feedback": ("skip_those", "post_anyway"),
    "held_not_scored": ("proceed", "stop"),
    "insincere_attempt": ("confirm_insincere", "stop"),
    "late_days": ("post_late_days", "stop"),
}

# Answers that drop the question's affected rows from the write. Everything
# else posts them. "stop" is separate: it abandons the whole apply.
_SKIP_ANSWERS = {"skip_those"}
_STOP_ANSWERS = {"stop"}

# held_not_scored names students who would receive nothing. They are not in
# the candidate set to begin with, so its answers gate the run rather than
# shrink it.
_ADVISORY_KINDS = {"held_not_scored"}


def _staged(student: dict) -> bool:
    """A row an assistant has scored and nobody has posted yet."""
    if (student.get("posted") or student.get("status") == "posted"
            or student.get("push_state") == "sent_unknown"):
        return False
    return student.get("ai_score") is not None or bool(
        (student.get("ai_feedback") or "").strip())


def _projected_payload(student: dict) -> dict:
    """What ``_payload`` will build once the staged values are approved.

    Mirrors the approval copy in ``approve_rows`` so the plan digest covers the
    bytes that will actually be sent, not the row's pre-approval state.
    """
    projected = dict(student)
    if student.get("teacher_score") is None:
        projected["teacher_score"] = student.get("ai_score")
    if not (student.get("teacher_feedback") or "").strip():
        projected["teacher_feedback"] = student.get("ai_feedback") or ""
    return session_actions._payload(projected)


def default_transports():
    """This module owns the Canvas transport, so the MCP layer never imports it.

    ``api/tests/dailywriting/test_dw_canvas_ingest.py`` enforces that no
    ``api/mcp_server/`` module imports ``canvas_client``: the assistant-facing
    layer is not allowed to hold a live Canvas call. Callers there ask this
    package to do the talking instead. Tests still inject their own fakes.
    """
    from api.platform_services.canvas_client import _canvas_send

    return _canvas_send


def _question(kind: str, detail: str, user_ids: list[str], **extra) -> dict:
    question = {
        "id": kind,
        "kind": kind,
        "detail": detail,
        "user_ids": sorted(user_ids),
        "options": list(QUESTION_OPTIONS[kind]),
    }
    question.update(extra)
    return question


def build_plan(session: dict, *, pseudonyms=()) -> dict:
    """Read-only. What would be posted, and what needs answering first.

    ``pseudonyms`` is every stand-in name in the local vault. Feedback that
    contains one would reach a real student as a stranger's fake name, or as
    their own -- which they have never seen either.
    """
    students = [s for s in session.get("students", []) if s.get("user_id") is not None]
    if any(s.get("push_state") == "sent_unknown" for s in students):
        return {
            "ok": False,
            "code": "canvas_write_attention",
            "error": "A previous Canvas write could not be confirmed. Review Canvas before retrying.",
        }
    candidates = [s for s in students if _staged(s)]
    candidate_ids = [str(s["user_id"]) for s in candidates]

    above, missing, tainted = [], [], []
    assignment = session.get("assignment") or {}

    for student in candidates:
        user_id = str(student["user_id"])
        payload = _projected_payload(student)

        score = student.get("ai_score") if student.get("teacher_score") is None \
            else student.get("teacher_score")
        if score is None:
            if "comment" in payload:
                missing.append(user_id)
        else:
            possible = _effective_points_possible({}, assignment, None, student)
            try:
                if possible is not None and float(score) > float(possible):
                    above.append(user_id)
            except (TypeError, ValueError):
                pass

        text = ((payload.get("comment") or {}).get("text_comment") or "")
        if any(name and name in text for name in pseudonyms):
            tainted.append(user_id)

    receives_nothing = [str(s["user_id"]) for s in students
                        if not _staged(s) and not s.get("posted")]

    # Grading-policy facts, stamped only on candidates in a policy course
    # (docs/contracts/grading-policy-contract.md section 5).
    insincere = [str(s["user_id"]) for s in candidates if (s.get("grading") or {}).get("insincere")]
    late_candidates = [s for s in candidates
                       if s.get("grading") and s.get("canvas_late")]

    questions = []
    if above:
        questions.append(_question(
            "score_above_possible",
            "Staged score is higher than the item is worth.", above))
    if missing:
        questions.append(_question(
            "missing_score",
            "Feedback was staged with no score, so only a comment would post.", missing))
    if tainted:
        questions.append(_question(
            "pseudonym_in_feedback",
            "Feedback quotes a stand-in name. Scrubbing is whole-word, so an "
            "ordinary word in the response may have become a pseudonym; posting "
            "it sends the student a name they have never seen.", tainted))
    if receives_nothing:
        questions.append(_question(
            "held_not_scored",
            "These submissions have nothing staged and would receive nothing. "
            "Attachment-only and media-only work is held out of AI packets.",
            receives_nothing))
    if insincere:
        questions.append(_question(
            "insincere_attempt",
            "These attempts get no effort credit and post their rubric score.",
            insincere))
    if late_candidates:
        questions.append(_question(
            "late_days",
            "Confirm how many days late each of these submissions counts for grading.",
            [str(s["user_id"]) for s in late_candidates],
            rows=[
                {
                    "user_id": str(s["user_id"]),
                    "canvas_days": (s.get("grading") or {}).get("canvas_late_days"),
                    "late_days": ((s["grading"].get("late_days"))
                                 if (s["grading"].get("late_days")) is not None
                                 else s["grading"].get("suggested_late_days")),
                }
                for s in sorted(late_candidates, key=lambda s: str(s["user_id"]))
            ],
        ))

    return {
        "ok": True,
        "candidate_ids": candidate_ids,
        "questions": questions,
        "notes": {
            "staged": len(candidate_ids),
            "in_session": len(students),
            "already_posted": sum(1 for s in students if s.get("posted")),
        },
        "digest": _plan_digest(candidate_ids, candidates, questions),
    }


def _plan_digest(candidate_ids, candidates, questions) -> str:
    """Covers the rows, the exact bytes to be pushed, and what was asked.

    A staged score edited between preview and apply, a question that appears or
    disappears, or a row entering or leaving the set all change this, so apply
    refuses rather than landing something the teacher never read.
    """
    return session_actions._digest({
        "user_ids": candidate_ids,
        "payloads": {str(s["user_id"]): _projected_payload(s) for s in candidates},
        "questions": [{"kind": q["kind"], "user_ids": q["user_ids"]} for q in questions],
    })


def resolve_answers(plan: dict, answers: dict | None) -> dict:
    """Turn the teacher's answers into the exact id set to push."""
    answers = {str(k): str(v) for k, v in (answers or {}).items()}
    unanswered = [q["id"] for q in plan["questions"] if q["id"] not in answers]
    if unanswered:
        return {"ok": False, "code": "unanswered_questions",
                "unanswered": unanswered,
                "error": ("Answer every question before applying: "
                          + ", ".join(unanswered))}

    invalid = [
        f"{q['id']}={answers[q['id']]}"
        for q in plan["questions"]
        if answers[q["id"]] not in QUESTION_OPTIONS[q["kind"]]
    ]
    if invalid:
        return {"ok": False, "code": "invalid_answer",
                "error": "Not an offered option: " + ", ".join(sorted(invalid))}

    skipped: set[str] = set()
    for question in plan["questions"]:
        answer = answers[question["id"]]
        if answer in _STOP_ANSWERS:
            return {"ok": False, "code": "stopped_by_answer",
                    "error": f"Stopped: you answered '{answer}' to {question['id']}."}
        if answer in _SKIP_ANSWERS and question["kind"] not in _ADVISORY_KINDS:
            skipped.update(question["user_ids"])

    selected = [uid for uid in plan["candidate_ids"] if uid not in skipped]
    if not selected:
        return {"ok": False, "code": "nothing_to_post",
                "error": "Every staged row was skipped by an answer. Nothing to post."}
    return {"ok": True, "user_ids": selected, "skipped": sorted(skipped)}


def approve_rows(session: dict, user_ids) -> None:
    """Copy staged AI values into the teacher fields and approve.

    Deliberately not ``session_actions.save_grade``: that records an implicit
    blind-first datapoint, which is only meaningful when a teacher scored
    without seeing the AI suggestion. Copying the AI's own score is the
    opposite, and recording it as blind would quietly corrupt that record.
    """
    wanted = {str(uid) for uid in user_ids}
    for student in session.get("students", []):
        if str(student.get("user_id")) not in wanted:
            continue
        if student.get("teacher_score") is None:
            student["teacher_score"] = student.get("ai_score")
        if not (student.get("teacher_feedback") or "").strip():
            student["teacher_feedback"] = student.get("ai_feedback") or ""
        student["status"] = "approved"


def apply_plan(session_id: str, *, expected_digest: str, answers: dict | None,
               load_session, save_session, canvas_send=None,
               pseudonyms=(), idempotency_key: str = "") -> tuple[dict, int]:
    """Approve and send exactly what a matching preview described.

    ``approve_rows`` runs before the send, so ``push_grades`` sees ordinary
    approved rows. There is no freeze, no drift check, and no read-back: the
    plan digest is the only thing standing between the preview the teacher read
    and the bytes that go out.
    """
    if canvas_send is None:
        canvas_send = default_transports()

    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404

    plan = build_plan(session, pseudonyms=pseudonyms)
    if not plan.get("ok"):
        return plan, 200
    if plan["digest"] != str(expected_digest or ""):
        return {"ok": False, "code": "plan_changed",
                "error": ("The staged scores changed since the preview. "
                          "Preview again before applying.")}, 409

    resolved = resolve_answers(plan, answers)
    if not resolved.get("ok"):
        return resolved, 409

    user_ids = resolved["user_ids"]
    approve_rows(session, user_ids)
    save_session(session)

    pushed, status = session_actions.push_grades(
        session_id, user_ids=json.dumps(user_ids),
        load_session=load_session, save_session=save_session,
        canvas_send=canvas_send, idempotency_key=idempotency_key,
    )
    if pushed.get("ok"):
        pushed = dict(pushed)
        pushed["skipped"] = resolved["skipped"]
    # Make retry state explicit even when Canvas accepted only part of the
    # batch.  The private session remains the source of truth for exact rows.
    current = load_session(session_id) or session
    rows = []
    for student in current.get("students") or []:
        if student.get("user_id") is None:
            continue
        row = {"user_id": str(student.get("user_id")),
               "posted": bool(student.get("posted")),
               "status": str(student.get("status") or "")}
        rows.append(row)
    posted_rows = [row["user_id"] for row in rows if row["posted"]]
    remaining_rows = [row["user_id"] for row in rows if not row["posted"]]
    pushed["posted_rows"] = posted_rows
    pushed["remaining_rows"] = remaining_rows
    if remaining_rows and posted_rows:
        pushed["code"] = "partial_post_remaining"
        pushed["recovery"] = "Retry only the remaining rows after resolving any attention rows."
    return pushed, status
