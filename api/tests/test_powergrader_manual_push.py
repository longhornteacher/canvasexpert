"""Safety contract tests for PowerGrader's narrow Canvas write.

The write is one send: the reviewed raw score and one plain-text comment go to
the Canvas Submissions endpoint, and Canvas applies every gradebook and
late-policy adjustment from there. These tests pin the absence of read-back as
a law, not an implementation detail.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.powergrader import session_actions


def _session():
    return {
        "session_id": "session-1",
        "course_id": "course-1",
        "assignment_id": "assignment-1",
        "students": [
            {"user_id": "user-1", "teacher_score": 4, "teacher_feedback": "Good.", "status": "approved", "posted": False},
            {"user_id": "user-2", "teacher_score": 0, "teacher_feedback": "", "status": "approved", "posted": False},
            {"user_id": "user-3", "teacher_score": None, "teacher_feedback": "", "status": "pending", "posted": False},
        ],
        "push_log": [],
    }


def test_send_writes_the_raw_score_and_plain_text_comment_once():
    """CONTRACT: a valid result maps to the exact raw posted_grade and comment."""
    session = _session()
    calls = []

    def send(method, path, payload):
        calls.append((method, path, payload))
        return {}, None

    result, code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )

    assert code == 200 and result["ok"] is True
    assert calls == [("PUT", "/api/v1/courses/course-1/assignments/assignment-1/submissions/user-1", {
        "submission": {"posted_grade": "4"},
        "comment": {"text_comment": "Good."},
    })]
    assert session["students"][0]["posted"] is True


def test_no_late_policy_or_gradebook_adjustment_field_is_sent():
    """LAW: without a ``grading`` stamp, CE sends no policy/gradebook
    adjustment field and requests no policy -- byte-identical to a course
    with no grading policy."""
    session = _session()
    sent = []

    def send(method, path, payload):
        sent.append(payload)
        return {}, None

    session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )

    blob = json.dumps(sent)
    for forbidden in ("late_policy_status", "seconds_late_override", "excuse",
                      "points_deducted", "late_policy"):
        assert forbidden not in blob


def test_late_fields_appear_only_with_a_grading_stamp():
    """LAW: the late submission fields are sent only when a ``grading``
    stamp is present (a grading-policy course) and the row is Canvas-late."""
    session = _session()
    session["students"][0]["canvas_late"] = True
    session["students"][0]["grading"] = {
        "floor_percent": 30, "points_possible": 10,
        "insincere": False, "late_days": 1,
        "suggested_late_days": 1, "canvas_late_days": 1,
    }
    session["students"][0]["teacher_score"] = 6
    sent = []

    def send(method, path, payload):
        sent.append(payload)
        return {}, None

    session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )

    assert sent[0]["submission"]["posted_grade"] == "7"
    assert sent[0]["submission"]["late_policy_status"] == "late"
    assert sent[0]["submission"]["seconds_late_override"] == 86400


def test_successful_send_performs_no_canvas_read():
    """LAW: after a successful ordinary scoring PUT, CE performs no Canvas GET,
    mirror refresh, final-grade comparison, or policy inspection."""
    session = _session()
    calls = []

    def send(method, path, payload):
        calls.append(path)
        return {}, None

    result, _code = session_actions.push_grades(
        "session-1", user_ids='["user-1", "user-2"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )

    assert result["ok"] is True
    assert len(calls) == 2
    # The accepted-write receipt carries transport facts only.
    receipt = session["push_log"][-1]["results"][0]
    assert set(receipt) == {"user_id", "status", "code",
                            "request_digest", "target_digest"}


def test_transport_error_is_unknown_without_idempotency_or_repeat():
    """LAW: a transport-unknown send never triggers a second PUT, automatic
    re-verification, or an exposed Canvas grade result."""
    session = _session()
    calls = []

    def send(method, path, payload):
        calls.append(path)
        return None, "connection lost"

    result, code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )

    assert code == 200 and result["ok"] is False
    assert result["code"] == "write_transport_unknown"
    assert result["results"][0]["status"] == "transport_unknown"
    assert session["students"][0]["push_state"] == "sent_unknown"
    assert not session.get("push_idempotency")

    before_retry = len(calls)
    retry, _code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )
    assert retry["code"] == "payload_changed"
    assert len(calls) == before_retry, "transport-unknown must not repeat the PUT"


def test_explicit_http_rejection_is_distinct_from_transport_uncertainty():
    session = _session()

    result, _code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=lambda *_a: (None, "HTTP 400: rejected"),
    )

    assert result["ok"] is False
    assert result["code"] == "canvas_rejected"
    assert result["results"][0]["status"] == "failed"
    assert session["students"][0].get("push_state") is None


def test_partial_success_updates_only_confirmed_rows_and_repeat_is_idempotent():
    session = _session()
    calls = []

    def send(method, path, payload):
        calls.append((method, path, payload))
        return ({}, None) if path.endswith("user-1") else ({}, "HTTP 400: rejected")

    result, code = session_actions.push_grades(
        "session-1", user_ids='["user-1", "user-2"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )
    assert code == 200 and result["ok"] is False
    assert session["students"][0]["posted"] is True
    assert session["students"][1]["posted"] is False
    assert len(calls) == 2

    # Re-approve the accepted row and resubmit the identical payload.
    session["students"][0]["posted"] = False
    session["students"][0]["status"] = "approved"
    again, code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )
    assert code == 200 and again["results"][0]["status"] == "already_applied"
    assert len(calls) == 2, "an accepted payload was sent twice"


def test_stored_ai_banner_is_removed_from_the_sent_payload():
    session = _session()
    session["students"][0]["teacher_feedback"] = (
        "---------- AI draft ----------\n"
        "AI score: 4 / 10\n"
        "Good."
    )
    calls = []

    def send(method, path, payload):
        calls.append((method, path, payload))
        return {}, None

    result, code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]',
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send,
    )
    assert code == 200 and result["ok"] is True
    assert calls[0][2] == {
        "submission": {"posted_grade": "4"},
        "comment": {"text_comment": "Good."},
    }


def test_grade_mutation_saves_the_teacher_edit():
    session = _session()
    saved = []
    result, _ = session_actions.save_grade(
        "session-1", user_id="user-1", teacher_score="5", teacher_feedback="Updated",
        status="approved", load_session=lambda _: session,
        save_session=lambda value: saved.append(copy.deepcopy(value)),
    )
    assert result["ok"] is True
    assert saved[0]["students"][0]["teacher_score"] == 5.0
    assert saved[0]["students"][0]["teacher_feedback"] == "Updated"
