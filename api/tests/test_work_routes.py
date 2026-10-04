import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.webui.local_request_guard import csrf_token
from api.webui.server import app
from api.webui.routes import work
from api.webui.routes import receipts as receipt_routes
from api.work_registry import adapters
from api.work_registry.models import material_version, stable_fingerprint
from api.work_registry.providers import finding


API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _current_course_scope(monkeypatch):
    monkeypatch.setattr(work.config, "active_courses", lambda: [{
        "id": "course-1", "name": "Fictional Course", "nickname": "Fictional Course",
    }])


def _job(origin="intentional", status="attention"):
    source = {"type": "workspace_relative", "value": "Assignments/sample.txt"}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "job_id": "job-route-1",
        "fingerprint": stable_fingerprint("create.assignment", source, ["course-1"], "assignment-1"),
        "material_version": material_version({"status": status, "counts": {"total": 1, "pending": 1, "affected": 0}}),
        "origin": origin,
        "kind": "create.assignment",
        "status": status,
        "title": "Assignment draft",
        "description": "Authored assignment",
        "course_ids": ["course-1"],
        "focused_course_id": "course-1",
        "assignment_id": "assignment-1",
        "resumable_url": "/course-expert",
        "source_ref": source,
        "counts": {"total": 1, "pending": 1, "affected": 0},
        "attention_reason": "Work needs attention" if status == "attention" else "",
        "created_at": now,
        "updated_at": now,
        "completed_at": "",
    }


def _client():
    return TestClient(app, base_url="http://127.0.0.1:8765")




def test_get_work_is_local_pii_free_and_rejects_unknown_section(monkeypatch):
    job = _job()
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [job])
    monkeypatch.setattr(work.adapters, "collect_start_sources", lambda: [{"kind": "create.assignment", "title": "Assignment source", "path": "Assignments/sample.txt"}])
    response = _client().get("/api/work?section=attention")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    projected_job = next(item for item in payload["jobs"] if item["job_id"] == "job-route-1")
    assert projected_job["title"] == "Create work"
    assert set(projected_job) == set(_job())
    assert set(payload["presentations"]["job-route-1"]) == {
        "course_label", "title", "summary", "action_label",
    }
    assert "student_id" not in json.dumps(payload).lower()
    assert "C:\\" not in json.dumps(payload)
    assert _client().get("/api/work?section=unknown").status_code == 400


def test_receipt_landing_renders_summary_and_routes_by_subject(monkeypatch):
    summaries = [
        {
            "receipt_id": "op_receipt_1", "subject_type": "operation",
            "subject_id": "operation-1", "kind": "assignment.create",
            "status": "partial", "attempted_at": "2026-09-20T12:00:00Z",
            "completed_at": "2026-09-20T12:01:00Z", "target_count": 2,
        },
        {
            "receipt_id": "routine_receipt_1", "subject_type": "routine",
            "subject_id": "download", "kind": "routine.run",
            "status": "applied", "attempted_at": "2026-09-20T13:00:00Z",
            "completed_at": "2026-09-20T13:01:00Z", "target_count": 4,
        },
    ]
    monkeypatch.setattr(receipt_routes.receipts, "list_receipts", lambda: summaries)
    monkeypatch.setattr(
        receipt_routes.receipts, "get_receipt",
        lambda receipt_id: (_ for _ in ()).throw(AssertionError("HTML opened private detail")),
    )
    client = _client()

    operation = client.get("/receipts/op_receipt_1")
    assert operation.status_code == 200
    assert "Partial" in operation.text
    assert "Assignment Create" in operation.text
    assert "operation-1" in operation.text
    assert "2" in operation.text
    assert 'href="/course-expert#ce-operations-list"' in operation.text
    create_page = client.get("/course-expert")
    assert create_page.status_code == 200
    assert 'id="ce-operations-list" class="ce-operations-list" tabindex="-1"' in create_page.text

    routine = client.get("/receipts/routine_receipt_1")
    assert routine.status_code == 200
    assert 'href="/"' in routine.text
    assert "download" in routine.text

    missing = client.get("/receipts/missing")
    assert missing.status_code == 404
    assert "Receipt not found" in missing.text


