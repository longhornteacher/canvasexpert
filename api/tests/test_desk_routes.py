"""Focused route/render contracts for the slice-07 Desk surface."""

import re
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from api.webui.server import app
from api.webui.routes import pages
from api.work_registry.models import material_version, stable_fingerprint
from api.work_registry.providers import finding


def _client():
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _configure(monkeypatch):
    monkeypatch.setattr(pages.config, "token_is_set", lambda: True)
    monkeypatch.setattr(pages.config, "get_canvas_base", lambda: "https://canvas.example.test")
    monkeypatch.setattr(pages.workspace, "workspace_root", lambda: None)
    monkeypatch.setattr(pages.config, "active_courses", lambda: [
        {"id": "course-1", "name": "Course One", "nickname": "Course One", "active": True},
        {"id": "course-2", "name": "Course Two", "nickname": "Course Two", "active": True},
    ])


def test_desk_empty_render_is_local_and_honest(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [])
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")

    assert response.status_code == 200
    assert 'class="ce-desk-shell"' in response.text
    assert 'id="desk-course-field"' not in response.text
    assert "Active courses:" in response.text
    assert 'href="/settings#current-courses-card"' in response.text
    assert 'id="desk-sync"' in response.text
    assert "Sync now" in response.text
    assert "Checking Canvas sync…" in response.text
    assert "No open work." in response.text
    assert "No items need review." in response.text
    assert "No prepared operations." in response.text
    assert "No receipts." in response.text
    assert "/api/operations" not in response.text
    assert 'name="canvasexpert-csrf-token"' in response.text


def test_desk_readiness_card_names_missing_courses_and_calendar(monkeypatch):
    """Home keeps the concrete active-course and Calendar repairs together."""
    _configure(monkeypatch)
    monkeypatch.setattr(pages.config, "active_courses", lambda: [])
    monkeypatch.setattr(
        pages.deps,
        "load_bell_schedules",
        lambda: ({"bell-a": []}, []),
    )
    monkeypatch.setattr(
        pages.school_calendar,
        "readiness",
        lambda **kwargs: {"status": "unconfigured"},
    )
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [])
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")

    assert response.status_code == 200
    assert "No active courses yet" in response.text
    assert 'href="/settings#current-courses-card"' in response.text
    assert "Calendar and Create" in response.text
    assert "The Calendar school year and dates need to be set up." in response.text
    assert 'href="/calendar#calendar-create-card"' in response.text
    assert "No school calendar configured" not in response.text
    assert "every day counts as instructional" not in response.text


def test_desk_ready_calendar_has_no_warning_and_uses_loaded_bell_schedule_ids(monkeypatch):
    _configure(monkeypatch)
    loaded_ids = {"bell-a": [], "bell-b": []}
    loader_problem = r"C:\private\calendar\bell-schedule.json: unreadable"
    monkeypatch.setattr(
        pages.deps,
        "load_bell_schedules",
        lambda: (loaded_ids, [loader_problem]),
    )
    readiness_calls = []

    def fake_readiness(**kwargs):
        readiness_calls.append(kwargs)
        return {
            "status": "ready",
            "today": {"state": "no_school"},
            "unknown_schedule_dates": [],
        }

    monkeypatch.setattr(pages.school_calendar, "readiness", fake_readiness)
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [])
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")

    assert response.status_code == 200
    assert readiness_calls == [{"bell_schedule_ids": loaded_ids}]
    assert "No active courses yet" not in response.text
    assert "No school calendar configured" not in response.text
    assert "every day counts as instructional" not in response.text
    assert loader_problem not in response.text


