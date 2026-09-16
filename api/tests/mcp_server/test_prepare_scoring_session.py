"""MCP preparation and direct session-resolution examples."""
from __future__ import annotations

from contextlib import nullcontext

from api.mcp_server import tools
from api.powergrader import session_store


def test_prepare_requires_exact_current_scope(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])
    result = tools.prepare_scoring_session("c1", "")
    assert result["code"] == "invalid_scope"
    assert result["stage"] == "validate"
    assert result["retryable"] is False


def test_prepare_calls_one_full_refresh_and_returns_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])

    def prepare(course_id, assignment_id, guidance, *, refresh_course, save_session=session_store.save_session):
        calls.append((course_id, assignment_id, guidance))
        assert refresh_course(course_id) is True
        return {"ok": True, "status": "ready", "scoring_session_id": "s1"}

    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session", prepare)
    monkeypatch.setattr(tools, "_refresh_course_for_scoring", lambda course_id: calls.append(("refresh", course_id)) or True)
    result = tools.prepare_scoring_session("c1", "a1", "Writing")
    assert result["status"] == "ready"
    assert calls == [("c1", "a1", "Writing"), ("refresh", "c1")]


def test_prepare_owner_exception_returns_safe_typed_failure(monkeypatch):
    private_text = "private student identity should never escape"
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])

    def prepare(*_args, **_kwargs):
        raise RuntimeError(private_text)

    monkeypatch.setattr("api.powergrader.scoring_preparation.prepare_scoring_session", prepare)
    result = tools.prepare_scoring_session("c1", "a1")

    assert result["ok"] is False
    assert result["code"] == "safe_preparation_failed"
    assert result["stage"] == "prepare"
    assert result["retryable"] is True
    assert result["user_action"] == (
        "The SAFE scoring packet could not be prepared. Retry this exact assignment."
    )
    assert private_text not in str(result)
    assert "RuntimeError" not in str(result)


def test_list_ignores_unsupported_records_and_lists_assignment_sessions(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1"}])
    sessions = {
        "root": {"session_id": "root", "session_kind": "scoring_session", "course_id": "c1"},
        "legacy": {"session_id": "legacy", "session_kind": "legacy_root", "course_id": "c1"},
        "s1": {"session_id": "s1", "session_kind": "scoring_assignment", "course_id": "c1",
               "assignment_name": "Essay", "created": "2026-01-01", "status": "ready",
               "students": [{"status": "approved", "posted": True}]},
    }
    monkeypatch.setattr(session_store, "list_session_summaries", lambda: [
        {"session_id": sid, "session_kind": value.get("session_kind"), "course_id": value.get("course_id"),
         "assignment_name": value.get("assignment_name"), "created": value.get("created"),
         "status": value.get("status"), "total": len(value.get("students") or []),
         "approved": 1, "posted": 1} for sid, value in sessions.items()
    ])
    monkeypatch.setattr(session_store, "load_session", lambda sid: sessions.get(sid))
    result = tools.list_scoring_sessions()
    assert [row[0] for row in result["sessions"]["rows"]] == ["s1"]
    assert "queue" not in str(result).lower()


def test_packet_rejects_historical_root_record_directly(monkeypatch):
    monkeypatch.setattr(session_store, "load_session", lambda _sid: {
        "session_id": "root", "session_kind": "scoring_session",
    })
    assert tools.get_scoring_packet("root")["code"] == "session_not_found"
