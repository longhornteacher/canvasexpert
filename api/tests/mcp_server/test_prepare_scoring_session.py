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
