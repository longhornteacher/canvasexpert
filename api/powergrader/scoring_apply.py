"""Chat-side scoring apply: plan, questions, and the push.

The web UI queue and this module reach Canvas through the same transport --
``session_actions.review_push`` freezes, ``session_actions.push_grades``
writes. What differs is where the teacher's review happens: the queue shows it
on a page, this shows it in the conversation.

Read-only preview. ``build_plan`` captures a Canvas baseline but writes
nothing, locally or remotely. Every state change -- copying staged AI values
into the teacher fields, approving rows, freezing, pushing -- happens in
``apply_plan``, inside one session lock held by the caller.

That ordering is forced, not stylistic: ``push_grades`` refuses unless the
requested ids equal the frozen review's ids exactly (``review_mismatch``), so a
``skip_those`` answer has to shrink the set *before* the freeze. Resolving
answers first and freezing once is the only shape that works, and it has the
happy side effect that a preview the teacher never applies leaves nothing
behind.

Identity: this module speaks Canvas ``user_id``, like the rest of the
powergrader package. Pseudonym resolution belongs to the MCP tool layer above
it, which never lets a ``user_id`` cross the boundary.

Cost, so it is a known quantity rather than a surprise: an apply reads each
student's submission three times -- once here to recompute the plan digest,
once in ``review_push`` to freeze the baseline, once in ``push_grades`` to
drift-check against it. The three serve different purposes and none is
redundant. The first is load-bearing specifically for
``overwrites_existing_score``: ``review_push`` captures its baseline fresh at
apply time, so a score that appeared in Canvas after the preview would be
folded silently into that baseline and never raise the question the teacher
should have been asked. A 25-student session therefore costs ~75 GETs on
apply; watch ``X-Rate-Limit-Remaining`` if sessions get much larger.
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
    "overwrites_existing_score": ("overwrite", "skip_those"),
    "missing_score": ("comment_only", "skip_those"),
    "pseudonym_in_feedback": ("skip_those", "post_anyway"),
    "held_not_scored": ("proceed", "stop"),
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
    bytes that will actually be pushed, not the row's pre-approval state.
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
    from api.platform_services.canvas_client import canvas_get, _canvas_send

    return canvas_get, _canvas_send


def _question(kind: str, detail: str, user_ids: list[str]) -> dict:
    return {
        "id": kind,
        "kind": kind,
        "detail": detail,
        "user_ids": sorted(user_ids),
        "options": list(QUESTION_OPTIONS[kind]),
    }


def build_plan(session: dict, *, canvas_get=None, pseudonyms=()) -> dict:
    """Read-only. What would be posted, and what needs answering first.

    ``pseudonyms`` is every stand-in name in the local vault. Feedback that
    contains one would reach a real student as a stranger's fake name, or as
    their own -- which they have never seen either.
    """
    if canvas_get is None:
        canvas_get, _ = default_transports()
    students = [s for s in session.get("students", []) if s.get("user_id") is not None]
    if any(s.get("push_state") == "sent_unknown" for s in students):
        return {
            "ok": False,
            "code": "canvas_write_attention",
            "error": "A previous Canvas write could not be verified. Review Canvas before retrying.",
        }
    candidates = [s for s in students if _staged(s)]
    candidate_ids = [str(s["user_id"]) for s in candidates]

    above, overwrites, missing, tainted = [], [], [], []
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

        if "submission" in payload:
            baseline, error = session_actions._fetch_snapshot(session, user_id, canvas_get)
            if error:
                return {"ok": False, "code": error,
                        "error": "Could not read the current Canvas state for this session."}
            # A leftover or auto-derived score on work Canvas still marks
            # submitted/pending_review is the reason this row is in the
            # session. Asking to overwrite it treats unfinished SpeedGrader
            # work as a finished grade.
            state = str(baseline.get("workflow_state") or "").strip().casefold()
            if baseline.get("score") is not None and state not in {
                    "submitted", "pending_review"}:
                overwrites.append(user_id)

    receives_nothing = [str(s["user_id"]) for s in students
                        if not _staged(s) and not s.get("posted")]

    questions = []
    if above:
        questions.append(_question(
            "score_above_possible",
            "Staged score is higher than the item is worth.", above))
    if overwrites:
        questions.append(_question(
            "overwrites_existing_score",
            "Canvas already has a score for these submissions.", overwrites))
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
               load_session, save_session, canvas_get=None, canvas_send=None,
               pseudonyms=()) -> tuple[dict, int]:
    """Approve, freeze and push exactly what a matching preview described.

    ``approve_rows`` runs before the freeze, so ``review_push`` sees ordinary
    approved rows and needs no special eligibility handling.
    """
    if canvas_get is None or canvas_send is None:
        default_get, default_send = default_transports()
        canvas_get = canvas_get or default_get
        canvas_send = canvas_send or default_send

    session = load_session(session_id)
    if not session:
        return {"ok": False, "code": "session_not_found", "error": "Session not found."}, 404

    plan = build_plan(session, canvas_get=canvas_get, pseudonyms=pseudonyms)
    if not plan.get("ok"):
        return plan, 200
    if plan["digest"] != str(expected_digest or ""):
        return {"ok": False, "code": "plan_changed",
                "error": ("The staged scores or the Canvas state changed since the "
                          "preview. Preview again before applying.")}, 409

    resolved = resolve_answers(plan, answers)
    if not resolved.get("ok"):
        return resolved, 409

    user_ids = resolved["user_ids"]
    approve_rows(session, user_ids)
    save_session(session)

    frozen, status = session_actions.review_push(
        session_id, user_ids=json.dumps(user_ids),
        load_session=load_session, save_session=save_session, canvas_get=canvas_get,
    )
    if not frozen.get("ok"):
        return frozen, status

    pushed, status = session_actions.push_grades(
        session_id, user_ids=json.dumps(user_ids), review_token=frozen["review_token"],
        load_session=load_session, save_session=save_session,
        canvas_send=canvas_send, canvas_get=canvas_get,
    )
    if pushed.get("ok"):
        pushed = dict(pushed)
        pushed["skipped"] = resolved["skipped"]
    return pushed, status
