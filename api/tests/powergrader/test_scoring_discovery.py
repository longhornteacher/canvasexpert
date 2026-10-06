"""Examples and laws for local-only cross-course scoring discovery."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from api.powergrader.scoring_discovery import discover_scoring_work

NOW = datetime(2026, 9, 21, 12, 30, tzinfo=timezone.utc)
STAMP = "2026-09-21T12:00:00Z"


def _item(aid, name, *, due_at=None, ungraded=0, partially=0, late=0, coverage="complete",
          counts_complete=True, observed=STAMP):
    unknown = coverage == "unknown"
    return {"assignment_id": aid, "name": name, "due_at": due_at, "points": 10,
            "published": True, "coverage": coverage, "counts_complete": counts_complete,
            "submissions_observed_at": None if unknown else observed, "ambiguous_entities": 0,
            "ungraded": None if unknown else ungraded,
            "partially_scored": None if unknown else partially,
            "late_ungraded": None if unknown else late}


def _course(cid, items, *, roster="complete", scope="complete", title="Course", observed=STAMP):
    return {"course_id": cid, "title": title, "selection_status": "current", "revision": "rev-1",
            "roster": {"coverage": roster, "observed_at": None if roster == "unknown" else observed,
                       "known_members": 2},
            "assignments_scope": {"coverage": scope, "observed_at": None if scope == "unknown" else observed,
                                  "status": "ready"},
            "assignments": items}


def _evidence(*courses, revision="rev-1"):
    return {"revision": revision, "courses": list(courses)}


def _discover(courses, *evidence_courses, **kwargs):
    kwargs.setdefault("now", NOW)
    return discover_scoring_work(courses, evidence=_evidence(*evidence_courses), **kwargs)


def test_discovery_projects_current_courses_and_orders_rows():
    courses = [{"id": "c1", "name": "Alpha"}, {"id": "c2", "name": "Beta"}]
    result = _discover(
        courses,
        _course("c1", [_item("a2", "Zed", due_at="2026-09-20", ungraded=1),
                       _item("a1", "Early", due_at="2026-09-10", partially=2, ungraded=2)]),
        _course("c2", [_item("b1", "Essay", ungraded=3)]))
    assert result["status"] == "ready"
    assert [row[2] for row in result["assignments"]["rows"]] == ["a1", "a2", "b1"]
    assert result["totals"]["courses_usable"] == 2
    assert result["totals"]["counts_complete"] is True
    assert result["totals"]["assignments_with_unknown_counts"] == 0
    assert len(result["freshness"]["rows"]) == 2
    assert result["assignments"]["rows"][0][11] == "rev-1"      # opaque revision string
    assert result["assignments"]["rows"][0][19:21] == ["complete", True]


def test_complete_empty_queue_is_nothing_to_grade_but_gap_is_not():
    courses = [{"id": "c1", "name": "One"}]
    done = _discover(courses, _course("c1", [_item("a1", "Done")]))
    assert done["status"] == "nothing_to_grade" and done["assignments"]["rows"] == []
    # An assignment whose counts are unknown must surface, never disappear as zero.
    gap = _discover(courses, _course("c1", [_item("a1", "Mystery", coverage="unknown",
                                                  counts_complete=False)]))
    assert gap["status"] == "partial"
    assert gap["assignments"]["rows"][0][6] is None
    assert gap["totals"]["counts_complete"] is False
    assert gap["totals"]["assignments_with_unknown_counts"] == 1
    assert gap["attention"]["rows"][0][2] == "evidence_not_acquired"
    assert gap["attention"]["rows"][0][7] == "a1"


def test_partial_observation_is_listed_and_flagged_incomplete():
    result = _discover([{"id": "c1", "name": "One"}], _course("c1", [
        _item("a1", "Partial", ungraded=0, coverage="incomplete", counts_complete=False)]))
    assert result["status"] == "partial"
    assert result["assignments"]["rows"][0][19:21] == ["incomplete", False]
    assert result["attention"]["rows"][0][2:3] == ["evidence_membership_incomplete"]


def test_one_unreadable_course_does_not_block_a_healthy_one():
    courses = [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}]
    result = _discover(courses, _course("c1", [_item("a1", "Essay", ungraded=1)]),
                       _course("c2", [], scope="unknown", roster="unknown"))
    assert result["ok"] is True and result["status"] == "partial"
    assert [row[2] for row in result["assignments"]["rows"]] == ["a1"]
    attention = {row[0]: row for row in result["attention"]["rows"]}
    assert attention["c2"][2] == "evidence_not_acquired" and attention["c2"][7] is None
    states = {row[0]: row for row in result["freshness"]["rows"]}
    assert states["c2"][2] == "unavailable" and states["c2"][4] is None   # unknown age, not zero
    assert states["c2"][-1] == "unknown" and states["c1"][-1] == "complete"


def test_no_readable_course_is_a_typed_failure_with_specific_recovery():
    result = _discover([{"id": "c1", "name": "One"}], _course("c1", [], scope="unknown", roster="unknown"))
    assert result["ok"] is False and result["code"] == "scoring_discovery_failed"
    assert result["attention"]["rows"][0][2] == "evidence_not_acquired"
    assert result["freshness"]["rows"][0][2] == "unavailable"
    absent = discover_scoring_work([{"id": "c1", "name": "One"}], evidence=_evidence(), now=NOW)
    assert absent["ok"] is False and absent["attention"]["rows"][0][2] == "evidence_not_acquired"


def test_scoped_course_errors_keep_other_courses_usable():
    courses = [{"id": "c1", "name": "One"}, {"id": "c2", "name": "Two"}]
    result = _discover(courses, _course("c1", [_item("a1", "Essay", ungraded=1)]),
                       _course("c2", [_item("b1", "Hidden", ungraded=9)]),
                       course_errors={"c2": "evidence_update_required"})
    assert [row[2] for row in result["assignments"]["rows"]] == ["a1"]
    assert result["attention"]["rows"][0][2] == "evidence_update_required"
    assert result["attention"]["rows"][0][3] is False


def test_discovery_joins_only_current_actionable_session():
    sessions = [{"session_kind": "scoring_assignment", "course_id": "c1", "assignment_id": "a1",
                 "session_id": "s1", "status": "staged"},
                {"session_kind": "scoring_assignment", "course_id": "c1", "assignment_id": "a2",
                 "session_id": "done", "status": "completed"}]
    result = _discover([{"id": "c1", "name": "One"}],
                       _course("c1", [_item("a1", "A", ungraded=1), _item("a2", "B", ungraded=1)]),
                       actionable_sessions=sessions)
    rows = {row[2]: row for row in result["assignments"]["rows"]}
    assert rows["a1"][9:11] == ["staged", "s1"]
    assert rows["a2"][9:11] == [None, None]


def test_resume_lookup_failure_warns_without_erasing_assignments():
    result = _discover([{"id": "c1", "name": "One"}],
                       _course("c1", [_item("a1", "A", ungraded=1)]), resume_incomplete=True)
    assert result["ok"] is True and result["status"] == "partial"
    assert len(result["assignments"]["rows"]) == 1
    assert result["attention"]["rows"][-1][2] == "scoring_resume_unavailable"


def test_discovery_is_student_free_even_if_evidence_carries_extras():
    course = _course("c1", [{**_item("a1", "Essay", ungraded=1), "pseudonym": "Hidden",
                             "submission_text": "private text"}])
    result = _discover([{"id": "c1", "name": "Course"}], {**course, "students": ["PRIVATE"]})
    serialized = json.dumps(result)
    assert all(value not in serialized for value in ("PRIVATE", "private text", "Hidden"))


def test_stale_age_is_reported_but_never_withholds_work():
    old = "2026-09-21T11:00:00Z"
    result = _discover([{"id": "c1", "name": "One"}],
                       _course("c1", [_item("a1", "Essay", ungraded=1, observed=old)], observed=old))
    assert result["status"] == "ready"
    row = result["freshness"]["rows"][0]
    assert row[4] == 90 and row[5] is True                       # age_minutes, refresh requested
    assert row[2] == "stale"


def test_oldest_available_observation_sets_course_age():
    result = _discover([{"id": "c1", "name": "One"}], _course("c1", [
        _item("a1", "New", ungraded=1, observed="2026-09-21T12:20:00Z"),
        _item("a2", "Old", ungraded=1, observed="2026-09-21T10:30:00Z")]))
    assert result["freshness"]["rows"][0][4] == 120


def test_metadata_free_family_labels_come_from_titles_and_registrations_only():
    items = [_item("tier-a", "Quiz (Red)", ungraded=1), _item("tier-b", "Quiz (Blue)", partially=1, ungraded=1)]
    result = _discover([{"id": "c1", "name": "One"}], _course("c1", items),
                       tier_tags={"Support": "Red", "Core": "Blue"})
    rows = {row[2]: row for row in result["assignments"]["rows"]}
    assert rows["tier-a"][13:18] == ["source", "needs_repair", None, "missing", True]
    assert "no bridge yet" in rows["tier-a"][18].casefold()


def test_single_unsuffixed_assignment_is_not_labeled_as_missing_bridge():
    result = _discover([{"id": "c1", "name": "One"}], _course("c1", [_item("ordinary", "AI Questions", ungraded=2)]))
    row, = result["assignments"]["rows"]
    assert row[12:19] == [None, None, None, None, None, False, None]


def test_empty_selection_uses_existing_no_current_courses_code():
    result = discover_scoring_work([], evidence=_evidence(), now=NOW)
    assert result["ok"] is False and result["code"] == "no_current_courses"
