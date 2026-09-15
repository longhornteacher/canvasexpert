"""Backlog-wide Scoring Session MCP start and continuation behavior."""
from __future__ import annotations

import copy
import json
from contextlib import nullcontext

from api.mcp_server import tools


def _bind_courses(monkeypatch, courses):
    monkeypatch.setattr(tools.config, "active_courses", lambda: copy.deepcopy(courses))


def _bind_snapshots(monkeypatch, snapshots):
    calls = []

    def load(course_id):
        calls.append(course_id)
        return snapshots.get(course_id, (None, "stale"))

    monkeypatch.setattr(tools, "_load_snapshot", load)
    return calls


def _bind_session_store(monkeypatch):
    from api.powergrader import session_store

    sessions = {}
    monkeypatch.setattr(session_store, "load_session",
                        lambda session_id: copy.deepcopy(sessions.get(session_id)))
    monkeypatch.setattr(session_store, "save_session",
                        lambda session: sessions.__setitem__(session["session_id"], copy.deepcopy(session)))
    monkeypatch.setattr(session_store, "list_session_summaries", lambda: [
        {"session_id": session["session_id"], "session_kind": session.get("session_kind", ""),
         "created": session.get("created", "")}
        for session in sessions.values()
    ])
    monkeypatch.setattr(session_store, "session_lock", lambda _session_id: nullcontext())
    return sessions


def _snapshot(*assignments):
    return {"assignments": list(assignments), "source": "mirror"}


def _assignment(assignment_id, name, ungraded, partially_scored=0, due_at=""):
    return {"id": assignment_id, "name": name, "ungraded": ungraded,
            "partially_scored": partially_scored, "due_at": due_at}


def test_unscoped_start_freezes_ordered_queue_from_every_current_snapshot(monkeypatch):
    _bind_courses(monkeypatch, [
        {"id": "c1", "name": "Course One"},
        {"id": "c2", "name": "Course Two"},
    ])
    calls = _bind_snapshots(monkeypatch, {
        "c1": (_snapshot(_assignment("a2", "Second", 2, 1, "2026-10-01"),
                         _assignment("a1", "First", 1)), None),
        "c2": (_snapshot(_assignment("b1", "Third", 3)), None),
    })
    sessions = _bind_session_store(monkeypatch)

    result = tools.start_scoring_session()

    assert result["ok"] is True and result["status"] == "started"
    assert calls == ["c1", "c2"]
    [root] = [session for session in sessions.values()
              if session.get("session_kind") == "scoring_session"]
    assert [(item["course_id"], item["assignment_id"]) for item in root["queue"]] == [
        ("c1", "a2"), ("c1", "a1"), ("c2", "b1"),
    ]
    assert root["queue"][0]["partially_scored"] == 1
    assert root["queue"][0]["due_at"] == "2026-10-01"


def test_scoped_start_includes_only_the_exact_assignment(monkeypatch):
    _bind_courses(monkeypatch, [{"id": "c1", "name": "Course One"}])
    calls = _bind_snapshots(monkeypatch, {
        "c1": (_snapshot(_assignment("a1", "First", 1),
                         _assignment("a2", "Second", 2)), None),
    })
    sessions = _bind_session_store(monkeypatch)

    result = tools.start_scoring_session("c1", "a2")

    assert result["status"] == "started"
    assert calls == ["c1"]
    [root] = [session for session in sessions.values() if session.get("session_kind")]
    assert [item["assignment_id"] for item in root["queue"]] == ["a2"]


def test_assignment_filter_without_course_is_refused_without_saving(monkeypatch):
    sessions = _bind_session_store(monkeypatch)

    result = tools.start_scoring_session(assignment_id="a1")

    assert result["ok"] is False
    assert result["code"] == "invalid_scope"
    assert sessions == {}


