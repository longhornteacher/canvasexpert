"""MCP preparation and local session-resolution contracts."""
from __future__ import annotations

import pytest

from api.mcp_server import tools
from api.powergrader import session_store


def test_prepare_requires_exact_current_scope(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])
    result = tools.prepare_scoring_session("c1", "")
    assert result["code"] == "invalid_scope"
    assert result["stage"] == "validate"


def test_prepare_passes_local_first_options_without_refresh_callback(monkeypatch):
    calls = []
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])

    def prepare(course_id, assignment_id, guidance, *, use_existing_mirror=False):
        calls.append((course_id, assignment_id, guidance, use_existing_mirror))
        return {"ok": True, "status": "ready", "scoring_session_id": "s1"}

    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session", prepare)
    result = tools.prepare_scoring_session("c1", "a1", "Writing", use_existing_mirror=True)
    assert result["status"] == "ready"
    assert calls == [("c1", "a1", "Writing", True)]


def test_prepare_refuses_duplicate_and_points_to_local_packet(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    session = {"session_id": "s1", "course_id": "c1", "assignment_id": "a1",
               "session_kind": "scoring_assignment", "status": "staged"}
    monkeypatch.setattr(session_store, "current_actionable_session", lambda *_args: session)
    monkeypatch.setattr(session_store, "packet_health", lambda _session: {"ok": True})
    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session",
                        lambda *_args, **_kwargs: pytest.fail("duplicate preparation ran"))
    result = tools.prepare_scoring_session("c1", "a1")
    assert result["code"] == "scoring_session_already_open"
    assert "stage once" in result["user_action"]
    assert "prepare this assignment again" in result["user_action"]


def test_prepare_owner_exception_returns_safe_typed_failure(monkeypatch):
    private_text = "private student identity should never escape"
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(private_text)))
    result = tools.prepare_scoring_session("c1", "a1")
    assert result["code"] == "safe_preparation_failed"
    assert private_text not in str(result)


def test_list_exposes_staged_assignment_sessions_only(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    monkeypatch.setattr(session_store, "current_actionable_sessions", lambda **_kwargs: [{
        "session_id": "s1", "created": "2026-01-01", "status": "staged",
        "assignment_name": "Essay", "total": 1, "approved": 1, "posted": 0}])
    result = tools.list_scoring_sessions()
    assert result["sessions"]["rows"][0][0] == "s1"
    assert result["sessions"]["rows"][0][2] == "staged"


def test_non_current_duplicate_is_refused_before_stage_validation(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    session = {"session_id": "old", "session_kind": "scoring_assignment", "course_id": "c1",
               "assignment_id": "a1", "status": "superseded"}
    monkeypatch.setattr(session_store, "load_session", lambda _sid: session)
    monkeypatch.setattr(session_store, "is_current_session", lambda _sid: False)
    result = tools.stage_scoring_results("old", [], "digest")
    assert result["code"] == "session_superseded"


def test_packet_accepts_staged_status_but_rejects_historical_root(monkeypatch):
    monkeypatch.setattr(session_store, "load_session", lambda _sid: {
        "session_id": "root", "session_kind": "scoring_session"})
    assert tools.get_scoring_packet("root")["code"] == "session_not_found"


def test_prepare_refuses_an_unknown_late_policy_naming_the_values(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session",
                        lambda *_args, **_kwargs: pytest.fail("preparation ran"))

    result = tools.prepare_scoring_session("c1", "a1", late_policy="forgive")

    assert result["code"] == "invalid_late_policy"
    assert all(value in result["error"] for value in ("ask", "waive", "apply"))


@pytest.mark.parametrize("policy", ["waive", "apply"])
def test_prepare_hands_a_non_default_late_policy_to_preparation(monkeypatch, policy):
    seen = {}
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])

    def prepare(*_args, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "status": "ready", "scoring_session_id": "s1"}

    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session", prepare)

    tools.prepare_scoring_session("c1", "a1", "Writing", late_policy=policy)

    assert seen["late_policy"] == policy


def test_already_open_session_takes_the_new_late_policy_and_reports_it(monkeypatch):
    """CONTRACT: a differing late_policy is saved onto the open session, a local
    preference rather than packet content, and the refusal reports the current one."""
    saved = {}
    session = {"session_id": "s1", "course_id": "c1", "assignment_id": "a1",
               "session_kind": "scoring_assignment", "status": "staged", "late_policy": "ask"}
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    monkeypatch.setattr(session_store, "current_actionable_session", lambda *_args: session)
    monkeypatch.setattr(session_store, "packet_health", lambda _session: {"ok": True})
    monkeypatch.setattr(session_store, "load_session", lambda _sid: dict(session))
    monkeypatch.setattr(session_store, "save_session", lambda value: saved.update(value))
    monkeypatch.setattr(session_store, "session_lock",
                        lambda _sid: __import__("contextlib").nullcontext())
    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session",
                        lambda *_args, **_kwargs: pytest.fail("duplicate preparation ran"))

    result = tools.prepare_scoring_session("c1", "a1", late_policy="waive")

    assert result["code"] == "scoring_session_already_open"
    assert result["late_policy"] == "waive"
    assert saved["late_policy"] == "waive"
    # Supplying the value the session already has changes nothing and saves nothing.
    saved.clear()
    session["late_policy"] = "waive"
    assert tools.prepare_scoring_session("c1", "a1", late_policy="waive")["late_policy"] == "waive"
    assert saved == {}


def test_reprepare_without_late_policy_keeps_the_saved_one(monkeypatch):
    """LAW: omitting late_policy never resets a saved waive/apply."""
    saved = {}
    session = {"session_id": "s1", "course_id": "c1", "assignment_id": "a1",
               "session_kind": "scoring_assignment", "status": "staged", "late_policy": "waive"}
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    monkeypatch.setattr(session_store, "current_actionable_session", lambda *_args: session)
    monkeypatch.setattr(session_store, "packet_health", lambda _session: {"ok": True})
    monkeypatch.setattr(session_store, "load_session", lambda _sid: dict(session))
    monkeypatch.setattr(session_store, "save_session", lambda value: saved.update(value))
    monkeypatch.setattr(session_store, "session_lock",
                        lambda _sid: __import__("contextlib").nullcontext())

    result = tools.prepare_scoring_session("c1", "a1")

    assert result["code"] == "scoring_session_already_open"
    assert result["late_policy"] == "waive"
    assert saved == {}
    # Only an explicit value changes it, including an explicit ask.
    assert tools.prepare_scoring_session("c1", "a1", late_policy="ask")["late_policy"] == "ask"
    assert saved["late_policy"] == "ask"
