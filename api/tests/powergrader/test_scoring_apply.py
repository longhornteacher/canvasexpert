"""Chat-side scoring apply: the plan, its questions, and the narrow write.

Canvas is faked at the ``canvas_send`` injection point these functions already
take. No live scoring run, no real student data.

The write is deliberately narrow: one send, no read-back. These tests pin that
as a law, not an implementation detail.
"""
import json
import hashlib

import pytest

from api.powergrader import scoring_apply, session_actions
from api import score_ledger

def _session(**overrides):
    session = {
        "session_id": "session-1", "course_id": "course-1", "assignment_id": "assignment-1",
        "assignment": {"points_possible": 10},
        "students": [
            {"user_id": "9001", "ai_score": 8, "ai_feedback": "Clear evidence."},
            {"user_id": "9002", "ai_score": 6, "ai_feedback": "Good structure."},
        ],
    }
    session.update(overrides)
    return session


def test_scoring_session_canvas_feedback_contains_no_em_dashes():
    payload = session_actions._payload({
        "teacher_score": 8,
        "teacher_feedback": "Strong claim \u2014 add evidence.",
    })

    assert payload["comment"]["text_comment"] == "Strong claim - add evidence."


def test_feedback_payload_preserves_literal_punctuation_and_unicode():
    """CONTRACT: ordinary feedback reaches the outbound JSON payload faithfully.

    Literal ``&``, ``<``, ``>``, straight and curly quotes, en dash, newlines,
    and other supplied Unicode must survive CE's own payload construction. The
    only expected change is the em dash, which normalizes to an ASCII hyphen by
    the locked ``normalize_student_text`` rule.

    This is a synthetic string, not a real submission: no diagnostic feedback
    text is persisted and no student data is involved.
    """
    supplied = (
        "Tom & Jerry <b>bold</b> \"straight\" \u201ccurly\u201d "
        "en\u2013dash \u2014 em\u2013dash\nsecond line \u00e9\u00fc\u4e2d\u6587"
    )

    payload = session_actions._payload({
        "teacher_score": 7,
        "teacher_feedback": supplied,
    })

    text = payload["comment"]["text_comment"]
    assert "&" in text and "<b>" in text and ">" in text
    assert '"straight"' in text and "\u201ccurly\u201d" in text
    assert "en\u2013dash" in text
    assert "\n" in text
    assert "\u00e9\u00fc\u4e2d\u6587" in text
    # The em dash is the one locked normalization: it becomes an ASCII hyphen.
    assert "\u2014" not in text
    assert "en\u2013dash - em\u2013dash" in text
    # The payload is JSON-serializable exactly as supplied.
    assert json.loads(json.dumps(payload))["comment"]["text_comment"] == text


def test_no_policy_keeps_fractional_score_unrounded():
    payload = session_actions._payload({
        "teacher_score": 7.5, "teacher_feedback": "Clear evidence.",
        "grading": {"floor_percent": None, "points_possible": 10},
    })
    assert payload["submission"]["posted_grade"] == "7.5"


# ── The plan ────────────────────────────────────────────────────────────────


def test_build_plan_writes_nothing():
    """LAW: preview is read-only, locally and remotely.

    Planning performs no Canvas read at all, so a preview the teacher never
    applies leaves nothing behind and costs no Canvas call.
    """
    session = _session()
    before = repr(session)

    plan = scoring_apply.build_plan(session, pseudonyms=[])

    assert plan["ok"] and plan["candidate_ids"] == ["9001", "9002"]
    assert repr(session) == before, "build_plan mutated the session"


def test_plan_digest_changes_when_a_staged_score_changes():
    """LAW: the digest covers the bytes that will be pushed.

    Apply compares it, so an edit between preview and apply is refused rather
    than landing something the teacher never read.
    """
    session = _session()
    first = scoring_apply.build_plan(session)["digest"]

    session["students"][0]["ai_score"] = 9
    second = scoring_apply.build_plan(session)["digest"]

    assert first != second


def test_posted_rows_are_not_candidates():
    session = _session()
    session["students"][0]["posted"] = True

    plan = scoring_apply.build_plan(session)

    assert plan["candidate_ids"] == ["9002"]


# ── Questions ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(scoring_apply.QUESTION_OPTIONS))
def test_every_question_kind_is_answerable(kind):
    """CONTRACT: each kind offers at least two options and is reachable.

    Parametrized over the kind registry, so a new question kind is covered
    without a new test.
    """
    options = scoring_apply.QUESTION_OPTIONS[kind]
    assert len(options) >= 2
    assert len(set(options)) == len(options)


