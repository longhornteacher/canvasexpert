"""Root queue laws: frozen scope, active-child binding, and progress."""
from __future__ import annotations

import copy
from contextlib import nullcontext

from api.powergrader import scoring_queue, session_store


def _memory_store(monkeypatch):
    sessions = {}
    monkeypatch.setattr(session_store, "load_session",
                        lambda session_id: copy.deepcopy(sessions.get(session_id)))
    monkeypatch.setattr(session_store, "save_session",
                        lambda session: sessions.__setitem__(session["session_id"], copy.deepcopy(session)))
    monkeypatch.setattr(session_store, "session_lock", lambda _session_id: nullcontext())
    monkeypatch.setattr(session_store, "list_session_summaries", lambda: [
        {"session_id": session["session_id"], "session_kind": session.get("session_kind", "")}
        for session in sessions.values()
    ])
    return sessions


def _queue_item(course_id, assignment_id):
    return {"course_id": course_id, "course_label": course_id,
            "assignment_id": assignment_id, "assignment_label": assignment_id,
            "due_at": "", "ungraded": 1, "partially_scored": 0}


def _child(root_id, session_id, course_id, assignment_id):
    return {
        "session_id": session_id, "session_kind": scoring_queue.CHILD_KIND,
        "parent_scoring_session_id": root_id,
        "course_id": course_id, "assignment_id": assignment_id,
        "scoring_basis": {"source": "local_rubric", "label": "Writing"},
    }


def test_queue_digest_rejects_scope_widening_and_old_records_are_ignored(monkeypatch):
    sessions = _memory_store(monkeypatch)
    root = scoring_queue.create_root_session(
        queue=[_queue_item("c1", "a1")], scope={}, session_id="root-1")

    assert scoring_queue.load_root_session("root-1") is not None
    tampered = copy.deepcopy(root)
    tampered["queue"].append(_queue_item("c2", "a2"))
    session_store.save_session(tampered)
    assert scoring_queue.load_root_session("root-1") is None

    session_store.save_session({"session_id": "old-run", "course_id": "c1"})
    assert scoring_queue.load_root_session("old-run") is None
    assert [row["session_id"] for row in scoring_queue.root_summaries()] == ["root-1"]
    assert all(row["session_id"] != "old-run" for row in scoring_queue.root_summaries())


def test_only_claimed_active_child_resolves_and_terminal_submit_advances(monkeypatch):
    sessions = _memory_store(monkeypatch)
    root_id = "root-2"
    scoring_queue.create_root_session(
        queue=[_queue_item("c1", "a1"), _queue_item("c2", "a2")],
        scope={}, session_id=root_id)
    claim = scoring_queue.claim_active_item(root_id)
    assert claim["kind"] == "claimed"
    session_store.save_session(_child(root_id, "child-1", "c1", "a1"))
    assert scoring_queue.attach_child(root_id, claim["index"], claim["claim"], "child-1")

    resolved = scoring_queue.resolve_active_child(root_id)
    assert resolved["ok"] is True
    assert resolved["child"]["session_id"] == "child-1"
    assert scoring_queue.resolve_active_child("child-1")["ok"] is False
    assert scoring_queue.record_submit_result(root_id, "other-child", {"ok": True})["ok"] is False

    recorded = scoring_queue.record_submit_result(root_id, "child-1", {
        "ok": True, "counts": {"finalized": 1, "held": 0, "failed": 0},
    })
    assert recorded["ok"] is True
    assert recorded["progress"]["completed"] == 1
    next_claim = scoring_queue.claim_active_item(root_id)
    assert next_claim["kind"] == "claimed"
    assert next_claim["index"] == 1
    assert next_claim["item"]["assignment_id"] == "a2"


def test_needs_teacher_input_and_write_failure_do_not_advance(monkeypatch):
    _memory_store(monkeypatch)
    root_id = "root-3"
    scoring_queue.create_root_session(
        queue=[_queue_item("c1", "a1"), _queue_item("c1", "a2")],
        scope={}, session_id=root_id)
    claim = scoring_queue.claim_active_item(root_id)
    session_store.save_session(_child(root_id, "child-1", "c1", "a1"))
    scoring_queue.attach_child(root_id, claim["index"], claim["claim"], "child-1")

    waiting = scoring_queue.record_submit_result(root_id, "child-1", {
        "ok": True, "status": "needs_teacher_input",
    })
    assert waiting["status"] == "needs_teacher_input"
    assert scoring_queue.claim_active_item(root_id)["kind"] == "ready"

    failed = scoring_queue.record_submit_result(root_id, "child-1", {
        "ok": False, "code": "canvas_preflight_failed",
    })
    assert failed["progress"]["remaining"] == 2
    assert failed["progress"]["failed"] == 1
    root = scoring_queue.load_root_session(root_id)
    assert root["active_index"] == 0
    assert root["queue"][0]["status"] == "ready"


def test_deterministic_preparation_failure_stays_blocked_after_reload(monkeypatch):
    _memory_store(monkeypatch)
    root_id = "root-blocked"
    scoring_queue.create_root_session(
        queue=[_queue_item("c1", "a1"), _queue_item("c1", "a2")],
        scope={}, session_id=root_id)
    claim = scoring_queue.claim_active_item(root_id)
    assert scoring_queue.record_preparation_failure(
        root_id, claim["index"], claim["claim"],
        "mirror_submission_identity_mismatch", retryable=False,
    )

    blocked = scoring_queue.claim_active_item(root_id)
    assert blocked["kind"] == "blocked"
    assert blocked["item"]["preparation_retryable"] is False
    assert blocked["item"]["last_failure"] == "mirror_submission_identity_mismatch"
    assert scoring_queue.load_root_session(root_id)["active_index"] == 0
