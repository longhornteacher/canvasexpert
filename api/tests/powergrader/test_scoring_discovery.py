"""Examples and laws for local-only cross-course scoring discovery."""
from __future__ import annotations

import json

from api.powergrader.scoring_discovery import discover_scoring_work


def _snapshot(rows, revision=1):
    return {"mirror_revision": revision, "assignments": rows}


def _assignment(aid, name, *, due_at="", ungraded=0, partially=0, late_ungraded=0):
    return {"id": aid, "name": name, "due_at": due_at, "points": 10,
            "ungraded": ungraded, "partially_scored": partially,
            "late_ungraded": late_ungraded}


def _fresh(course_id, name="Course", *, state="current", age=0, needs=False):
    return {"course_id": course_id, "course_name": name, "state": state,
            "last_success_at": "2026-09-21T12:00:00Z", "age_minutes": age,
            "requires_teacher_confirmation": needs}


def test_discovery_projects_current_courses_and_orders_rows_without_refresh():
    courses = [{"id": "c1", "name": "Alpha"}, {"id": "c2", "name": "Beta"}]
    snapshots = {"c1": _snapshot([_assignment("a2", "Zed", due_at="2026-09-20", ungraded=1),
                                  _assignment("a1", "Early", due_at="2026-09-10", partially=2)]),
                 "c2": _snapshot([_assignment("b1", "Essay", ungraded=3)])}
    calls = []

    def load(course_id, **_kwargs):
        calls.append(course_id)
        return {"snapshot": snapshots[course_id], "freshness": _fresh(course_id), "error": None}

    result = discover_scoring_work(courses, load_snapshot=load)
    assert result["status"] == "ready"
    assert sorted(calls) == ["c1", "c2"]
    assert [row[2] for row in result["assignments"]["rows"]] == ["a1", "a2", "b1"]
    assert result["totals"]["courses_usable"] == 2
    assert len(result["freshness"]["rows"]) == 2


def test_discovery_joins_only_current_actionable_session():
    sessions = [{"session_kind": "scoring_assignment", "course_id": "c1", "assignment_id": "a1",
                 "session_id": "s1", "status": "staged"},
                {"session_kind": "scoring_assignment", "course_id": "c1", "assignment_id": "a2",
                 "session_id": "done", "status": "completed"}]
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        load_snapshot=lambda _cid, **_kw: {"snapshot": _snapshot([
            _assignment("a1", "A", ungraded=1), _assignment("a2", "B", ungraded=1)]),
            "freshness": _fresh("c1", "One"), "error": None},
        actionable_sessions=sessions)
    rows = {row[2]: row for row in result["assignments"]["rows"]}
    assert rows["a1"][9:11] == ["staged", "s1"]
    assert rows["a2"][9:11] == [None, None]


def test_unavailable_projection_is_fail_closed_and_keeps_freshness_table():
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        load_snapshot=lambda _cid, **_kw: {"snapshot": None,
            "freshness": _fresh("c1", "One", state="unavailable"), "error": "corrupt"})
    assert result["ok"] is False
    assert result["code"] == "scoring_discovery_failed"
    assert result["attention"]["rows"][0][2] == "mirror_projection_unavailable"
    assert result["freshness"]["rows"][0][2] == "unavailable"


def test_discovery_is_student_free_even_if_loader_snapshot_is_not():
    snapshot = {"mirror_revision": 12, "students": [{"name": "PRIVATE", "user_id": "canvas-user"}],
                "assignments": [{**_assignment("a1", "Essay", ungraded=1),
                                 "submission_text": "private text", "pseudonym": "Hidden"}]}
    result = discover_scoring_work(
        [{"id": "c1", "name": "Course"}],
        load_snapshot=lambda _cid, **_kw: {"snapshot": snapshot, "freshness": _fresh("c1"), "error": None})
    serialized = json.dumps(result)
    assert all(value not in serialized for value in ("PRIVATE", "canvas-user", "private text", "Hidden"))
    assert "students" not in result


def test_stale_freshness_is_reported_but_does_not_trigger_refresh():
    calls = []
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        load_snapshot=lambda _cid, **_kw: (calls.append(True) or {
            "snapshot": _snapshot([_assignment("a1", "Essay", ungraded=1)]),
            "freshness": _fresh("c1", "One", age=31, needs=True), "error": None}))
    assert calls == [True]
    assert result["status"] == "ready"
    assert result["freshness"]["rows"][0][4] == 31
    assert result["freshness"]["rows"][0][5] is True


def test_metadata_driven_differentiated_sources_always_show_bridge_state():
    rows = [
        {
            **_assignment("tier-a", "Renamed support", ungraded=1),
            "metadata": {"family_id": "family-1", "tier": "Support"},
        },
        {
            **_assignment("tier-b", "Renamed core", partially=1),
            "metadata": {"family_id": "family-1", "tier": "Core"},
        },
        {
            **_assignment("bridge-1", "Renamed gradebook column"),
            "metadata": {"family_id": "family-1", "bridge": True},
        },
    ]
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        load_snapshot=lambda _cid, **_kw: {
            "snapshot": _snapshot(rows),
            "freshness": _fresh("c1", "One"),
            "error": None,
        },
        tier_tags={"Support": "Red", "Core": "Blue"},
    )

    projected = {row[2]: row for row in result["assignments"]["rows"]}
    source = projected["tier-a"]
    assert source[12:19] == [
        "Renamed support", "source", "needs_repair", "bridge-1", "present", True,
        "Scoring this tier is incomplete until the corresponding bridge score is prepared and applied through the reviewed SIS bridge operation.",
    ]


def test_metadata_driven_family_without_bridge_requires_reconciliation():
    rows = [
        {
            **_assignment("tier-a", "Renamed support", ungraded=1),
            "metadata": {"family_id": "family-2", "tier": "Support"},
        },
        {
            **_assignment("tier-b", "Renamed core", partially=1),
            "metadata": {"family_id": "family-2", "tier": "Core"},
        },
    ]
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        load_snapshot=lambda _cid, **_kw: {
            "snapshot": _snapshot(rows),
            "freshness": _fresh("c1", "One"),
            "error": None,
        },
        tier_tags={"Support": "Red", "Core": "Blue"},
    )

    projected = {row[2]: row for row in result["assignments"]["rows"]}
    source = projected["tier-a"]
    assert source[15:18] == [None, "missing", True]
    assert "no bridge yet" in source[18].casefold()


def test_single_unsuffixed_assignment_is_not_labeled_as_missing_bridge():
    result = discover_scoring_work(
        [{"id": "c1", "name": "One"}],
        load_snapshot=lambda _cid, **_kw: {
            "snapshot": _snapshot([_assignment("ordinary", "AI Questions", ungraded=2)]),
            "freshness": _fresh("c1", "One"), "error": None,
        },
    )
    row, = result["assignments"]["rows"]
    assert row[12:19] == [None, None, None, None, None, False, None]
