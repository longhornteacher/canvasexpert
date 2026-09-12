"""Bounded, aggregate-only Work Registry discovery tests."""

import json
import threading
import time
from datetime import datetime, timezone

import pytest

from api.work_registry import discovery, storage
from api.work_registry.models import material_version, stable_fingerprint
from api.work_registry.providers import CalendarNeedsAttention, WorkCourseReads, finding
from api.work_registry.providers import grading_debt, home_attention, late_work, roster_warnings


def _job(kind="grade.debt", course_id="course-1", assignment_id="assignment-1"):
    return finding(
        kind=kind,
        course_id=course_id,
        assignment_id=assignment_id,
        counts={"total": 1, "pending": 1, "affected": 1},
        now="2026-07-11T12:00:00+00:00",
        latest_submitted_at="2026-07-11T11:00:00+00:00",
        latest_attempt_number=1,
        due_at="2026-07-10T11:00:00+00:00",
        resumable_url="/gradebook",
    )


def _reads(live_reader, deadline=None):
    """Build a WorkCourseReads with a test live_reader."""
    return WorkCourseReads(
        "course-1", deadline=deadline or time.monotonic() + 5, live_reader=live_reader,
    )


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    monkeypatch.setattr(storage.workspace, "workspace_root", lambda: str(root))
    return root


def test_discovery_cache_round_trip_and_quarantine(workspace_root):
    job = _job()
    document = {
        "version": 1,
        "updated_at": "2026-07-11T12:00:00+00:00",
        "courses": {
            "course-1": {
                "checked_at": "2026-07-11T12:00:00+00:00",
                "findings": [job],
                "stale": False,
                "error_code": "",
            }
        },
    }
    assert storage.write_discovery_cache(document) == {"ok": True}
    assert storage.read_discovery_cache() == document

    path = storage.discovery_cache_path()
    path.write_text("not json", encoding="utf-8")
    assert storage.read_discovery_cache()["courses"] == {}
    assert list((workspace_root / "_System" / "workbench" / "quarantine").glob("*.corrupt"))


def test_scan_caps_concurrency_and_persists_course_results(workspace_root, monkeypatch):
    monkeypatch.setattr(discovery.config, "active_courses", lambda: [{"id": str(i)} for i in range(5)])
    lock = threading.Lock()
    state = {"active": 0, "maximum": 0}

    def fake_scan(course, *, now, deadline):
        with lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return {
            "course_id": course.get("id"),
            "checked_at": now,
            "findings": [_job(course_id=course.get("id"))],
            "stale": False,
            "error_code": "",
        }

    monkeypatch.setattr(discovery, "_scan_course", fake_scan)
    result = discovery.scan_active_courses(now=datetime(2026, 7, 11, tzinfo=timezone.utc))

    assert result["ok"] is True
    assert result["courses_scanned"] == 5
    assert result["findings"] == 5
    assert state["maximum"] <= 3
    assert len(storage.read_discovery_cache()["courses"]) == 5


def test_scan_enforces_overall_deadline(workspace_root, monkeypatch):
    monkeypatch.setattr(discovery.config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(discovery, "SCAN_TIMEOUT_SECONDS", 0.01)

    def slow_scan(course, *, now, deadline):
        time.sleep(0.05)
        return {"course_id": course["id"], "checked_at": now, "findings": [], "stale": False, "error_code": ""}

    monkeypatch.setattr(discovery, "_scan_course", slow_scan)
    result = discovery.scan_active_courses()

    assert result["partial"] is True
    assert result["stale_course_ids"] == ["course-1"]
    assert "course_timeout" in result["error_codes"]


def test_scan_keeps_last_good_course_on_failure(workspace_root, monkeypatch):
    previous = _job(course_id="course-1")
    storage.write_discovery_cache({
        "version": 1,
        "updated_at": "2026-07-11T11:00:00+00:00",
        "courses": {
            "course-1": {
                "checked_at": "2026-07-11T11:00:00+00:00",
                "findings": [previous],
                "stale": False,
                "error_code": "",
            }
        },
    })
    monkeypatch.setattr(discovery.config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(discovery, "_scan_course", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))

    result = discovery.scan_active_courses()
    cached = storage.read_discovery_cache()["courses"]["course-1"]
    assert result["partial"] is True
    assert result["stale_course_ids"] == ["course-1"]
    assert cached["stale"] is True
    assert cached["error_code"] == "provider_failed"
    assert cached["findings"] == [previous]


def test_course_provider_failure_does_not_stop_other_providers(monkeypatch):
    monkeypatch.setattr(
        discovery.grading_debt,
        "scan_course",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()),
    )
    monkeypatch.setattr(
        discovery.late_work,
        "scan_course",
        lambda *args, **kwargs: [_job(kind="late.work", assignment_id="late-1")],
    )
    monkeypatch.setattr(
        discovery.roster_warnings,
        "scan_course",
        lambda *args, **kwargs: [_job(kind="roster.warning", assignment_id="warning-1")],
    )
    monkeypatch.setattr(discovery.home_attention, "scan_comment_follow_up", lambda *args, **kwargs: [])

    record = discovery._scan_course(
        {"id": "course-1"}, now="2026-07-11T12:00:00+00:00", deadline=time.monotonic() + 5)
    assert record["stale"] is True
    assert record["error_code"] == "provider_failed"
    assert {job["assignment_id"] for job in record["findings"]} == {"late-1", "warning-1"}