def test_no_grade_state_question_remains():
    """LAW: the MCP scoring lane asks nothing about Canvas grade state.

    No existing-score lookup, no overwrite question, no frozen baseline. The
    teacher reviews the result in Canvas, which is the review surface.
    """
    assert "overwrites_existing_score" not in scoring_apply.QUESTION_OPTIONS
    assert not hasattr(session_actions, "_fetch_snapshot")
    assert not hasattr(session_actions, "_same_postcondition")
    assert not hasattr(session_actions, "_same_score_baseline")
    assert not hasattr(session_actions, "_same_comments")
    assert not hasattr(session_actions, "review_push")


def test_score_above_possible_is_raised():
    session = _session()
    session["students"][0]["ai_score"] = 12

    plan = scoring_apply.build_plan(session)

    question = next(q for q in plan["questions"] if q["kind"] == "score_above_possible")
    assert question["user_ids"] == ["9001"]


def test_feedback_with_no_score_is_raised():
    session = _session()
    session["students"][0]["ai_score"] = None

    plan = scoring_apply.build_plan(session)

    question = next(q for q in plan["questions"] if q["kind"] == "missing_score")
    assert question["user_ids"] == ["9001"]


def test_pseudonym_in_feedback_is_raised():
    """The enforcement half of the scoring contract's don't-quote rule.

    Scrubbing is whole-word, so an ordinary word in a response may have become
    a pseudonym. The contract tells the model not to quote it back; this is
    where that is actually checkable.
    """
    session = _session()
    session["students"][1]["ai_feedback"] = "You wrote that Pikachu rang loudly."

    plan = scoring_apply.build_plan(session, pseudonyms=["Pikachu", "Snorlax"])

    question = next(q for q in plan["questions"] if q["kind"] == "pseudonym_in_feedback")
    assert question["user_ids"] == ["9002"]


def test_students_who_would_receive_nothing_are_raised():
    session = _session()
    session["students"].append({"user_id": "9003"})

    plan = scoring_apply.build_plan(session)

    question = next(q for q in plan["questions"] if q["kind"] == "held_not_scored")
    assert question["user_ids"] == ["9003"]
    assert "9003" not in plan["candidate_ids"]


def test_a_clean_session_asks_nothing():
    """Do not inflate the question list: a teacher asked something every time
    stops reading the questions."""
    plan = scoring_apply.build_plan(_session())

    assert plan["questions"] == []


# ── Answers ─────────────────────────────────────────────────────────────────


def test_unanswered_questions_block_the_apply():
    """LAW: a question the agent can ignore is not a question."""
    session = _session()
    session["students"][0]["ai_score"] = 12
    plan = scoring_apply.build_plan(session)

    resolved = scoring_apply.resolve_answers(plan, {})

    assert resolved["ok"] is False
    assert resolved["code"] == "unanswered_questions"
    assert "score_above_possible" in resolved["unanswered"]


def test_skip_those_removes_those_students_from_the_write():
    """LAW: the answer changes what lands."""
    session = _session()
    session["students"][0]["ai_score"] = 12
    plan = scoring_apply.build_plan(session)

    resolved = scoring_apply.resolve_answers(
        plan, {"score_above_possible": "skip_those"})

    assert resolved["ok"] is True
    assert resolved["user_ids"] == ["9002"]
    assert resolved["skipped"] == ["9001"]


def test_post_anyway_keeps_them():
    session = _session()
    session["students"][0]["ai_score"] = 12
    plan = scoring_apply.build_plan(session)

    resolved = scoring_apply.resolve_answers(
        plan, {"score_above_possible": "post_anyway"})

    assert resolved["user_ids"] == ["9001", "9002"]


def test_an_answer_outside_the_offered_options_is_refused():
    session = _session()
    session["students"][0]["ai_score"] = 12
    plan = scoring_apply.build_plan(session)

    resolved = scoring_apply.resolve_answers(
        plan, {"score_above_possible": "obviously_just_do_it"})

    assert resolved["ok"] is False and resolved["code"] == "invalid_answer"


def test_stop_abandons_the_whole_apply():
    session = _session()
    session["students"].append({"user_id": "9003"})
    plan = scoring_apply.build_plan(session)

    resolved = scoring_apply.resolve_answers(plan, {"held_not_scored": "stop"})

    assert resolved["ok"] is False and resolved["code"] == "stopped_by_answer"