@pytest.mark.parametrize(
    ("readiness", "expected_copy", "expected_links"),
    [
        (
            {
                "status": "ready",
                "today": {"state": "outside_coverage"},
                "coverage_position": "before",
                "coverage": {"start": "2026-08-17", "end": "2027-05-28"},
            },
            [],
            [],
        ),
        (
            {"status": "unconfigured"},
            ["The Calendar school year and dates need to be set up."],
            ["/calendar#calendar-create-card"],
        ),
        (
            {"status": "invalid_calendar"},
            ["Calendar could not read the saved school year."],
            ["/calendar#calendar-create-card"],
        ),
        (
            {
                "status": "needs_attention",
                "today": {"state": "outside_coverage"},
                "coverage_position": "after",
                "unknown_schedule_dates": [],
                "remaining_coverage_days": 0,
                "coverage": {"end": "2026-07-31"},
            },
            ["Calendar does not cover today.", "Extend or replace the school year"],
            ["/calendar#calendar-create-card"],
        ),
        (
            {
                "status": "needs_attention",
                "today": {"state": "no_school"},
                "unknown_schedule_dates": ["2026-09-02", "2026-09-03"],
                "remaining_coverage_days": 120,
                "coverage": {"end": "2027-06-01"},
            },
            [
                "Calendar references 2 dates with an unavailable Bell Schedule.",
                "Restore or add the schedule",
                "Update the affected dates",
            ],
            ["/calendar#calendar-bell-card", "/calendar#calendar-change-card"],
        ),
        (
            {
                "status": "needs_attention",
                "today": {"state": "no_school"},
                "coverage_position": "within",
                "unknown_schedule_dates": [],
                "remaining_coverage_days": 12,
                "coverage": {"end": "2026-08-20"},
            },
            [
                "Calendar coverage ends on 2026-08-20.",
                "Extend or replace the school year",
            ],
            ["/calendar#calendar-create-card"],
        ),
        (
            {
                "status": "needs_attention",
                "today": {"state": "no_school"},
                "coverage_position": "within",
                "unknown_schedule_dates": ["2026-09-02"],
                "remaining_coverage_days": 12,
                "coverage": {"end": "2026-08-20"},
            },
            [
                "Calendar references 1 date with an unavailable Bell Schedule.",
                "Calendar coverage ends on 2026-08-20.",
            ],
            [
                "/calendar#calendar-bell-card",
                "/calendar#calendar-change-card",
                "/calendar#calendar-create-card",
            ],
        ),
    ],
)
def test_desk_calendar_warning_mapping(monkeypatch, readiness, expected_copy, expected_links):
    _configure(monkeypatch)
    monkeypatch.setattr(
        pages.deps,
        "load_bell_schedules",
        lambda: ({"bell-a": []}, [r"C:\private\calendar\bell.json"]),
    )
    monkeypatch.setattr(pages.school_calendar, "readiness", lambda **kwargs: readiness)
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [])
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")

    assert response.status_code == 200
    for copy in expected_copy:
        assert copy in response.text
    for link in expected_links:
        assert f'href="{link}"' in response.text
    assert "No school calendar configured" not in response.text
    assert "every day counts as instructional" not in response.text
    assert "School Schedule" not in response.text


def test_desk_outside_coverage_does_not_duplicate_low_coverage_warning(monkeypatch):
    _configure(monkeypatch)
    readiness = {
        "status": "needs_attention",
        "today": {"state": "outside_coverage"},
        "coverage_position": "after",
        "unknown_schedule_dates": [],
        "remaining_coverage_days": 12,
        "coverage": {"end": "2026-08-20"},
    }
    monkeypatch.setattr(pages.deps, "load_bell_schedules", lambda: ({"bell-a": []}, []))
    monkeypatch.setattr(pages.school_calendar, "readiness", lambda **kwargs: readiness)
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [])
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")

    assert response.status_code == 200
    assert "Calendar does not cover today." in response.text
    assert "Calendar coverage ends on 2026-08-20." not in response.text


def test_desk_future_unknown_schedule_omits_expired_calendar_repair(monkeypatch):
    _configure(monkeypatch)
    readiness = {
        "status": "needs_attention",
        "today": {"state": "outside_coverage"},
        "coverage_position": "before",
        "unknown_schedule_dates": ["2026-08-17"],
        "remaining_coverage_days": 288,
        "coverage": {"start": "2026-08-17", "end": "2027-05-28"},
    }
    monkeypatch.setattr(pages.deps, "load_bell_schedules", lambda: ({"bell-a": []}, []))
    monkeypatch.setattr(pages.school_calendar, "readiness", lambda **kwargs: readiness)
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [])
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")

    assert response.status_code == 200
    assert "Calendar references 1 date with an unavailable Bell Schedule." in response.text
    assert "Calendar does not cover today." not in response.text
    assert "Calendar coverage ends" not in response.text


def test_desk_active_courses_use_teacher_names_saved_order_and_empty_state(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(pages.config, "active_courses", lambda: [
        {"id": "fictional-1", "name": "Course One", "nickname": "Athletics 7"},
        {"id": "fictional-2", "name": "Course Two", "nickname": "ELA 7"},
        {"id": "fictional-3", "name": "Course Three", "nickname": "ELA 7 PAP"},
        {"id": "fictional-4", "name": "Course Four", "nickname": "Intro <to> CS"},
    ])
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [])
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")
    course_line = re.search(
        r'<p class="ce-desk-active-courses">(.*?)</p>', response.text, re.DOTALL
    ).group(1)

    assert course_line.index("Athletics 7") < course_line.index("ELA 7")
    assert course_line.index("ELA 7") < course_line.index("ELA 7 PAP")
    assert course_line.index("ELA 7 PAP") < course_line.index("Intro &lt;to&gt; CS")
    assert "fictional-1" not in course_line
    assert "fictional-4" not in course_line

    monkeypatch.setattr(pages.config, "active_courses", lambda: [])
    empty_response = _client().get("/")
    assert "Active courses:" in empty_response.text
    assert "None selected" in empty_response.text


