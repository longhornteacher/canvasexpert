import json
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


def test_launcher_rendered_csrf_authorizes_stubbed_scan():
    script = textwrap.dedent(
        """
        from html.parser import HTMLParser

        from fastapi.testclient import TestClient

        from api.webui import server
        from api.webui.routes import pages, work


        class CsrfMetaParser(HTMLParser):
            token = None

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "meta" and values.get("name") == "canvasexpert-csrf-token":
                    self.token = values.get("content")


        server.config.token_is_set = lambda: True
        server.config.get_canvas_base = lambda: "https://canvas.invalid"
        server.config.active_courses = lambda: []
        pages.work_routes._section_jobs = lambda section: []
        pages.operation_store.list_operations_pii_minimized = lambda: []
        pages.receipt_store.list_receipts = lambda: []
        pages.workspace.workspace_root = lambda: None

        work.storage.workspace.workspace_root = lambda: "stubbed-workspace"
        work.discovery.scan_active_courses = lambda: {
            "ok": True,
            "partial": False,
            "courses_scanned": 0,
            "findings": 0,
            "stale_course_ids": [],
            "error_codes": [],
            "courses": {},
        }
        work._merge_discovery = lambda result: {"ok": True}

        client = TestClient(server.app, base_url="http://127.0.0.1:8765")
        rendered = client.get("/", headers={"Accept": "text/html"})
        assert rendered.status_code == 200
        parser = CsrfMetaParser()
        parser.feed(rendered.text)
        assert parser.token

        origin = "http://127.0.0.1:8765"
        rejected = client.post(
            "/api/work/scan",
            headers={"X-CanvasExpert-CSRF": "wrong", "Origin": origin},
        )
        assert rejected.status_code == 403

        scanned = client.post(
            "/api/work/scan",
            headers={"X-CanvasExpert-CSRF": parser.token, "Origin": origin},
        )
        assert scanned.status_code == 200
        assert scanned.json() == {
            "ok": True,
            "partial": False,
            "courses_scanned": 0,
            "findings": 0,
            "stale_course_ids": [],
            "error_codes": [],
        }
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=API_DIR.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


def test_detected_grading_and_home_cards_use_current_surfaces(monkeypatch):
    debt = finding(
        kind="grade.debt", course_id="course-1", assignment_id="assignment-debt",
        counts={"total": 5, "pending": 3, "affected": 3},
        now="2026-07-11T12:00:00+00:00", resumable_url="/gradebook",
    )
    late = finding(
        kind="late.work", course_id="course-1", assignment_id="assignment-late",
        counts={"total": 2, "pending": 2, "affected": 2},
        now="2026-07-11T12:00:00+00:00", resumable_url="/gradebook",
    )
    roster = finding(
        kind="roster.warning", course_id="course-1", assignment_id="",
        counts={"total": 4, "pending": 4, "affected": 4},
        now="2026-07-11T12:00:00+00:00", resumable_url="/roster",
    )
    follow_up = finding(
        kind="grade.followup", course_id="course-1", assignment_id="assignment-follow-up",
        counts={"total": 2, "pending": 2, "affected": 2},
        now="2026-07-11T12:00:00+00:00", resumable_url="/gradebook",
    )
    staff_check = finding(
        kind="grade.staff_check", course_id="course-1", assignment_id="assignment-staff-check",
        counts={"total": 1, "pending": 1, "affected": 1},
        now="2026-07-11T12:00:00+00:00", resumable_url="/gradebook",
    )
    monkeypatch.setattr(
        work.adapters, "collect_local_jobs", lambda: [debt, late, roster, follow_up, staff_check]
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
    assert by_kind["grade.debt"]["action_label"] == "Open Gradebook"
    assert by_kind["late.work"]["title"] == "Late Lab"
    assert by_kind["late.work"]["summary"] == "2 late submissions"
    assert by_kind["late.work"]["action_label"] == "Open Gradebook"
    # roster.warning is course-level (no assignment) and keeps its aggregate title.
    assert by_kind["roster.warning"]["title"] == "Roster attention"
    assert by_kind["roster.warning"]["summary"] == "4 roster issues need review"
    assert by_kind["roster.warning"]["action_label"] == "Open Roster"
    assert by_kind["grade.followup"] == {
        "course_label": "Fictional Course",
        "title": "Follow-up Reflection",
        "summary": "2 responses need a human check",
        "action_label": "Open Gradebook",
    }
    assert by_kind["grade.staff_check"] == {
        "course_label": "Fictional Course",
        "title": "Staff Check Task",
        "summary": "1 response needs a staff response check",
        "action_label": "Open Gradebook",
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


def test_mutation_guard_rejection_matrix_and_valid_same_origin(monkeypatch, tmp_path):
    job = _job()
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [job])
    monkeypatch.setattr(work.adapters, "collect_start_sources", lambda: [])
    monkeypatch.setattr(work.storage.workspace, "workspace_root", lambda: str(tmp_path / "workspace"))
    client = _client()
    body = {"material_version": job["material_version"]}
    assert client.post("/api/work/job-route-1/ignore", json=body).status_code == 403
    assert client.post("/api/work/job-route-1/ignore", json=body, headers={"X-CanvasExpert-CSRF": "wrong"}).status_code == 403
    assert client.post("/api/work/job-route-1/ignore", json=body, headers={"X-CanvasExpert-CSRF": csrf_token(), "Host": "192.0.2.1:8765"}).status_code == 403
    assert client.post("/api/work/job-route-1/ignore", json=body, headers={"X-CanvasExpert-CSRF": csrf_token(), "Origin": "http://evil.invalid:8765"}).status_code == 403
    assert client.post("/api/work/job-route-1/ignore", json=body, headers={"X-CanvasExpert-CSRF": csrf_token(), "Origin": "not-an-origin"}).status_code == 403
    valid = client.post("/api/work/job-route-1/ignore", json=body, headers={"X-CanvasExpert-CSRF": csrf_token(), "Origin": "http://127.0.0.1:8765"})
    assert valid.status_code == 200
    assert valid.json()["job"]["status"] == "ignored"
    assert client.get("/api/work?section=attention").json()["jobs"] == []


def test_stale_and_unknown_mutations_fail_closed(monkeypatch, tmp_path):
    job = _job()
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [job])
    monkeypatch.setattr(work.adapters, "collect_start_sources", lambda: [])
    monkeypatch.setattr(work.storage.workspace, "workspace_root", lambda: str(tmp_path / "workspace"))
    headers = {"X-CanvasExpert-CSRF": csrf_token()}
    stale = {"material_version": "stale"}
    client = _client()
    assert client.post("/api/work/job-route-1/ignore", json=stale, headers=headers).status_code == 409
    assert client.post("/api/work/missing/ignore", json={"material_version": job["material_version"]}, headers=headers).status_code == 409
    assert client.post("/api/work/job-route-1/snooze", json={"material_version": job["material_version"], "until": "not-a-date"}, headers=headers).status_code == 409
    assert not (tmp_path / "workspace" / "_System" / "workbench" / "suppressions.v1.json").exists()


def test_complete_only_intentional_and_no_canvas_calls(monkeypatch, tmp_path):
    detected = _job(origin="detected")
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [detected])
    monkeypatch.setattr(work.adapters, "collect_start_sources", lambda: [])
    monkeypatch.setattr(work.storage.workspace, "workspace_root", lambda: str(tmp_path / "workspace"))
    headers = {"X-CanvasExpert-CSRF": csrf_token()}
    client = _client()
    response = client.post("/api/work/job-route-1/complete", json={"material_version": detected["material_version"]}, headers=headers)
    assert response.status_code == 409

    intentional = _job(origin="intentional", status="in_progress")
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [intentional])
    preview = client.get("/api/work?section=all").json()
    assert preview["presentations"]["job-route-1"]["title"] == "Create work"
    response = client.post("/api/work/job-route-1/complete", json={"material_version": intentional["material_version"]}, headers=headers)
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "completed"
    disk = (tmp_path / "workspace" / "_System" / "workbench" / "registry.v1.json").read_text(encoding="utf-8")
    assert "student" not in disk.lower()
    assert "submission" not in disk.lower()
    assert "presentations" not in disk.lower()
    assert "Fictional Stored Title" not in disk

    refreshed = client.get("/api/work?section=all")
    assert refreshed.status_code == 200
    assert refreshed.json()["jobs"][0]["status"] == "completed"


def test_scan_is_guarded_merges_findings_and_get_stays_local(monkeypatch, tmp_path):
    discovered = finding(
        kind="grade.debt",
        course_id="course-1",
        assignment_id="assignment-1",
        counts={"total": 1, "pending": 1, "affected": 1},
        now="2026-07-11T12:00:00+00:00",
        resumable_url="/powergrader",
    )
    result = {
        "ok": True,
        "partial": False,
        "courses_scanned": 1,
        "findings": 1,
        "stale_course_ids": [],
        "error_codes": [],
        "courses": {
            "course-1": {
                "checked_at": "2026-07-11T12:00:00+00:00",
                "findings": [discovered],
                "stale": False,
                "error_code": "",
            }
        },
    }
    monkeypatch.setattr(work.storage.workspace, "workspace_root", lambda: str(tmp_path / "workspace"))
    monkeypatch.setattr(work.discovery, "scan_active_courses", lambda: result)
    monkeypatch.setattr(work.adapters, "collect_local_jobs", lambda: [])
    monkeypatch.setattr(work.adapters, "collect_start_sources", lambda: [])
    client = _client()

    assert client.post("/api/work/scan").status_code == 403
    response = client.post(
        "/api/work/scan",
        headers={
            "X-CanvasExpert-CSRF": csrf_token(),
            "Origin": "http://127.0.0.1:8765",
        },
    )
    assert response.status_code == 200
    assert response.json()["findings"] == 1

    monkeypatch.setattr(work.discovery, "scan_active_courses", lambda: (_ for _ in ()).throw(AssertionError("GET scanned Canvas")))
    monkeypatch.setattr(
        "api.mirror.queries.course_assignments",
        lambda course_id, **kwargs: ([{"id": "assignment-1", "name": "Scanned Assignment"}], None),
    )
    get_response = client.get("/api/work?section=attention")
    assert get_response.status_code == 200
    assert get_response.json()["jobs"][0]["kind"] == "grade.debt"
    assert get_response.json()["presentations"][get_response.json()["jobs"][0]["job_id"]]["title"] == "Scanned Assignment"


def test_retired_scoring_work_is_hidden_and_current_grading_uses_gradebook(monkeypatch):
    debt = finding(
        kind="grade.debt", course_id="course-1", assignment_id="assignment-debt",
        counts={"total": 5, "pending": 3, "affected": 3},
        now="2026-07-11T12:00:00+00:00", resumable_url="/gradebook",
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
    assert payload["jobs"][0]["resumable_url"] == "/gradebook"