def test_skipping_everything_refuses_rather_than_pushing_nothing():
    session = _session()
    session["students"][0]["ai_score"] = 12
    session["students"][1]["ai_score"] = 12
    plan = scoring_apply.build_plan(session)

    resolved = scoring_apply.resolve_answers(
        plan, {"score_above_possible": "skip_those"})

    assert resolved["ok"] is False and resolved["code"] == "nothing_to_post"


# ── Approval ────────────────────────────────────────────────────────────────


def test_approve_rows_copies_staged_values_without_a_blind_datapoint(monkeypatch):
    """LAW: approving from chat is not a blind-first observation.

    save_student records an implicit blind datapoint because a teacher who
    scores without revealing the AI suggestion is the cleanest one there is.
    Copying the AI's own score is the opposite, and recording it as blind would
    quietly corrupt that record.
    """
    from api.powergrader import blind_first

    recorded = []
    monkeypatch.setattr(blind_first, "record_implicit_blind",
                        lambda *a, **k: recorded.append(a))
    session = _session()

    scoring_apply.approve_rows(session, ["9001"])

    assert session["students"][0]["teacher_score"] == 8
    assert session["students"][0]["teacher_feedback"] == "Clear evidence."
    assert session["students"][0]["status"] == "approved"
    assert session["students"][1].get("status") is None, "untouched row was approved"
    assert recorded == []


def test_approve_rows_does_not_overwrite_a_teacher_edit():
    session = _session()
    session["students"][0]["teacher_score"] = 10
    session["students"][0]["teacher_feedback"] = "My own words."

    scoring_apply.approve_rows(session, ["9001"])

    assert session["students"][0]["teacher_score"] == 10
    assert session["students"][0]["teacher_feedback"] == "My own words."


# ── End to end ──────────────────────────────────────────────────────────────


def _store(session):
    saved = {"session": session}
    return (lambda _sid: saved["session"],
            lambda value: saved.__setitem__("session", value),
            saved)


def _readback_for(sent, params):
    rows = []
    for uid in params.get("student_ids[]", []):
        payload = next(item[-1] for item in sent if item[-2].endswith("/" + str(uid)))
        entered = float(payload["submission"]["posted_grade"])
        rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                     "points_deducted": None,
                     "late_policy_status": payload["submission"].get("late_policy_status")})
    return rows, None


def test_apply_plan_pushes_the_previewed_rows():
    """EXAMPLE: preview, answer, apply -- one push per selected student."""
    session = _session()
    load, save, saved = _store(session)
    sent = []

    def canvas_send(method, path, payload, timeout=30):
        sent.append((method, path, payload))
        return ({"id": 1}, None)

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save, canvas_send=canvas_send,
        canvas_read=lambda _path, params: _readback_for(sent, params))

    assert result["ok"] is True
    assert len(sent) == 2
    assert all(student.get("posted") for student in saved["session"]["students"])


def test_apply_plan_refuses_a_stale_digest():
    """LAW: staged scores edited after the preview are not silently posted."""
    session = _session()
    load, save, _saved = _store(session)
    sent = []

    plan = scoring_apply.build_plan(session)
    session["students"][0]["ai_score"] = 9

    result, status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save,
        canvas_send=lambda *a, **k: sent.append(a) or ({}, None))

    assert result["ok"] is False and result["code"] == "plan_changed"
    assert sent == [], "refused apply still called Canvas"


def test_apply_plan_makes_no_canvas_write_while_a_question_is_open():
    session = _session()
    session["students"][0]["ai_score"] = 12
    load, save, _saved = _store(session)
    sent = []

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers=None,
        load_session=load, save_session=save,
        canvas_send=lambda *a, **k: sent.append(a) or ({}, None))

    assert result["code"] == "unanswered_questions"
    assert sent == []


def test_apply_plan_skips_what_the_answer_skipped():
    session = _session()
    session["students"][0]["ai_score"] = 12
    load, save, _saved = _store(session)
    sent = []

    def canvas_send(method, path, payload, timeout=30):
        sent.append((method, path, payload))
        return ({"id": 1}, None)

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"],
        answers={"score_above_possible": "skip_those"},
        load_session=load, save_session=save, canvas_send=canvas_send,
        canvas_read=lambda _path, params: _readback_for(sent, params))

    assert result["ok"] is True
    assert len(sent) == 1 and "9002" in sent[0][1]


