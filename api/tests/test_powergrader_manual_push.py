"""Safety contract tests for PowerGrader's frozen review/apply flow."""

import copy
import json
import sys
from pathlib import Path

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


def canvas_get(path, params=None, timeout=0):
    return ({
        "submission": {"score": 2, "grade": "2", "graded_at": None, "updated_at": "2026-01-01T00:00:00Z"},
        "submission_comments": [],
    }, None)


def test_review_selects_only_approved_rows_and_persists_frozen_state():
    session = _session()
    saved = []
    result, status = session_actions.review_push(
        "session-1", user_ids="", load_session=lambda _: session,
        save_session=lambda value: saved.append(value), canvas_get=canvas_get,
    )
    assert status == 200 and result["ok"] is True
    assert result["user_ids"] == ["user-1", "user-2"]
    assert "real_name" not in json.dumps(result)
    pending = session["pending_push_review"]
    assert pending["user_ids"] == result["user_ids"]
    assert pending["token"] == result["review_token"]
    assert len(pending["overall_digest"]) == 64
    assert saved


def test_direct_apply_and_mismatched_review_are_rejected_without_put():
    session = _session()
    sent = []
    send = lambda *args: sent.append(args) or ({}, None)
    direct, code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]', review_token="", load_session=lambda _: session,
        save_session=lambda _: None, canvas_send=send, canvas_get=canvas_get,
    )
    assert code == 409 and direct["code"] == "review_required" and sent == []
    review, _ = session_actions.review_push(
        "session-1", user_ids='["user-1"]', load_session=lambda _: session,
        save_session=lambda _: None, canvas_get=canvas_get,
    )
    mismatch, code = session_actions.push_grades(
        "session-1", user_ids='["user-2"]', review_token=review["review_token"],
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send, canvas_get=canvas_get,
    )
    assert code == 409 and mismatch["code"] == "review_mismatch" and sent == []


def test_score_drift_blocks_before_any_put():
    session = _session()
    review, _ = session_actions.review_push(
        "session-1", user_ids='["user-1"]', load_session=lambda _: session,
        save_session=lambda _: None, canvas_get=canvas_get,
    )
    def drifted(*args, **kwargs):
        data, _ = canvas_get(*args, **kwargs)
        data["submission"]["updated_at"] = "2026-01-02T00:00:00Z"
        return data, None
    sent = []
    result, code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]', review_token=review["review_token"],
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=lambda *args: sent.append(args) or ({}, None), canvas_get=drifted,
    )
    assert code == 409 and result["code"] == "drift_detected" and sent == []


def test_partial_success_updates_only_confirmed_rows_and_repeat_is_idempotent():
    session = _session()
    review, _ = session_actions.review_push(
        "session-1", user_ids='["user-1", "user-2"]', load_session=lambda _: session,
        save_session=lambda _: None, canvas_get=canvas_get,
    )
    calls = []
    def send(method, path, payload):
        calls.append((method, path, payload))
        return ({}, None) if path.endswith("user-1") else ({}, "rejected")
    result, code = session_actions.push_grades(
        "session-1", user_ids=json.dumps(review["user_ids"]), review_token=review["review_token"],
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send, canvas_get=canvas_get,
    )
    assert code == 200 and result["ok"] is False
    assert session["students"][0]["posted"] is True
    assert session["students"][1]["posted"] is False
    assert len(calls) == 2
    again, code = session_actions.push_grades(
        "session-1", user_ids=json.dumps(review["user_ids"]), review_token=review["review_token"],
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=send, canvas_get=canvas_get,
    )
    assert code == 200 and again["results"][0]["status"] == "already_applied"
    assert len(calls) == 3


def test_stored_ai_banner_is_removed_from_review_apply_payload():
    session = _session()
    session["students"][0]["teacher_feedback"] = (
        "---------- AI draft ----------\n"
        "AI score: 4 / 10\n"
        "Good."
    )
    review, _ = session_actions.review_push(
        "session-1", user_ids='["user-1"]', load_session=lambda _: session,
        save_session=lambda _: None, canvas_get=canvas_get,
    )
    calls = []
    result, code = session_actions.push_grades(
        "session-1", user_ids='["user-1"]', review_token=review["review_token"],
        load_session=lambda _: session, save_session=lambda _: None,
        canvas_send=lambda method, path, payload: calls.append((method, path, payload)) or ({}, None),
        canvas_get=canvas_get,
    )
    assert code == 200 and result["ok"] is True
    assert calls[0][2] == {
        "submission": {"posted_grade": "4"},
        "comment": {"text_comment": "Good."},
    }


def test_grade_mutation_invalidates_review():
    session = _session()
    session["pending_push_review"] = {"token": "opaque"}
    saved = []
    result, _ = session_actions.save_grade(
        "session-1", user_id="user-1", teacher_score="5", teacher_feedback="Updated",
        status="approved", load_session=lambda _: session,
        save_session=lambda value: saved.append(copy.deepcopy(value)),
    )
    assert result["ok"] is True and "pending_push_review" not in saved[0]
