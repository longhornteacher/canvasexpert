"""Chat-side scoring apply: plan, questions, and the narrow write.

The ordinary Assignment write is one send: the reviewed raw score and
plain-text comment go to the Canvas Submissions endpoint, and Canvas applies
every gradebook and late-policy adjustment from there. This module performs no
Canvas read before the send -- no existing-score lookup, no frozen baseline, no
drift check. (Staging makes one separate read of the assignment's posting policy,
only to warn; see ``read_posting_policy``.) After the send it makes exactly one read-only check of every
posted numeric score: a batched submissions read to confirm Canvas stored what
was sent and honored any late decision. It is never retried and never corrects
a row. Canvas Live is the record and the place for later edits.

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
import math
from datetime import datetime, timezone
from api import score_ledger

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
    "late_days": ("post_late_days", "waive_late", "stop"),
}

# Answers that drop the question's affected rows from the write. Everything
# else posts them. "stop" is separate: it abandons the whole apply.
_SKIP_ANSWERS = {"skip_those"}
_STOP_ANSWERS = {"stop"}
# Answers that waive the late penalty for every row the question lists.
_WAIVE_ANSWERS = {"waive_late"}

_LATE_LEGEND = (
    "canvas_days is Canvas's calendar-day count; late_days is school days after "
    "the due date (weekends and Holidays.csv dates excluded, less any grace days) "
    "and is the value posted unless waived."
)
_LATE_DETAIL = (
    "Decide how late work posts. post_late_days posts each row's late_days as "
    "listed; waive_late posts every listed row with the late penalty waived; "
    "stop abandons this apply. To mix per row, resubmit results with late_days: 0 "
    "on the rows to waive and answer post_late_days."
)

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


def _projected_payload(student: dict, session: dict | None = None, *,
                       grade_mode: str = "post_score", waive_user_ids=()) -> dict:
    """What ``_payload`` will build once the staged values are approved.

    Mirrors the approval copy in ``approve_rows`` so the plan digest covers the
    bytes that will actually be sent, not the row's pre-approval state.
    ``waive_user_ids`` carries a ``waive_late`` answer, which the apply send
    honors; the plan digest itself is built without it.
    """
    projected = dict(student)
    if student.get("teacher_score") is None:
        projected["teacher_score"] = student.get("ai_score")
    if not (student.get("teacher_feedback") or "").strip():
        projected["teacher_feedback"] = student.get("ai_feedback") or ""
    return session_actions._payload(
        projected, grade_mode=grade_mode,
        waive_late=session_actions.late_waived(
            session or {}, student, waive_user_ids, grade_mode=grade_mode))


def default_transports():
    """This module owns the Canvas transport, so the MCP layer never imports it.

    ``api/tests/dailywriting/test_dw_canvas_ingest.py`` enforces that no
    ``api/mcp_server/`` module imports ``canvas_client``: the assistant-facing
    layer is not allowed to hold a live Canvas call. Callers there ask this
    package to do the talking instead. Tests still inject their own fakes.
    """
    from api.platform_services.canvas_client import _canvas_send

    return _canvas_send


def default_read_transport():
    """The read-only counterpart of ``default_transports`` for the posted-score check.

    Returns ``read(path, params) -> (rows, error)``; a list that could not be
    proven complete is an error, so a partial page never looks like a result.
    """
    from api.platform_services.canvas_client import canvas_get_all_complete

    def read(path, params):
        rows, error, complete = canvas_get_all_complete(path, params=params)
        if error or not complete:
            return None, error or "pagination_incomplete"
        return rows, None

    return read


def default_assignment_read():
    """One-object read of a single assignment, for the posting-policy check.

    ``default_read_transport`` only accepts list pages; an assignment GET returns
    one object. Returns ``read(path) -> (assignment, error)``. Like the other
    transports here, this module owns the call so the MCP layer never imports
    ``canvas_client``.
    """
    from api.platform_services.canvas_client import canvas_get

    def read(path):
        assignment, error = canvas_get(path, timeout=10)
        if error or not isinstance(assignment, dict):
            return None, error or "unexpected_response"
        return assignment, None

    return read


def read_posting_policy(course_id, assignment_id, canvas_read=None) -> dict:
    """One assignment read: does Canvas hold scores and comments back until posted?

    ``post_manually`` is ``True`` or ``False``, or ``None`` when Canvas could not
    be read or did not say. Never raises and never blocks staging; the answer
    only becomes a warning. It is not part of any digest.
    """
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    post_manually = None
    try:
        read = canvas_read or default_assignment_read()
        assignment, error = read(f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
        value = assignment.get("post_manually") if not error and isinstance(assignment, dict) else None
        if isinstance(value, bool):
            post_manually = value
    except Exception:
        post_manually = None
    return {"post_manually": post_manually, "checked_at": checked_at}


def posting_warnings(posting_policy: dict | None) -> list[dict]:
    """Assignment-level warnings for what students see as soon as the push lands."""
    post_manually = (posting_policy or {}).get("post_manually")
    if post_manually is False:
        return [{"code": "posts_automatically",
                 "text": "This assignment posts automatically: students see scores "
                         "and comments as soon as they are pushed."}]
    if post_manually is None:
        return [{"code": "posting_unchecked",
                 "text": "Could not check this assignment's posting policy. Students "
                         "may see scores and comments as soon as they are pushed."}]
    return []


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


def waived_user_ids(plan: dict, answers: dict | None) -> list[str]:
    """Rows a ``waive_late`` answer waives: every row listed in that question."""
    answers = {str(k): str(v) for k, v in (answers or {}).items()}
    waived: set[str] = set()
    for question in plan.get("questions") or []:
        if answers.get(question["id"]) in _WAIVE_ANSWERS:
            waived.update(question["user_ids"])
    return sorted(waived)


def late_decisions(session: dict, plan: dict, answers: dict | None = None) -> dict:
    """``{user_id: {decision, late_days?}}`` for each late candidate row.

    Reflects the post-answer decision once ``answers`` carries a ``waive_late``
    answer; before any answer a policy-course row shows what ``post_late_days``
    would post.
    """
    grade_mode = str(plan.get("grade_mode") or "post_score")
    if grade_mode == "feedback_only":
        return {}
    waived = set(waived_user_ids(plan, answers))
    wanted = set(plan.get("candidate_ids") or [])
    out = {}
    for student in session.get("students", []):
        uid = str(student.get("user_id"))
        if uid not in wanted:
            continue
        decision = session_actions.late_decision(
            student, waive=session_actions.late_waived(session, student, waived))
        if decision:
            out[uid] = decision
    return out


def _finite(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def preview_rows(session: dict, plan: dict, user_ids, answers: dict | None = None) -> dict:
    """Read-only ``{user_id: row}`` for rows a stage will post.

    Every value that reaches Canvas is read from ``_projected_payload``, the same
    builder the plan digest covers and ``push_grades`` sends from: ``comment`` is
    the projected ``text_comment`` and ``entered`` the projected ``posted_grade``
    (``None`` in feedback-only mode), so the preview cannot show anything Canvas
    will not receive. ``agent_commentary`` is teacher-only and never in a payload.
    Warnings are information, never blocking.
    """
    grade_mode = str(plan.get("grade_mode") or session.get("grade_mode") or "post_score")
    waived = waived_user_ids(plan, answers)
    decisions = late_decisions(session, plan, answers)
    assignment = session.get("assignment") or {}
    wanted = {str(uid) for uid in user_ids}
    rows = {}
    for student in session.get("students", []):
        uid = str(student.get("user_id"))
        if uid not in wanted:
            continue
        payload = _projected_payload(student, session, grade_mode=grade_mode,
                                     waive_user_ids=waived)
        submission = payload.get("submission") or {}
        entered = submission.get("posted_grade")
        raw = student.get("teacher_score") if student.get("teacher_score") is not None \
            else student.get("ai_score")
        possible = _effective_points_possible({}, assignment, None, student)
        decision = decisions.get(uid)
        late = None
        warnings = []
        if decision:
            late = {**decision, "status": submission.get("late_policy_status")}
            days = decision.get("late_days")
            if decision["decision"] == "waived":
                warnings.append({"code": "late_waived", "text": "Late penalty waived."})
            elif decision["decision"] == "applied" and days:
                warnings.append({"code": "late_penalty_applied",
                                 "text": f"Late penalty applied: {days} school day(s)."})
            elif decision["decision"] == "canvas":
                warnings.append({"code": "late_canvas_policy",
                                 "text": "Submitted late: Canvas's own late policy decides any deduction."})
        baseline = student.get("submission_baseline") or {}
        existing = _finite(baseline.get("entered_score"))
        if existing is None:
            existing = _finite(baseline.get("canvas_score"))
        if _finite(entered) is not None and existing is not None \
                and abs(existing - _finite(entered)) > 1e-9:
            warnings.append({"code": "replaces_canvas_score",
                             "text": f"Replaces a score already in Canvas "
                                     f"({session_actions._format_number(existing)}), "
                                     f"as of session preparation."})
        if _finite(entered) is not None and _finite(raw) is not None \
                and abs(_finite(raw) - _finite(entered)) > 1e-9:
            reason = ("score curve" if student.get("frozen_curve")
                      else "grading floor" if student.get("grading") else "adjustment")
            warnings.append({"code": "entered_differs_from_raw",
                             "text": f"Entered {entered} differs from raw "
                                     f"{session_actions._format_number(raw)} ({reason})."})
        rows[uid] = {
            "raw_score": _finite(raw),
            "entered": entered,
            "points_possible": float(possible) if possible is not None else None,
            "late": late,
            "comment": (payload.get("comment") or {}).get("text_comment") or "",
            "agent_commentary": str(student.get("agent_commentary") or ""),
            "warnings": warnings,
        }
    return rows


def build_plan(session: dict, *, pseudonyms=()) -> dict:
    """Read-only. What would be posted, and what needs answering first.

    ``pseudonyms`` is every stand-in name in the local vault. Feedback that
    contains one would reach a real student as a stranger's fake name, or as
    their own -- which they have never seen either.
    """
    grade_mode = str(session.get("grade_mode") or "post_score")
    if grade_mode not in {"post_score", "feedback_only"}:
        return {"ok": False, "code": "invalid_grade_mode",
                "error": "The scoring grade mode is invalid."}
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
        payload = _projected_payload(student, session, grade_mode=grade_mode)

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
    insincere = ([str(s["user_id"]) for s in candidates
                  if (s.get("grading") or {}).get("insincere")]
                 if grade_mode == "post_score" else [])
    # The session's late_policy settles the question up front: waive and apply
    # never ask. Only ask puts the late_days question to the teacher. Feedback-only
    # sends no late fields, so it has no late decision and ignores late_policy.
    late_candidates = ([s for s in candidates
                        if s.get("grading") and s.get("canvas_late")
                        and str(session.get("late_policy") or "ask") == "ask"]
                       if grade_mode == "post_score" else [])

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
            _LATE_DETAIL,
            [str(s["user_id"]) for s in late_candidates],
            legend=_LATE_LEGEND,
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
        "digest": _plan_digest(candidate_ids, candidates, questions, session,
                               grade_mode=grade_mode),
        "grade_mode": grade_mode,
    }


def _plan_digest(candidate_ids, candidates, questions, session=None, *,
                 grade_mode: str = "post_score") -> str:
    """Covers the rows, the exact bytes to be pushed, and what was asked.

    A staged score edited between preview and apply, a question that appears or
    disappears, or a row entering or leaving the set all change this, so apply
    refuses rather than landing something the teacher never read.
    """
    identity = {
        "user_ids": candidate_ids,
        "payloads": {str(s["user_id"]): _projected_payload(s, session, grade_mode=grade_mode)
                     for s in candidates},
        "questions": [{"kind": q["kind"], "user_ids": q["user_ids"]} for q in questions],
    }
    curve_rules = {str(s["user_id"]): s.get("frozen_curve")
                   for s in candidates if s.get("frozen_curve")}
    if curve_rules:
        identity["curve_rules"] = curve_rules
    # Missing/default post_score deliberately retains the established digest
    # shape so actionable pilot stages remain valid. The non-default mode is
    # explicit because it changes the outbound payload and approval questions.
    if grade_mode == "feedback_only":
        identity["grade_mode"] = grade_mode
    return session_actions._digest(identity)


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
        f"{q['id']}={answers[q['id']]}: offered {', '.join(QUESTION_OPTIONS[q['kind']])}"
        for q in plan["questions"]
        if answers[q["id"]] not in QUESTION_OPTIONS[q["kind"]]
    ]
    if invalid:
        return {"ok": False, "code": "invalid_answer",
                "error": "Not an offered option: " + "; ".join(sorted(invalid))}

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
    waived = [uid for uid in waived_user_ids(plan, answers) if uid in set(selected)]
    return {"ok": True, "user_ids": selected, "skipped": sorted(skipped), "waived": waived}


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


def _verify_posted_scores(session_id: str, pushed: dict, load_session, canvas_read) -> None:
    """Verify every accepted numeric score with one bounded, read-only pass."""
    def finite_number(value):
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    rows = [r for r in pushed.get("results") or [] if r.get("status") == "pushed"]
    if not rows:
        return
    session = load_session(session_id) or {}
    try:
        prior_events = score_ledger.list_events(str(session.get("course_id") or ""),
                                                str(session.get("assignment_id") or ""))
    except Exception:
        prior_events = []
    rows = [r for r in rows if finite_number(r.get("entered_score")) is not None]
    numeric_rows = [r for r in rows if r.get("user_id") is not None]
    by_user = {}
    read_ok = True
    try:
        if canvas_read is None:
            canvas_read = default_read_transport()
        for start in range(0, len(numeric_rows), 100):
            chunk = numeric_rows[start:start + 100]
            found, error = canvas_read(
                f"/api/v1/courses/{session['course_id']}/assignments/{session['assignment_id']}/submissions",
                {"student_ids[]": [str(r["user_id"]) for r in chunk], "per_page": 100},
            )
            if error or not isinstance(found, list):
                read_ok = False
                break
            by_user.update({str(r.get("user_id")): r for r in found if isinstance(r, dict)})
    except Exception:
        read_ok = False

    for row in rows:
        late = row.setdefault("late", {"decision": "canvas", "sent_status": None})
        read = by_user.get(str(row["user_id"])) if read_ok else None
        if read is None:
            late.update({"readback": "unavailable", "verification": "score_readback_unavailable"})
            continue
        entered = finite_number(read.get("entered_score"))
        canvas_score = finite_number(read.get("score"))
        deducted = finite_number(read.get("points_deducted"))
        status = str(read.get("late_policy_status") or "")
        sent = finite_number(row.get("entered_score"))
        if sent is None:
            sent = finite_number(row.get("score_sent"))
        if sent is None:
            # The submission payload is retained only as private push evidence.
            sent = finite_number(row.get("sent_score"))
        # Session_actions records the exact entered grade on the pushed row.
        if sent is None:
            sent = finite_number(row.get("posted_grade"))
        late.update({"readback": "available", "entered_score": entered,
                     "canvas_score": canvas_score, "points_deducted": deducted,
                     "late_policy_status": status})
        curve = next((item.get("frozen_curve") or {} for item in session.get("students") or []
                      if str(item.get("user_id")) == str(row.get("user_id"))), {})
        if curve.get("rule_id"):
            late.update({"raw_score": curve.get("raw_score"),
                         "curve_rule_id": curve.get("rule_id")})
        valid_statuses = {"late", "missing", "none", "extended"}
        match = (sent is not None and entered is not None and canvas_score is not None
                 and abs(entered - sent) <= 1e-6)
        decision = str(late.get("decision") or "canvas")
        sent_status = late.get("sent_status")
        if match and status and status not in valid_statuses:
            match = False
        if match and sent_status is not None and status != str(sent_status):
            match = False
        if match and deducted is None:
            match = abs(canvas_score - entered) <= 1e-6
        elif match:
            match = abs(canvas_score - (entered - deducted)) <= 1e-6
            if deducted > 1e-6 and status not in valid_statuses - {"none", "extended"}:
                match = False
        if not match:
            late.update({"late_honored": False, "verification": "score_mismatch"})
        else:
            late.update({"late_honored": True, "verification": "verified"})
        try:
            student = next((item for item in session.get("students") or []
                            if str(item.get("user_id")) == str(row.get("user_id"))), {})
            baseline = student.get("submission_baseline") or {}
            action = "verified" if match else "failed"
            sent_event = next((event for event in reversed(prior_events)
                if event.get("source") == "ce_apply" and event.get("action") in {"accepted", "intent"}
                and str(event.get("student_id") or "") == str(row.get("user_id") or "")
                and str(event.get("session_id") or "") == str(session_id)
                and str(event.get("stage_id") or "") == str((session.get("staged_scoring_apply") or {}).get("stage_digest") or "")
                and event.get("curve_rule_id") == (student.get("frozen_curve") or {}).get("rule_id")), None)
            score_ledger.append_event({
                "source": "ce_apply", "action": action,
                "course_id": session.get("course_id"), "assignment_id": session.get("assignment_id"),
                "student_id": row.get("user_id"),
                "attempt": baseline.get("attempt") or student.get("current_attempt"),
                "submission_digest": baseline.get("submission_digest"),
                "raw_score": (student.get("frozen_curve") or {}).get("raw_score"),
                "entered_score": entered, "canvas_score": canvas_score,
                "points_deducted": deducted, "late_status": status,
                "late_days": read.get("late_days"),
                "feedback": (sent_event or {}).get("feedback"),
                "curve_rule_id": (student.get("frozen_curve") or {}).get("rule_id"),
                "session_id": session_id, "stage_id": (session.get("staged_scoring_apply") or {}).get("stage_digest"),
            }, idempotency_key=f"verify:{session_id}:{row.get('target_digest') or row.get('request_digest') or row.get('user_id')}:{action}")
        except Exception:
            # Verification is still reflected in the in-memory result. The
            # accepted write is never retried to repair the private export.
            pass
    verification_codes = [str((row.get("late") or {}).get("verification") or "") for row in rows]
    try:
        score_ledger.flush_exports(str(session.get("course_id") or ""),
                                   str(session.get("assignment_id") or ""))
    except Exception:
        # The accepted Canvas writes and their canonical event files remain
        # intact; export failure is surfaced as durable-history unavailability
        # by the ledger reader and never causes a resend.
        pass
    if "score_mismatch" in verification_codes:
        pushed["ok"] = False
        pushed["code"] = "score_mismatch"
    elif "score_readback_unavailable" in verification_codes:
        pushed["ok"] = False
        pushed["code"] = "score_readback_unavailable"


def apply_plan(session_id: str, *, expected_digest: str, answers: dict | None,
               load_session, save_session, canvas_send=None, canvas_read=None,
               pseudonyms=(), idempotency_key: str = "") -> tuple[dict, int]:
    """Approve and send exactly what a matching preview described.

    ``approve_rows`` runs before the send, so ``push_grades`` sees ordinary
    approved rows. There is no freeze and no drift check: the plan digest and
    the frozen answers are the only things standing between the preview the
    teacher read and the bytes that go out. Rows a ``waive_late`` answer waived
    go to ``push_grades`` by id, so ``_payload`` builds the waived bytes for
    exactly those rows. After the send, ``_verify_posted_scores`` makes the one
    read-only check of every posted numeric score.
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
        grade_mode=plan["grade_mode"],
        waive_late_user_ids=resolved.get("waived") or (),
    )
    if pushed.get("ok"):
        pushed = dict(pushed)
        pushed["skipped"] = resolved["skipped"]
    _verify_posted_scores(session_id, pushed, load_session, canvas_read)
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
