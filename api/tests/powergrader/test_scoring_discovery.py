"""Synthetic laws and examples for cross-course Scoring Session discovery."""
from __future__ import annotations

import json
import threading
import time

from api.powergrader.scoring_discovery import discover_scoring_work


def _snapshot(rows, revision=1):
    return {"mirror_revision": revision, "assignments": rows}


def _assignment(aid, name, *, due_at="", ungraded=0, partially=0, late_ungraded=0):
    return {
        "id": aid, "name": name, "due_at": due_at, "points": 10,
        "ungraded": ungraded, "partially_scored": partially,
        "late_ungraded": late_ungraded,
    }


def test_discovery_projects_all_current_courses_and_orders_rows():
    courses = [
        {"id": "c1", "name": "Alpha"},
        {"id": "c2", "name": "Beta"},
        {"id": "c3", "name": "Gamma"},
    ]
    snapshots = {
        "c1": _snapshot([_assignment("a2", "Zed", due_at="2026-09-20", ungraded=1, late_ungraded=1),
                         _assignment("a1", "Early", due_at="2026-09-10", partially=2)]),
        "c2": _snapshot([_assignment("b1", "Essay", due_at="2026-09-12", ungraded=3, late_ungraded=2)]),
        "c3": _snapshot([_assignment("g1", "Done", ungraded=0)]),
    }
    refreshed = []
    result = discover_scoring_work(
        courses,
        refresh_course=lambda course_id: refreshed.append(course_id) or {"ok": True, "mirror_revision": 7},
        load_snapshot=lambda course_id: (snapshots[course_id], None),
    )

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert refreshed == ["c1", "c2", "c3"]
    assert [row[2] for row in result["assignments"]["rows"]] == ["a1", "a2", "b1"]
    assert result["totals"] == {
        "courses_checked": 3, "courses_usable": 3, "assignments": 3,
        "ungraded": 4, "partially_scored": 2, "late_ungraded": 3,
    }


def test_discovery_refresh_completes_before_each_snapshot_and_never_exceeds_three_workers():
    courses = [{"id": f"c{i}", "name": f"Course {i}"} for i in range(8)]
    state = {"active": 0, "maximum": 0, "refreshed": set(), "violations": []}
    lock = threading.Lock()

    def refresh(course_id):
        with lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        time.sleep(0.01)
        with lock:
            state["active"] -= 1
            state["refreshed"].add(course_id)
        return {"ok": True, "mirror_revision": 1}

    def load(course_id):
        with lock:
            if course_id not in state["refreshed"]:
                state["violations"].append(course_id)
        return (_snapshot([]), None)

    result = discover_scoring_work(courses, refresh_course=refresh, load_snapshot=load)
    assert result["status"] == "nothing_to_grade"
    assert state["maximum"] <= 3
    assert state["violations"] == []


def test_discovery_partial_and_all_failure_contracts():
    courses = [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}]

    def refresh(course_id):
        return {"ok": course_id == "c1", "mirror_revision": 3}

    partial = discover_scoring_work(
        courses,
        refresh_course=refresh,
        load_snapshot=lambda _cid: (_snapshot([_assignment("a1", "Essay", ungraded=1)]), None),
    )
    assert partial["status"] == "partial"
    assert partial["assignments"]["rows"][0][0] == "c1"
    assert partial["attention"]["rows"][0][0] == "c2"

    failed = discover_scoring_work(
        courses,
        refresh_course=lambda _cid: False,
        load_snapshot=lambda _cid: (_snapshot([]), None),
    )
    assert failed["ok"] is False
    assert failed["code"] == "scoring_discovery_failed"
    assert failed["stage"] == "discover"


def test_discovery_joins_only_the_exact_current_actionable_session():
    sessions = [
        {"session_kind": "scoring_assignment", "course_id": "c1", "assignment_id": "a1",
         "session_id": "s1", "status": "ready"},
        {"session_kind": "scoring_assignment", "course_id": "c1", "assignment_id": "a2",
         "session_id": "terminal", "status": "completed"},
        {"session_kind": "scoring_assignment", "course_id": "c2", "assignment_id": "a1",
         "session_id": "wrong-course", "status": "ready"},
    ]
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        refresh_course=lambda _cid: {"ok": True, "mirror_revision": 9},
        load_snapshot=lambda _cid: (_snapshot([
            _assignment("a1", "A", ungraded=1), _assignment("a2", "B", ungraded=1),
        ]), None),
        actionable_sessions=sessions,
    )
    rows = {row[2]: row for row in result["assignments"]["rows"]}
    assert rows["a1"][9:11] == ["ready", "s1"]
    assert rows["a2"][9:11] == [None, None]