def test_scan_course_shares_successful_assignment_and_submission_reads(monkeypatch):
    monkeypatch.setattr(late_work.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(
        late_work.school_calendar, "resolve_instructional_range",
        lambda date_from, date_to, known_schedule_ids, **kw: {
            "state": "ready", "date_from": date_from, "date_to": date_to,
            "days": {}, "no_count_dates": [],
        })
    monkeypatch.setattr(roster_warnings, "scan_course", lambda *args, **kwargs: [])

    assignments_path = "/api/v1/courses/course-1/assignments"
    submissions_path = "/api/v1/courses/course-1/students/submissions"
    calls = {assignments_path: 0, submissions_path: 0}

    def fake_get(path, params=None, timeout=None):
        assert timeout == 10
        calls[path] += 1
        if path == assignments_path:
            return [
                {"id": "debt-1", "published": True, "due_at": "2026-07-09T11:00:00+00:00",
                 "submission_types": ["online_text_entry"]},
                {"id": "late-1", "published": True, "due_at": "2026-07-09T11:00:00+00:00"},
            ], None
        assert path == submissions_path
        assert params == {
            "student_ids[]": "all",
            "include[]": "submission_comments",
            "per_page": 100,
        }
        return [
            {
                "assignment_id": "debt-1",
                "user_id": "fictional-learner-1",
                "workflow_state": "submitted",
                "submitted_at": "2026-07-11T11:00:00+00:00",
                "score": None,
                "submission_comments": [],
            },
            {
                "assignment_id": "late-1",
                "user_id": "fictional-learner-2",
                "workflow_state": "late",
                "late": True,
                "submitted_at": "2026-07-11T11:00:00+00:00",
                "submission_comments": [],
            },
        ], None

    record = discovery._scan_course(
        {"id": "course-1"},
        now="2026-07-11T12:00:00+00:00",
        deadline=time.monotonic() + 5,
        canvas_get_all=fake_get,
    )

    assert record["stale"] is False
    assert {item["kind"] for item in record["findings"]} == {
        "grade.debt", "late.work",
    }
    assert calls == {assignments_path: 1, submissions_path: 1}



def test_grading_debt_counts_zero_as_graded_and_teacher_comment_as_touched(monkeypatch):

    def fake_get(path, params=None, timeout=None):
        assert timeout == 10
        if path.endswith("/assignments"):
            return [{"id": 1, "due_at": "2026-07-10T11:00:00+00:00"}], None
        return [
            {"assignment_id": 1, "user_id": "u-zero", "workflow_state": "submitted",
             "submitted_at": "2026-07-11T11:00:00+00:00", "score": 0},
            {"assignment_id": 1, "user_id": "u-comment", "workflow_state": "submitted",
             "submitted_at": "2026-07-11T10:00:00+00:00", "score": None,
             "submission_comments": [{"author_id": "teacher-1"}]},
            {"assignment_id": 1, "user_id": "u-pending", "workflow_state": "pending_review",
             "submitted_at": "2026-07-11T09:00:00+00:00", "score": None,
             "submission_comments": []},
        ], None

    reads = _reads(fake_get)
    findings = grading_debt.scan_course(
        "course-1", now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    assert len(findings) == 1
    assert findings[0]["counts"] == {"total": 3, "pending": 1, "affected": 1}


def test_late_work_uses_school_day_and_extra_time(monkeypatch):
    monkeypatch.setattr(late_work.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(
        late_work.school_calendar, "resolve_instructional_range",
        lambda date_from, date_to, known_schedule_ids, **kw: {
            "state": "ready", "date_from": date_from, "date_to": date_to,
            "days": {}, "no_count_dates": [],
        })

    def fake_get(path, params=None, timeout=None):
        if path.endswith("/assignments"):
            return [{"id": 1, "published": True, "due_at": "2026-07-09T11:00:00+00:00"}], None
        return [{"assignment_id": 1, "user_id": "u-1", "workflow_state": "late",
                 "submitted_at": "2026-07-11T11:00:00+00:00"}], None

    reads = _reads(fake_get)
    findings = late_work.scan_course(
        "course-1", now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == "late.work"
    assert findings[0]["counts"]["affected"] == 1


def test_late_work_raises_calendar_needs_attention_when_calendar_cannot_cover_the_range(monkeypatch):
    """A late submission exists, but the calendar can't validate its
    due/submitted range -- the provider must raise rather than confidently
    reporting zero late-work findings."""
    monkeypatch.setattr(late_work.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(
        late_work.school_calendar, "resolve_instructional_range",
        lambda date_from, date_to, known_schedule_ids, **kw: {
            "state": "unconfigured", "problems": ["unconfigured"], "repair_url": "/calendar",
        })

    def fake_get(path, params=None, timeout=None):
        if path.endswith("/assignments"):
            return [{"id": 1, "published": True, "due_at": "2026-07-09T11:00:00+00:00"}], None
        return [{"assignment_id": 1, "user_id": "u-1", "workflow_state": "late",
                 "submitted_at": "2026-07-11T11:00:00+00:00"}], None

    reads = _reads(fake_get)
    with pytest.raises(CalendarNeedsAttention):
        late_work.scan_course("course-1", now="2026-07-11T12:00:00+00:00", reads=reads)


def test_scan_course_marks_stale_with_calendar_error_code_and_keeps_last_good(monkeypatch):
    """discovery._scan_course must classify CalendarNeedsAttention distinctly
    and mark the course stale -- scan_active_courses then falls back to the
    last-known-good findings rather than confidently reporting zero."""
    monkeypatch.setattr(grading_debt, "scan_course", lambda *args, **kwargs: [])
    monkeypatch.setattr(roster_warnings, "scan_course", lambda *args, **kwargs: [])
    monkeypatch.setattr(discovery.home_attention, "scan_comment_follow_up", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        discovery.late_work, "scan_course",
        lambda *args, **kwargs: (_ for _ in ()).throw(CalendarNeedsAttention()),
    )

    record = discovery._scan_course(
        {"id": "course-1"}, now="2026-07-11T12:00:00+00:00", deadline=time.monotonic() + 5)
    assert record["stale"] is True
    assert record["error_code"] == "calendar_needs_attention"


def test_comment_follow_up_classifier_is_ordered_conservative_and_ta_aware():
    base = {
        "user_id": "synthetic-student-1",
        "submission_comments": [
            {"author_id": "synthetic-staff-1", "author_role": "teacher", "created_at": "2026-07-11T09:00:00+00:00"},
            {"author_id": "synthetic-student-1", "created_at": "2026-07-11T10:00:00+00:00"},
        ],
    }
    assert home_attention.classify_comment_follow_up(base) == "definite"

    with_ta = {
        **base,
        "submission_comments": [
            *base["submission_comments"],
            {
                "author_id": "synthetic-staff-2",
                "created_at": "2026-07-11T11:00:00+00:00",
                "comment": home_attention._TA_MARKER,
            },
        ],
    }
    assert home_attention.classify_comment_follow_up(with_ta) == "definite"

    unknown_staff = {
        **base,
        "submission_comments": [
            base["submission_comments"][1],
            {"author_id": "synthetic-unknown-1", "created_at": "2026-07-11T11:00:00+00:00"},
        ],
    }
    assert home_attention.classify_comment_follow_up(unknown_staff) == "uncertain"

    proven_staff = {
        **unknown_staff,
        "submission_comments": [
            unknown_staff["submission_comments"][0],
            {
                "author_id": "synthetic-staff-3",
                "author_role": "teacher",
                "created_at": "2026-07-11T11:00:00+00:00",
            },
        ],
    }
    assert home_attention.classify_comment_follow_up(proven_staff) == "none"

    malformed = {
        **base,
        "submission_comments": [
            {"created_at": "2026-07-11T10:00:00+00:00"},
        ],
    }
    assert home_attention.classify_comment_follow_up(malformed) == "none"

    ambiguous_order = {
        **base,
        "submission_comments": [
            {"author_id": "synthetic-student-1", "created_at": "2026-07-11T10:00:00+00:00"},
            {"author_id": "synthetic-staff-4", "created_at": "2026-07-11T10:00:00+00:00"},
        ],
    }
    assert home_attention.classify_comment_follow_up(ambiguous_order) == "none"


def test_roster_warning_provider_aggregates_without_writing_vault(monkeypatch):
    monkeypatch.setattr(roster_warnings.config, "get_roster_group_scheme", lambda course_id: {"selected_group_category_id": "cat-1"})
    monkeypatch.setattr(roster_warnings.config, "get_extra_time", lambda course_id: [{"id": "u-1", "days": 0}])
    monkeypatch.setattr(roster_warnings, "_vault_context", lambda: ({}, set(), {"literary": [], "dup_first": [], "common_word": []}))

    def fake_get(path, params=None, timeout=None):
        if path.endswith("/users"):
            return [{"id": "u-1"}], None
        return [], None

    reads = _reads(fake_get)
    findings = roster_warnings.scan_course(
        "course-1", now="2026-07-11T12:00:00+00:00", reads=reads,
    )
    kinds = {item["kind"] for item in findings}
    assert kinds == {"roster.warning"}
    assert sum(item["counts"]["affected"] for item in findings) == 3