def test_apply_plan_reports_exact_partial_post_recovery_rows():
    session = _session()
    load, save, _saved = _store(session)
    sent = []

    def canvas_send(method, path, payload, timeout=30):
        user_id = path.rstrip("/").split("/")[-1]
        sent.append(user_id)
        if user_id == "9002":
            return None, "HTTP 400 rejected"
        return {"id": 1}, None

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save, canvas_send=canvas_send)

    assert sent == ["9001", "9002"]
    assert result["code"] == "partial_post_remaining"
    assert result["posted_rows"] == ["9001"]
    assert result["remaining_rows"] == ["9002"]


# ── Score read-back ────────────────────────────────────────────────────────

def test_successful_numeric_send_gets_score_readback():
    """LAW: every accepted numeric write gets bounded score verification."""
    session = _session()
    load, save, _saved = _store(session)
    sent = []

    def canvas_send(method, path, payload, timeout=30):
        sent.append((method, path, payload))
        return ({"id": 1}, None)

    reads = []
    def canvas_read(path, params):
        reads.append((path, params))
        return _readback_for(sent, params)

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save, canvas_send=canvas_send,
        canvas_read=canvas_read)

    assert result["ok"] is True
    assert len(reads) == 1
    receipt = session["push_log"][-1]["results"][0]
    assert receipt["entered_score"] == 8
    assert result["results"][0]["late"]["verification"] == "verified"
    assert "postcondition_digest" not in receipt


def test_transport_unknown_never_repeats_or_reverifies():
    """LAW: a transport-unknown send never triggers a second PUT, automatic
    re-verification, or an exposed Canvas grade result."""
    session = _session()
    load, save, _saved = _store(session)
    sent = []

    def canvas_send(method, path, payload, timeout=30):
        sent.append(path)
        return None, "connection lost"

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save, canvas_send=canvas_send)

    assert result["ok"] is False
    assert result["code"] == "write_transport_unknown"
    # One PUT per row, and no row is ever sent twice.
    assert len(sent) == len(set(sent)) == 2
    assert session["students"][0]["push_state"] == "sent_unknown"
    assert not session.get("push_idempotency")


def test_accepted_exact_payload_is_not_sent_twice():
    """LAW: an accepted exact idempotent payload is not sent twice."""
    session = _session()
    load, save, _saved = _store(session)
    sent = []

    def canvas_send(method, path, payload, timeout=30):
        sent.append(path)
        return ({"id": 1}, None)

    plan = scoring_apply.build_plan(session)
    scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save, canvas_send=canvas_send,
        idempotency_key="batch-1")
    first_count = len(sent)

    # Re-approve the same rows and resubmit the identical payload.
    for student in session["students"]:
        student["posted"] = False
        student["status"] = "approved"
    again, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save, canvas_send=canvas_send,
        idempotency_key="batch-1")

    assert len(sent) == first_count, "an accepted payload was sent twice"
    assert again["ok"] is True


# ── Late decision per row ───────────────────────────────────────────────────


def _late_session(*, policy_course=True, late_policy=None, late_days=(2, 0)):
    """Two late candidate rows; ``late_days`` is each row's explicit override."""
    session = _session()
    if late_policy:
        session["late_policy"] = late_policy
    for student, days in zip(session["students"], late_days):
        student["canvas_late"] = True
        student["grading"] = {"floor_percent": 30 if policy_course else None,
                              "points_possible": 10, "insincere": False,
                              "late_days": days, "suggested_late_days": days}
    return session


def _late_fields(payload):
    submission = payload.get("submission") or {}
    return {key: submission[key] for key in ("late_policy_status", "seconds_late_override")
            if key in submission}


@pytest.mark.parametrize("policy_course, waive, late_days, expected", [
    (True, True, 2, {"late_policy_status": "none"}),
    (False, True, None, {"late_policy_status": "none"}),
    (True, False, 2, {"late_policy_status": "late", "seconds_late_override": 172800}),
    (True, False, 0, {"late_policy_status": "none"}),
    (False, False, None, {}),
])
def test_late_decision_decides_the_late_fields_in_the_payload(
        policy_course, waive, late_days, expected):
    """LAW: ``_payload`` is the one place late fields are computed, and waived
    sends status none with no override in any course; posted_grade stays the score."""
    student = {"user_id": "9001", "teacher_score": 8, "canvas_late": True}
    student["grading"] = {"floor_percent": 30 if policy_course else None,
                          "points_possible": 10, "insincere": False,
                          "late_days": late_days, "suggested_late_days": late_days}

    payload = session_actions._payload(student, waive_late=waive)

    assert _late_fields(payload) == expected
    # Waiving never touches the mark: raw score, or the effort-credit mark (8/10 at a 30% floor).
    assert payload["submission"]["posted_grade"] == ("9" if policy_course else "8")


