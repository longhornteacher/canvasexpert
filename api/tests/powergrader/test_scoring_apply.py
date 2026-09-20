"""Chat-side scoring apply: the plan, its questions, and the narrow write.

Canvas is faked at the ``canvas_send`` injection point these functions already
take. No live scoring run, no real student data.

The write is deliberately narrow: one send, no read-back. These tests pin that
as a law, not an implementation detail.
"""
import json

import pytest

from api.powergrader import scoring_apply, session_actions

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
        load_session=load, save_session=save, canvas_send=canvas_send)

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
        sent.append(path)
        return ({"id": 1}, None)

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"],
        answers={"score_above_possible": "skip_those"},
        load_session=load, save_session=save, canvas_send=canvas_send)

    assert result["ok"] is True
    assert len(sent) == 1 and "9002" in sent[0]


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


# ── The narrow write: no read-back ──────────────────────────────────────────

def test_successful_send_performs_no_canvas_read():
    """LAW: after a successful ordinary scoring PUT, CE performs no Canvas GET,
    mirror refresh, final-grade comparison, or policy inspection."""
    session = _session()
    load, save, _saved = _store(session)

    def canvas_send(method, path, payload, timeout=30):
        return ({"id": 1}, None)

    plan = scoring_apply.build_plan(session)
    result, _status = scoring_apply.apply_plan(
        "session-1", expected_digest=plan["digest"], answers={},
        load_session=load, save_session=save, canvas_send=canvas_send)

    assert result["ok"] is True
    # The accepted-write receipt carries transport facts only.
    receipt = session["push_log"][-1]["results"][0]
    assert set(receipt) == {"user_id", "status", "code",
                            "request_digest", "target_digest"}
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
