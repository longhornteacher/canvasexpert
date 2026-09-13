"""Root listing exposes aggregate progress and never exposes private children."""
from __future__ import annotations

import copy
import json
from contextlib import nullcontext

from api.mcp_server import tools
from api.powergrader import scoring_queue, session_store


def test_list_scoring_sessions_lists_current_roots_not_children_or_old_records(
    monkeypatch, _rows,
):
    sessions = {}
    monkeypatch.setattr(tools.config, "active_courses",
                        lambda: [{"id": "c1", "name": "Course One"}])
    monkeypatch.setattr(session_store, "load_session",
                        lambda session_id: copy.deepcopy(sessions.get(session_id)))
    monkeypatch.setattr(session_store, "save_session",
                        lambda session: sessions.__setitem__(session["session_id"], copy.deepcopy(session)))
    monkeypatch.setattr(session_store, "session_lock", lambda _sid: nullcontext())
    monkeypatch.setattr(session_store, "list_session_summaries", lambda: [
        {"session_id": session["session_id"],
         "session_kind": session.get("session_kind", ""),
         "created": session.get("created", "")}
        for session in sessions.values()
    ])
    root = scoring_queue.create_root_session(queue=[
        {"course_id": "c1", "course_label": "Course One", "assignment_id": "a1",
         "assignment_label": "Essay One", "due_at": "", "ungraded": 3, "partially_scored": 1},
        {"course_id": "c1", "course_label": "Course One", "assignment_id": "a2",
         "assignment_label": "Essay Two", "due_at": "", "ungraded": 2, "partially_scored": 0},
    ], scope={}, session_id="root-public")
    root["queue"][0]["status"] = "completed"
    root["queue"][1]["status"] = "ready"
    root["queue"][1]["child_session_id"] = "private-child-id"
    root["active_index"] = 1
    root["queue_digest"] = scoring_queue._queue_digest(root["queue"])
    root["progress"] = scoring_queue._progress(root)
    root["status"] = "ready"
    session_store.save_session(root)
    session_store.save_session({"session_id": "private-child-id", "session_kind": "assignment_run",
                                "parent_scoring_session_id": "root-public", "course_id": "c1"})
    session_store.save_session({"session_id": "old-assignment-session", "course_id": "c1"})
    previous = scoring_queue.create_root_session(queue=[{
        "course_id": "c2", "course_label": "Previous Course", "assignment_id": "a3",
        "assignment_label": "Old Essay", "due_at": "", "ungraded": 1, "partially_scored": 0,
    }], scope={}, session_id="root-previous")
    session_store.save_session(previous)

    result = tools.list_scoring_sessions()
    rows = _rows(result["sessions"])

    assert list(result["sessions"]["columns"]) == list(tools._SCORING_SESSION_COLUMNS)
    assert len(rows) == 1
    [row] = rows
    assert row["scoring_session_id"] == "root-public"
    assert row["status"] == "ready"
    assert row["active_course"] == "Course One"
    assert row["active_assignment"] == "Essay Two"
    assert row["total"] == 2 and row["completed"] == 1 and row["remaining"] == 1
    wire = json.dumps(result)
    assert "private-child-id" not in wire
    assert "old-assignment-session" not in wire