def test_a_row_that_is_not_late_has_no_late_decision():
    assert session_actions.late_decision({"user_id": "9001"}, waive=True) is None


def test_late_days_have_no_default_question_but_explicit_ask_reviews_them():
    assert not any(q["kind"] == "late_days" for q in scoring_apply.build_plan(_late_session())["questions"])
    plan = scoring_apply.build_plan(_late_session(late_policy="ask"))
    question = next(q for q in plan["questions"] if q["kind"] == "late_days")

    assert question["options"] == ["post_late_days", "waive_late", "stop"]
    assert "first meaningful attempt" in question["detail"]
    assert "waive_late" in question["detail"] and "post_late_days" in question["detail"]
    # Generic text only: a per-row reason could disclose an accommodation.
    assert all(set(row) == {"user_id", "late_days"} for row in question["rows"])


@pytest.mark.parametrize("kind", sorted(scoring_apply.QUESTION_OPTIONS))
def test_invalid_answer_lists_the_offered_options(kind):
    """CONTRACT: every question kind names its own options when an answer fails."""
    plan = {"questions": [{"id": kind, "kind": kind, "user_ids": ["9001"]}],
            "candidate_ids": ["9001"]}

    resolved = scoring_apply.resolve_answers(plan, {kind: "not_an_option"})

    assert resolved["code"] == "invalid_answer"
    assert (f"{kind}=not_an_option: offered "
            + ", ".join(scoring_apply.QUESTION_OPTIONS[kind])) in resolved["error"]


def _apply(session, answers, **kwargs):
    load, save, saved = _store(session)
    sent = []

    def canvas_send(method, path, payload, timeout=30):
        sent.append((path.rsplit("/", 1)[-1], payload))
        return ({"id": 1}, None)

    def canvas_read(_path, params):
        rows = []
        for uid in params.get("student_ids[]", []):
            payload = dict(sent).get(str(uid), {})
            entered = float((payload.get("submission") or {}).get("posted_grade"))
            late_status = (payload.get("submission") or {}).get("late_policy_status")
            rows.append({"user_id": int(uid), "entered_score": entered, "score": entered,
                         "points_deducted": None, "late_policy_status": late_status})
        return rows, None

    kwargs.setdefault("canvas_read", canvas_read)

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers=answers,
        load_session=load, save_session=save, canvas_send=canvas_send, **kwargs)
    return result, dict(sent), plan


def test_waive_late_answer_sends_the_waived_bytes_for_exactly_the_listed_rows():
    """EXAMPLE: the answer waives every listed row; rows outside the question
    keep their own payload."""
    session = _late_session(late_policy="ask")
    session["students"].append({"user_id": "9003", "ai_score": 7, "ai_feedback": "Fine."})

    result, sent, plan = _apply(session, {"late_days": "waive_late"})

    question = next(q for q in plan["questions"] if q["kind"] == "late_days")
    assert question["user_ids"] == ["9001", "9002"]
    assert _late_fields(sent["9001"]) == {"late_policy_status": "none"}
    assert _late_fields(sent["9002"]) == {"late_policy_status": "none"}
    assert _late_fields(sent["9003"]) == {}
    assert result["ok"] is True


def test_post_late_days_answer_keeps_the_applied_payload():
    session = _late_session(late_policy="ask")

    _result, sent, _plan = _apply(session, {"late_days": "post_late_days"},
                                  canvas_read=lambda *_a: ([], None))

    assert _late_fields(sent["9001"]) == {"late_policy_status": "late",
                                          "seconds_late_override": 172800}
    assert _late_fields(sent["9002"]) == {"late_policy_status": "none"}


@pytest.mark.parametrize("policy_course, late_policy, asks, expected_9001", [
    (True, "ask", True, {"late_policy_status": "late", "seconds_late_override": 172800}),
    (True, "apply", False, {"late_policy_status": "late", "seconds_late_override": 172800}),
    (True, "waive", False, {"late_policy_status": "none"}),
    (False, "waive", False, {"late_policy_status": "none"}),
    (False, "apply", False, {"late_policy_status": "late", "seconds_late_override": 172800}),
    (False, "ask", True, {"late_policy_status": "late", "seconds_late_override": 172800}),
])
def test_session_late_policy_settles_the_question_and_the_payload(
        policy_course, late_policy, asks, expected_9001):
    """CONTRACT: the session default decides whether the question is asked and
    which payload posts, across every policy x course combination."""
    session = _late_session(policy_course=policy_course, late_policy=late_policy)

    plan = scoring_apply.build_plan(session)
    asked = any(q["kind"] == "late_days" for q in plan["questions"])
    _result, sent, _plan = _apply(session, {"late_days": "post_late_days"} if asked else {},
                                  canvas_read=lambda *_a: ([], None))

    assert asked is asks
    assert _late_fields(sent["9001"]) == expected_9001