def test_detected_grading_and_home_cards_use_current_surfaces(monkeypatch):
    debt = finding(
        kind="grade.debt", course_id="course-1", assignment_id="assignment-debt",
        counts={"total": 5, "pending": 3, "affected": 3},
        now="2026-07-11T12:00:00+00:00", resumable_url="/course?course_id=course-1",
    )
    roster = finding(
        kind="roster.warning", course_id="course-1", assignment_id="",
        counts={"total": 4, "pending": 4, "affected": 4},
        now="2026-07-11T12:00:00+00:00", resumable_url="/roster",
    )
    follow_up = finding(
        kind="grade.followup", course_id="course-1", assignment_id="assignment-follow-up",
        counts={"total": 2, "pending": 2, "affected": 2},
        now="2026-07-11T12:00:00+00:00", resumable_url="/course?course_id=course-1",
    )
    staff_check = finding(
        kind="grade.staff_check", course_id="course-1", assignment_id="assignment-staff-check",
        counts={"total": 1, "pending": 1, "affected": 1},
        now="2026-07-11T12:00:00+00:00", resumable_url="/course?course_id=course-1",
    )
    monkeypatch.setattr(
        work.adapters, "collect_local_jobs", lambda: [debt, roster, follow_up, staff_check]
    )
    # Detected findings are relabeled with the mirror-resolved assignment name.
    assignment_names = {
        "assignment-debt": "Debt Essay",
        "assignment-late": "Late Lab",
        "assignment-follow-up": "Follow-up Reflection",
        "assignment-staff-check": "Staff Check Task",
    }
    monkeypatch.setattr(
        "api.mirror.queries.course_assignments",
        lambda course_id, **kwargs: (
            [{"id": aid, "name": name} for aid, name in assignment_names.items()], None
        ) if course_id == "course-1" else (None, "unavailable"),
    )

    payload = _client().get("/api/work?section=all").json()
    presentations = payload["presentations"]
    by_kind = {job["kind"]: presentations[job["job_id"]] for job in payload["jobs"]}
    # The title is now the assignment name; the aggregate summary is unchanged.
    assert by_kind["grade.debt"]["title"] == "Debt Essay"
    assert by_kind["grade.debt"]["summary"] == "3 submissions awaiting grading"
    assert by_kind["grade.debt"]["action_label"] == "Open course"
    # roster.warning is course-level (no assignment) and keeps its aggregate title.
    assert by_kind["roster.warning"]["title"] == "Roster attention"
    assert by_kind["roster.warning"]["summary"] == "4 roster issues need review"
    assert by_kind["roster.warning"]["action_label"] == "Open Roster"
    assert by_kind["grade.followup"] == {
        "course_label": "Fictional Course",
        "title": "Follow-up Reflection",
        "summary": "2 responses need a human check",
        "action_label": "Open course",
    }
    assert by_kind["grade.staff_check"] == {
        "course_label": "Fictional Course",
        "title": "Staff Check Task",
        "summary": "1 response needs a staff response check",
        "action_label": "Open course",
    }
    serialized = json.dumps(payload)
    assert "/powergrader" not in serialized


def test_work_projection_keeps_global_and_current_jobs_but_hides_previous(monkeypatch):
    current = _job()
    previous = _job()
    previous.update({"job_id": "job-previous", "course_ids": ["course-previous"]})
    global_job = _job()
    global_job.update({"job_id": "job-global", "course_ids": [], "focused_course_id": ""})
    monkeypatch.setattr(work.storage, "read_registry", lambda: {"jobs": []})
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [current, previous, global_job])

    jobs = work._all_jobs()

    assert {job["job_id"] for job in jobs} == {"job-route-1", "job-global"}
    assert work._find_current("job-previous") is None










def test_retired_scoring_work_is_hidden_and_current_grading_opens_course(monkeypatch):
    debt = finding(
        kind="grade.debt", course_id="course-1", assignment_id="assignment-debt",
        counts={"total": 5, "pending": 3, "affected": 3},
        now="2026-07-11T12:00:00+00:00", resumable_url="/course?course_id=course-1",
    )
    retired = finding(
        kind="grade.powergrader_ready", course_id="course-1", assignment_id="assignment-ready",
        counts={"total": 3, "pending": 3, "affected": 3},
        now="2026-07-11T12:00:00+00:00", resumable_url="/powergrader",
    )
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [debt, retired])
    monkeypatch.setattr(
        "api.mirror.queries.course_assignments",
        lambda course_id, **kwargs: (
            [
                {"id": "assignment-debt", "name": "Chapter 5 Essay"},
                {"id": "assignment-ready", "name": "Reflection Journal"},
            ], None,
        ) if course_id == "course-1" else (None, "unavailable"),
    )

    payload = _client().get("/api/work?section=all").json()
    by_kind = {job["kind"]: payload["presentations"][job["job_id"]] for job in payload["jobs"]}

    assert by_kind["grade.debt"]["title"] == "Chapter 5 Essay"
    assert by_kind["grade.debt"]["summary"] == "3 submissions awaiting grading"
    assert set(by_kind) == {"grade.debt"}
    assert payload["jobs"][0]["resumable_url"] == "/course?course_id=course-1"