def test_stale_course_blocks_queue_creation_for_entire_scope(monkeypatch):
    _bind_courses(monkeypatch, [{"id": "c1"}, {"id": "c2"}])
    calls = _bind_snapshots(monkeypatch, {
        "c1": (_snapshot(_assignment("a1", "First", 1)), None),
        "c2": (None, "stale"),
    })
    sessions = _bind_session_store(monkeypatch)

    result = tools.start_scoring_session()

    assert result["status"] == "needs_refresh"
    assert result["course_ids"] == ["c2"]
    assert calls == ["c1", "c2"]
    assert sessions == {}


def test_empty_snapshot_creates_no_session(monkeypatch):
    _bind_courses(monkeypatch, [{"id": "c1"}])
    _bind_snapshots(monkeypatch, {"c1": (_snapshot(_assignment("a1", "Done", 0)), None)})
    sessions = _bind_session_store(monkeypatch)

    result = tools.start_scoring_session("c1")

    assert result["status"] == "nothing_to_grade"
    assert result["counts"]["total"] == 0
    assert sessions == {}


def test_continue_pauses_for_norms_then_resumes_same_root_idempotently(
    monkeypatch, tmp_path,
):
    from api.powergrader import session_store

    _bind_courses(monkeypatch, [{"id": "c1", "name": "Course One"}])
    _bind_snapshots(monkeypatch, {"c1": (_snapshot(_assignment("a1", "Essay", 1)), None)})
    sessions = _bind_session_store(monkeypatch)
    started = tools.start_scoring_session("c1")
    root_id = started["scoring_session_id"]
    bundle_path = tmp_path / "safe.json"
    bundle_path.write_text(json.dumps({"contract_version": "1.0", "students": []}), encoding="utf-8")
    attempts = []

    def run_start(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            return {"ok": False, "payload": {
                "code": "needs_scoring_norms", "rubric_labels": ["Writing"],
            }}
        child_id = "child-1"
        kwargs["save_session"]({
            "session_id": child_id, "session_kind": "assignment_run",
            "parent_scoring_session_id": root_id, "course_id": "c1",
            "assignment_id": "a1", "assignment_name": "Essay", "mode": "packet",
            "scoring_basis": {"source": "local_rubric", "label": "Writing"},
            "students": [], "privacy_artifacts": {"safe_bundle": str(bundle_path)},
        })
        return {"ok": True, "session_id": child_id, "payload": {"ok": True}}

    monkeypatch.setattr("api.powergrader.start_workflow.run_start_session", run_start)

    paused = tools.continue_scoring_session(root_id)
    root_after_pause = sessions[root_id]
    resumed = tools.continue_scoring_session(root_id, rubric_name="Writing")
    repeated = tools.continue_scoring_session(root_id)

    assert paused["status"] == "needs_teacher_input"
    assert paused["scoring_session_id"] == root_id
    assert root_after_pause["queue"][0]["status"] == "needs_teacher_input"
    assert resumed["status"] == "ready"
    assert resumed["scoring_session_id"] == root_id
    assert repeated["status"] == "ready"
    assert len(attempts) == 2
    assert attempts[1]["parent_scoring_session_id"] == root_id
    assert attempts[1]["scoring_guidance"] == ""
    assert sessions[root_id]["queue"][0]["child_session_id"] == "child-1"


def test_continue_forwards_teacher_guidance_unchanged(monkeypatch):
    _bind_courses(monkeypatch, [{"id": "c1", "name": "Course One"}])
    _bind_snapshots(monkeypatch, {"c1": (_snapshot(_assignment("a1", "Essay", 1)), None)})
    sessions = _bind_session_store(monkeypatch)
    root_id = tools.start_scoring_session("c1")["scoring_session_id"]
    captured = []

    def run_start(**kwargs):
        captured.append(kwargs)
        return {"ok": False, "payload": {
            "code": "needs_scoring_norms", "rubric_labels": [],
        }}

    monkeypatch.setattr("api.powergrader.start_workflow.run_start_session", run_start)
    guidance = "Teacher guidance with scoring criteria."
    result = tools.continue_scoring_session(root_id, scoring_guidance=guidance)

    assert result["status"] == "needs_teacher_input"
    assert captured[0]["scoring_guidance"] == guidance
    assert sessions[root_id]["queue"][0]["status"] == "needs_teacher_input"