def test_changing_the_session_late_policy_changes_the_plan_digest():
    """LAW: a frozen plan cannot survive a late-policy change (the staged apply
    then refuses stage_changed and the agent restages)."""
    before = scoring_apply.build_plan(_late_session(late_policy="waive"))["digest"]

    assert scoring_apply.build_plan(_late_session(late_policy="apply"))["digest"] != before
    assert scoring_apply.build_plan(_late_session(late_policy="ask"))["digest"] != before


def test_late_decisions_reflect_the_post_answer_decision():
    session = _late_session(late_policy="ask")
    plan = scoring_apply.build_plan(session)

    before = scoring_apply.late_decisions(session, plan)
    after = scoring_apply.late_decisions(session, plan, {"late_days": "waive_late"})

    assert before == {"9001": {"decision": "set", "late_days": 2, "basis": "teacher_set"},
                      "9002": {"decision": "set", "late_days": 0, "basis": "teacher_set"}}
    assert after == {"9001": {"decision": "waived", "late_days": 0, "basis": "teacher_set"},
                     "9002": {"decision": "waived", "late_days": 0, "basis": "teacher_set"}}


@pytest.mark.parametrize(("posted_attempt", "canvas_late", "grade_mode", "expected"), [
    (None, False, "post_score", False), (3, False, "post_score", False),
    (2, False, "post_score", True), (2, True, "post_score", True),
    (2, True, "feedback_only", False),
])
def test_late_box_reset_requires_a_newer_attempt_and_score_payload(
        posted_attempt, canvas_late, grade_mode, expected):
    student = {"user_id": "9001", "ai_score": 8, "ai_feedback": "Feedback.",
        "canvas_late": canvas_late, "posted_attempt": posted_attempt,
        "grading": {"points_possible": 10, "late_days": 2,
                    "suggested_late_days": 2, "floor_percent": None},
        "submission_baseline": {"attempt": 3, "latest_attempt": 3,
            "latest_attempt_at": "2026-09-28T10:00:00Z",
            "first_attempt_at": "2026-09-25T10:00:00Z"}}
    session = _session(students=[student], grade_mode=grade_mode, late_policy="apply")
    plan = scoring_apply.build_plan(session)
    preview = scoring_apply.preview_rows(session, plan, ["9001"])["9001"]
    assert ("late_box_reset" in {warning["code"] for warning in preview["warnings"]}) is expected


def test_preview_attempt_date_uses_classroom_timezone():
    assert scoring_apply._plain_date("2026-09-26T02:00:00Z") == "2026-09-25"


def _posted_correction_session(*, days=0, score=8, feedback="Same feedback"):
    student = {
        "user_id": "9001", "ai_score": score, "ai_feedback": feedback,
        "teacher_score": None, "teacher_feedback": "", "_teacher_authored_feedback": True,
        "posted": True, "status": "posted", "correction_pending": True,
        "canvas_late": True, "grading": {"floor_percent": None, "points_possible": 10,
                                          "late_days": days, "suggested_late_days": 2},
        "submission_baseline": {"attempt": 1, "latest_attempt": 1},
        "last_posted": {"event_id": "prior-verified", "payload_digest": "old-payload",
                        "entered_score": 8, "late_days": 2, "attempt": 1,
                        "feedback_digest": hashlib.sha256(feedback.encode()).hexdigest()},
    }
    return _session(students=[student], late_policy="apply")


def test_days_only_correction_keeps_score_and_omits_duplicate_comment():
    session = _posted_correction_session(days=0)
    plan = scoring_apply.build_plan(session)
    assert plan["candidate_ids"] == ["9001"]
    preview = scoring_apply.preview_rows(session, plan, ["9001"])["9001"]
    assert preview["correction"]["previous"] == {"entered": 8, "late_days": 2, "attempt": 1}
    assert {warning["code"] for warning in preview["warnings"]} >= {
        "correction_of_pushed_row", "correction_comment_unchanged"}
    payload = scoring_apply._projected_payload(session["students"][0], session)
    assert payload["submission"] == {"posted_grade": "8", "late_policy_status": "none"}
    assert "comment" not in payload