def test_student_reports_is_a_view_inside_the_students_page(monkeypatch):
    _configure(monkeypatch)

    old_tab = _client().get("/course-expert?tab=students", follow_redirects=False)
    old_page = _client().get("/students/reports", follow_redirects=False)
    roster = _client().get("/roster")
    create = _client().get("/course-expert?tab=assignment")

    # Both old entry points land on the Students page's reports view.
    assert old_tab.status_code == 307
    assert old_tab.headers["location"] == "/roster?focus=reports"
    assert old_page.status_code == 307
    assert old_page.headers["location"] == "/roster?focus=reports"

    # The reports controls and scripts live on the Students page itself.
    assert roster.status_code == 200
    for control in ("sr-course", "sr-student", "sr-generate", "nqp-file", "mp-generate"):
        assert f'id="{control}"' in roster.text
    assert "/static/course_expert/student_reports.js" in roster.text
    assert "/static/course_expert/portfolio.js" in roster.text
    assert 'data-lens="reports"' in roster.text
    assert 'nav-section-manage' in roster.text

    # Create keeps its own tabs and none of the report scripts.
    assert create.status_code == 200
    assert 'id="ce-tab-assignment"' in create.text
    assert "/static/course_expert/student_reports.js" not in create.text
    assert "/static/course_expert/portfolio.js" not in create.text


def test_desk_populated_render_uses_registry_and_real_receipt_projection(monkeypatch):
    _configure(monkeypatch)
    job = finding(
        kind="grade.debt",
        course_id="course-1",
        assignment_id="assignment-1",
        counts={"total": 2, "pending": 1, "affected": 1},
        now="2026-07-11T12:00:00+00:00",
        resumable_url="/gradebook",
    )
    receipt = {
        "receipt_id": "receipt-1",
        "kind": "gradebook.sweep",
        "status": "partial",
        "target_count": 1,
        "detail_url": "/api/receipts/receipt-1",
    }
    operations = [
        {
            "operation_id": "operation-private-id",
            "kind": "content.page",
            "status": "prepared",
            "target_count": 2,
        },
        {
            "operation_id": "operation-reviewed-id",
            "kind": "content.quiz",
            "status": "reviewed",
            "target_count": 1,
        },
        {
            "operation_id": "operation-applied-id",
            "kind": "content.page",
            "status": "applied",
            "target_count": 3,
        },
    ]
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: [job])
    monkeypatch.setattr(
        pages.work_routes, "_finding_assignment_names",
        lambda jobs: {("course-1", "assignment-1"): "Reflection Draft"},
    )
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: operations)
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [receipt])

    response = _client().get("/")

    assert response.status_code == 200
    assert "Reflection Draft" in response.text
    assert "1 submission awaiting grading" in response.text
    assert "gradebook.sweep" in response.text
    assert "/api/receipts/receipt-1" in response.text
    assert "Course One" in response.text
    assert "content.page" in response.text
    assert "prepared · 2 targets" in response.text
    assert "content.quiz" in response.text
    assert "reviewed · 1 target" in response.text
    assert "operation-private-id" not in response.text
    assert "operation-reviewed-id" not in response.text
    assert "operation-applied-id" not in response.text


def test_desk_home_attention_render_is_aggregate_only(monkeypatch):
    _configure(monkeypatch)
    jobs = [
        finding(
            kind="grade.followup", course_id="course-1", assignment_id="assignment-1",
            counts={"total": 2, "pending": 2, "affected": 2},
            now="2026-07-11T12:00:00+00:00", resumable_url="/gradebook",
        ),
        finding(
            kind="grade.staff_check", course_id="course-1", assignment_id="assignment-2",
            counts={"total": 1, "pending": 1, "affected": 1},
            now="2026-07-11T12:00:00+00:00", resumable_url="/gradebook",
        ),
    ]
    monkeypatch.setattr(pages.work_routes, "_section_jobs", lambda section: jobs)
    monkeypatch.setattr(pages.work_routes, "_finding_assignment_names", lambda jobs: {
        ("course-1", "assignment-1"): "Reflection One",
        ("course-1", "assignment-2"): "Reflection Two",
    })
    monkeypatch.setattr(pages.operation_store, "list_operations_pii_minimized", lambda: [])
    monkeypatch.setattr(pages.receipt_store, "list_receipts", lambda: [])

    response = _client().get("/")

    assert response.status_code == 200
    # Cards are titled by assignment name; the aggregate counts stay intact.
    for text in (
        "Reflection One", "2 responses need a human check",
        "Reflection Two", "1 response needs a staff response check",
    ):
        assert text in response.text
    assert "synthetic-student" not in response.text
    assert "submission_comments" not in response.text
