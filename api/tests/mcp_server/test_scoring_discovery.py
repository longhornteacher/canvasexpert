"""MCP boundary contracts for cross-course discovery."""
from __future__ import annotations

import json

from api.mcp_server import server, tools
from api.powergrader import session_store


def test_discover_scoring_work_uses_injected_service_and_next_advisory(monkeypatch):
    calls = []
    monkeypatch.setattr(tools.config, "active_courses", lambda: [
        {"id": "c1", "name": "Course"},
    ])
    monkeypatch.setattr(session_store, "current_actionable_sessions", lambda **_kwargs: [])
    monkeypatch.setattr(tools.scoring_discovery, "discover_scoring_work",
                        lambda courses, **kwargs: calls.append((courses, kwargs)) or {
                            "ok": True, "status": "nothing_to_grade",
                            "assignments": {"columns": [], "rows": []},
                            "totals": {"courses_checked": 1, "courses_usable": 1,
                                       "assignments": 0, "ungraded": 0,
                                       "partially_scored": 0, "late_ungraded": 0},
                            "attention": {"columns": [], "rows": []},
                        })

    result = tools.discover_scoring_work()
    assert result["status"] == "nothing_to_grade"
    assert "wait for teacher direction" in result["next"]
    assert calls and calls[0][0][0]["id"] == "c1"


def test_discovery_response_is_student_free_and_wrapper_is_text_only(monkeypatch):
    monkeypatch.setattr(tools, "discover_scoring_work", lambda: {
        "ok": True, "status": "nothing_to_grade",
        "assignments": {"columns": ["course_id"], "rows": []},
        "totals": {"courses_checked": 1, "courses_usable": 1, "assignments": 0,
                   "ungraded": 0, "partially_scored": 0, "late_ungraded": 0},
        "attention": {"columns": [], "rows": []},
        "next": "Report all rows and wait for teacher direction.",
    })
    wire = server._compact(tools.discover_scoring_work())
    payload = json.loads(wire)
    assert payload["ok"] is True
    assert "students" not in wire.casefold()
    assert "pseudonym" not in wire.casefold()


def test_discovery_tool_has_no_required_arguments_and_is_grouped_once():
    tool = server.mcp._tool_manager._tools["discover_scoring_work"]
    assert (tool.parameters.get("required") or []) == []
    grouped = [name for names in tools._TOOL_GROUPS.values() for name in names]
    assert grouped.count("discover_scoring_work") == 1


def test_refreshing_discovery_gets_bounded_automatic_continuation_advisory():
    result = tools._with_next("discover_scoring_work", {
        "ok": True,
        "status": "refreshing",
        "attention": {"columns": [], "rows": []},
    })

    assert "at most four total calls" in result["next"]
    assert "initial call plus three continuations" in result["next"]
    assert "without asking the teacher" in result["next"]
    assert "remaining attention" in result["next"]


def test_scoring_refresh_preserves_queued_or_running_coordinator_identity(monkeypatch):
    monkeypatch.setattr(tools, "_enqueue_sync", lambda *_args: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan", lambda *_args, **_kwargs: {
        "plan_id": "plan-1",
        "operation_id": "opaque-operation",
        "state": "running",
        "status": "syncing",
        "jobs": [{"state": "running", "course_id": "c1", "scope": "course.scoring_refresh"}],
    })

    result = tools._refresh_course_for_scoring("c1")

    assert result["ok"] is False
    assert result["state"] == "running"
    assert result["operation_id"] == "opaque-operation"
    assert result["status"] == "syncing"