@pytest.mark.parametrize(("new_score", "new_feedback", "comment_sent"), [
    (9, "Same feedback", False),
    (8, "Changed feedback", True),
])
def test_score_or_feedback_correction_sends_only_changed_comment(
        new_score, new_feedback, comment_sent):
    session = _posted_correction_session(days=2)
    student = session["students"][0]
    student.update({"ai_score": new_score, "ai_feedback": new_feedback,
                    "correction_pending": True,
                    "grading": {"floor_percent": None, "points_possible": 10,
                                "late_days": 2, "suggested_late_days": 2}})
    result, sent, _plan = _apply(session, {})
    assert result["results"][0]["corrected"] is True
    assert sent["9001"]["submission"]["posted_grade"] == str(new_score)
    assert ("comment" in sent["9001"]) is comment_sent


def test_identical_verified_correction_is_already_pushed_and_does_not_send_again():
    session = _posted_correction_session(days=0)
    first, _sent, _plan = _apply(session, {})
    assert first["results"][0]["corrected"] is True
    student = session["students"][0]
    student.update({"ai_score": 8, "ai_feedback": "Same feedback",
                    "correction_pending": False})
    plan = scoring_apply.build_plan(session)
    assert plan["candidate_ids"] == []


def test_correction_apply_verifies_and_links_new_events_to_prior_verified_event():
    session = _posted_correction_session(days=0)
    result, sent, _plan = _apply(session, {})
    assert sent["9001"] == {"submission": {"posted_grade": "8", "late_policy_status": "none"}}
    assert result["results"][0]["corrected"] is True
    events = score_ledger.list_events("course-1", "assignment-1")
    correction_events = [event for event in events
                         if event.get("source") == "ce_apply"
                         and event.get("corrects_event_id") == "prior-verified"]
    assert {event["action"] for event in correction_events} >= {"intent", "accepted", "verified"}
    assert all(event["corrects_event_id"] == "prior-verified" for event in correction_events)
    verified = next(event for event in correction_events if event["action"] == "verified")
    assert session["students"][0]["last_posted"]["event_id"] == verified["event_id"]
    session["students"][0].pop("last_posted")
    hydrated = session_actions.hydrate_last_posted(session, session["students"][0], events)
    assert hydrated["feedback_digest"] == hashlib.sha256(
        b"Same feedback").hexdigest()
    assert session_actions.correction_changed(session["students"][0], session) is False


@pytest.mark.parametrize("cached", [False, True])
def test_hydration_never_falls_back_when_latest_numeric_push_is_unverified(cached):
    session = _posted_correction_session(days=0)
    student = session["students"][0]
    old_target, latest_target = "old-target", "latest-target"
    session["push_log"] = [{"ts": "2026-01-01T00:00:00Z", "results": [{
        "user_id": "9001", "status": "pushed", "request_digest": "old-request",
        "target_digest": old_target, "entered_score": 8,
    }]}, {"ts": "2026-01-02T00:00:00Z", "results": [{
        "user_id": "9001", "status": "pushed", "request_digest": "new-request",
        "target_digest": latest_target, "entered_score": 9,
    }]}]
    events = [{"source": "ce_apply", "action": "verified", "session_id": "session-1",
        "student_id": "9001", "attempt": 1, "entered_score": 8,
        "logical_event_key": f"verify:session-1:{old_target}:verified",
        "timestamp": "2026-01-01T00:00:01Z", "event_id": "older-verified",
        "feedback_sha256": hashlib.sha256(b"Same feedback").hexdigest()}]
    if cached:
        student["last_posted"] = {"event_id": "older-verified", "entered_score": 8,
                                  "late_days": 2, "attempt": 1,
                                  "feedback_digest": hashlib.sha256(b"Same feedback").hexdigest()}
    assert session_actions.hydrate_last_posted(session, student, events) is None
    assert "last_posted" not in student
    assert session_actions.correction_changed(student, session) is False


def test_correction_to_an_earlier_payload_uses_a_new_ledger_slot():
    session = _posted_correction_session(days=0)
    first, _sent, _plan = _apply(session, {})
    assert first["ok"] is True
    student = session["students"][0]
    student.update({"ai_score": 8, "ai_feedback": "Same feedback",
                    "teacher_score": None, "teacher_feedback": "",
                    "correction_pending": True, "grading": {
                        "floor_percent": None, "points_possible": 10,
                        "late_days": 2, "suggested_late_days": 2}})
    second, sent, _plan = _apply(session, {})
    assert second["ok"] is True
    assert sent["9001"]["submission"] == {"posted_grade": "8", "late_policy_status": "late",
                                           "seconds_late_override": 172800}
    events = score_ledger.list_events("course-1", "assignment-1")
    verified = [event for event in events if event.get("source") == "ce_apply"
                and event.get("action") == "verified"]
    assert len(verified) == 2
    assert len({event["logical_event_key"] for event in verified}) == 2


