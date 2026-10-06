"""MCP boundary contracts for local-only cross-course discovery."""
from __future__ import annotations

import json

from api.mcp_server import server, tools
from api.powergrader import session_store


class _Service:
    def __init__(self):
        self.calls = []

    def read_scoring_discovery(self, *, source_key, course_ids):
        self.calls.append((source_key, list(course_ids)))
        return {"revision": "rev", "courses": []}


def test_discover_reads_evidence_once_and_never_touches_private_projection(monkeypatch):
    service, calls = _Service(), []
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])
    monkeypatch.setattr(tools, "_evidence_reader", lambda **kw: (service, "a" * 64, None))
    monkeypatch.setattr(tools, "_load_scoring_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("private projection read")))
    monkeypatch.setattr(session_store, "discovery_session_summaries",
                        lambda **_kwargs: {"summaries": [], "incomplete": True})
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
    assert service.calls == [("a" * 64, ["c1"])]
    kwargs = calls[0][1]
    assert kwargs["evidence"] == {"revision": "rev", "courses": []}
    assert kwargs["resume_incomplete"] is True
    assert "load_snapshot" not in kwargs and "refresh_course" not in kwargs


def test_update_required_course_is_scoped_not_a_blanket_refusal(monkeypatch):
    service, seen = _Service(), {}
    monkeypatch.setattr(tools.config, "active_courses",
                        lambda: [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}])

    def reader(*, scoped_updates=None):
        scoped_updates.append("c2")
        return service, "a" * 64, None
    monkeypatch.setattr(tools, "_evidence_reader", reader)
    monkeypatch.setattr(session_store, "discovery_session_summaries",
                        lambda **_kw: {"summaries": [], "incomplete": False})
    monkeypatch.setattr(tools.scoring_discovery, "discover_scoring_work",
                        lambda courses, **kw: seen.update(kw) or {"ok": True, "status": "partial"})
    assert tools.discover_scoring_work()["ok"] is True
    assert seen["course_errors"] == {"c2": "evidence_update_required"}


def test_global_reader_refusal_is_returned_as_is(monkeypatch):
    refusal = {"ok": False, "code": "evidence_index_pending", "error": "waiting"}
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "c1", "name": "Course"}])
    monkeypatch.setattr(tools, "_evidence_reader", lambda **kw: (None, None, refusal))
    assert tools.discover_scoring_work() == refusal


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
