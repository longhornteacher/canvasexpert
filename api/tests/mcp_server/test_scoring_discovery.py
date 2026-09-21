"""MCP boundary contracts for local-only cross-course discovery."""
from __future__ import annotations

import json

from api.mcp_server import server, tools
from api.powergrader import session_store


def test_discover_injects_local_snapshot_reader_and_next_advisory(monkeypatch):
    calls = []
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])
    monkeypatch.setattr(session_store, "current_actionable_sessions", lambda **_kwargs: [])
    monkeypatch.setattr(tools.scoring_discovery, "discover_scoring_work",
                        lambda courses, **kwargs: calls.append((courses, kwargs)) or {
                            "ok": True, "status": "nothing_to_grade",
                            "assignments": {"columns": [], "rows": []},
                            "totals": {"courses_checked": 1, "courses_usable": 1,
                                       "assignments": 0, "ungraded": 0,
                                       "partially_scored": 0, "late_ungraded": 0},
                            "attention": {"columns": [], "rows": []},
                            "freshness": {"columns": [], "rows": []}})
    result = tools.discover_scoring_work()
    assert result["status"] == "nothing_to_grade"
    assert "wait for teacher direction" in result["next"]
    assert calls and callable(calls[0][1]["load_snapshot"])
    assert "refresh_course" not in calls[0][1]


def test_discovery_response_is_student_free_and_wrapper_is_text_only(monkeypatch):
    monkeypatch.setattr(tools, "discover_scoring_work", lambda: {
        "ok": True, "status": "nothing_to_grade",
        "assignments": {"columns": ["course_id"], "rows": []},
        "freshness": {"columns": ["state"], "rows": [["current"]]},
        "totals": {"courses_checked": 1, "courses_usable": 1, "assignments": 0,
                   "ungraded": 0, "partially_scored": 0, "late_ungraded": 0},
        "attention": {"columns": [], "rows": []},
        "next": "Report all rows and wait for teacher direction."})
    payload = json.loads(server._compact(tools.discover_scoring_work()))
    assert payload["ok"] is True
    assert "students" not in json.dumps(payload).casefold()
    assert "pseudonym" not in json.dumps(payload).casefold()


def test_discovery_tool_has_no_required_arguments_and_is_grouped_once():
    tool = server.mcp._tool_manager._tools["discover_scoring_work"]
    assert (tool.parameters.get("required") or []) == []
    grouped = [name for names in tools._TOOL_GROUPS.values() for name in names]
    assert grouped.count("discover_scoring_work") == 1


def test_discovery_next_advisory_stays_local_and_actionable():
    result = tools._with_next("discover_scoring_work", {"ok": True, "status": "partial"})
    assert "wait for teacher direction" in result["next"]
    assert "refresh" not in result["next"].casefold()