# ── Late-row read-back (the one read after the write) ───────────────────────


def _reader(rows=None, error=None, calls=None):
    def read(path, params):
        if calls is not None:
            calls.append((path, params))
        return (None, error) if error else (rows, None)
    return read


def test_waived_row_with_a_deduction_is_reported_not_honored_in_one_batched_read():
    """EXAMPLE: one batched read; a waived row Canvas still penalized is flagged
    and the recorded write is untouched."""
    session = _late_session()
    calls = []
    rows = [{"user_id": 9001, "score": 6, "entered_score": 8, "points_deducted": 2,
             "late_policy_status": "none"},
            {"user_id": 9002, "score": 7, "entered_score": 7, "points_deducted": 0,
             "late_policy_status": "none"}]

    result, _sent, _plan = _apply(session, {"late_days": "waive_late"},
                                  canvas_read=_reader(rows, calls=calls))

    assert len(calls) == 1
    path, params = calls[0]
    assert path == "/api/v1/courses/course-1/assignments/assignment-1/submissions"
    assert params["student_ids[]"] == ["9001", "9002"]
    by_user = {r["user_id"]: r for r in result["results"]}
    assert by_user["9001"]["late"]["late_honored"] is False
    assert by_user["9001"]["late"]["points_deducted"] == 2
    assert by_user["9002"]["late"]["verification"] == "verified", by_user["9002"]
    assert all(s["posted"] for s in session["students"])


def test_a_status_that_differs_from_the_one_sent_is_not_honored():
    session = _late_session()
    rows = [{"user_id": 9001, "score": 6, "points_deducted": 2, "late_policy_status": "none"},
            {"user_id": 9002, "score": 6, "points_deducted": 0, "late_policy_status": "late"}]

    result, _sent, _plan = _apply(session, {"late_days": "post_late_days"},
                                  canvas_read=_reader(rows))

    by_user = {r["user_id"]: r for r in result["results"]}
    assert by_user["9001"]["late"]["late_honored"] is False   # sent late, read none
    assert by_user["9002"]["late"]["late_honored"] is False   # sent none, read late


def test_a_failed_read_marks_numeric_scores_unverified():
    session = _late_session()

    result, _sent, _plan = _apply(session, {"late_days": "waive_late"},
                                  canvas_read=_reader(error="HTTP 500: boom"))

    assert result["ok"] is False
    assert result["code"] == "score_readback_unavailable"
    assert all(r["late"]["readback"] == "unavailable" for r in result["results"])
    assert all("late_honored" not in r["late"] for r in result["results"])
    assert all(s["posted"] for s in session["students"])


def test_a_raising_reader_is_an_unavailable_score_verification():
    session = _late_session()

    def read(path, params):
        raise RuntimeError("network down")

    result, _sent, _plan = _apply(session, {"late_days": "waive_late"}, canvas_read=read)

    assert result["ok"] is False
    assert result["code"] == "score_readback_unavailable"
    assert all(r["late"]["readback"] == "unavailable" for r in result["results"])


@pytest.mark.parametrize("session_factory", [
    lambda: _session(),                                          # nothing late
    lambda: _late_session(policy_course=False),                  # decision: canvas
])
def test_every_numeric_write_is_read_even_without_an_explicit_late_decision(session_factory):
    """LAW: score verification covers ordinary and Canvas-owned late rows."""
    result, _sent, _plan = _apply(session_factory(), {})

    assert result["ok"] is True


@pytest.mark.parametrize("late_policy", ["ask", "waive", "apply"])
def test_feedback_only_mode_has_no_late_decision(late_policy):
    """LAW: feedback_only sends no submission object, so a late row gets no late
    question, no per-row late block, no read-back, and ``late_policy`` is ignored."""
    session = _late_session(late_policy=late_policy)
    session["grade_mode"] = "feedback_only"
    calls = []

    result, sent, plan = _apply(session, {}, canvas_read=_reader([], calls=calls))

    assert plan["grade_mode"] == "feedback_only"
    assert not [q for q in plan["questions"] if q["kind"] == "late_days"]
    assert scoring_apply.late_decisions(session, plan) == {}
    assert result["ok"] is True
    assert set(sent) == {"9001", "9002"}
    assert all("submission" not in payload for payload in sent.values())
    assert all("late" not in row for row in result["results"])
    assert calls == []