def test_discovery_projection_is_student_free_even_with_student_adjacent_snapshot_fields():
    snapshot = {
        "mirror_revision": 12,
        "students": [{"name": "UNREDACTED STUDENT", "user_id": "canvas-user"}],
        "submissions": [{"pseudonym": "Hidden", "feedback": "private"}],
        "assignments": [{
            **_assignment("a1", "Essay", ungraded=1),
            "student": "UNREDACTED STUDENT",
            "pseudonym": "Hidden",
            "submission_text": "private submission",
            "feedback": "private feedback",
        }],
    }
    result = discover_scoring_work(
        [{"id": "c1", "name": "Course"}],
        refresh_course=lambda _cid: {"ok": True, "mirror_revision": 12},
        load_snapshot=lambda _cid: (snapshot, None),
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert "UNREDACTED STUDENT" not in serialized
    assert "canvas-user" not in serialized
    assert "private submission" not in serialized
    assert "private feedback" not in serialized
    assert "Hidden" not in serialized
    assert "students" not in result
    assert "submissions" not in result


def test_refreshing_courses_are_retryable_attention_and_successful_when_all_in_progress():
    courses = [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}]

    result = discover_scoring_work(
        courses,
        refresh_course=lambda course_id: {
            "ok": False,
            "state": "queued" if course_id == "c1" else "running",
            "operation_id": f"opaque-{course_id}",
            "usable": False,
        },
        load_snapshot=lambda _cid: (_snapshot([]), None),
    )

    assert result["ok"] is True
    assert result["status"] == "refreshing"
    assert result["assignments"]["rows"] == []
    assert result["totals"] == {
        "courses_checked": 2, "courses_usable": 0, "assignments": 0,
        "ungraded": 0, "partially_scored": 0, "late_ungraded": 0,
    }
    attention = [dict(zip(result["attention"]["columns"], row))
                 for row in result["attention"]["rows"]]
    assert [row["code"] for row in attention] == [
        "mirror_refresh_in_progress", "mirror_refresh_in_progress",
    ]
    assert [row["operation_id"] for row in attention] == ["opaque-c1", "opaque-c2"]
    assert [row["refresh_status"] for row in attention] == ["queued", "running"]
    assert all(row["retryable"] for row in attention)
    assert all("retry discover_scoring_work" in row["user_action"].casefold()
               and "no teacher action" in row["user_action"].casefold()
               for row in attention)


def test_refreshing_status_keeps_usable_rows_and_precedes_partial():
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}],
        refresh_course=lambda course_id: (
            {"ok": True, "mirror_revision": 4}
            if course_id == "c1" else
            {"ok": False, "state": "running", "operation_id": "opaque-c2"}
        ),
        load_snapshot=lambda _cid: (_snapshot([
            _assignment("a1", "Essay", ungraded=1),
        ]), None),
    )

    assert result["ok"] is True
    assert result["status"] == "refreshing"
    assert result["assignments"]["rows"][0][2] == "a1"
    assert result["totals"]["courses_usable"] == 1
    assert result["attention"]["rows"][0][2] == "mirror_refresh_in_progress"


def test_terminal_failures_keep_existing_partial_and_failure_semantics():
    partial = discover_scoring_work(
        [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}],
        refresh_course=lambda course_id: (
            {"ok": True, "mirror_revision": 4}
            if course_id == "c1" else
            {"ok": False, "state": "failed", "error_code": "canvas_timeout"}
        ),
        load_snapshot=lambda _cid: (_snapshot([
            _assignment("a1", "Essay", ungraded=1),
        ]), None),
    )
    assert partial["status"] == "partial"
    assert partial["attention"]["rows"][0][2] == "canvas_timeout"

    failed = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        refresh_course=lambda _cid: {
            "ok": False, "state": "failed", "error_code": "canvas_timeout",
        },
        load_snapshot=lambda _cid: (_snapshot([]), None),
    )
    assert failed["ok"] is False
    assert failed["code"] == "scoring_discovery_failed"
