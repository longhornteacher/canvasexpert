"""Focused route tests for the split Gradebook backend."""

from fastapi.testclient import TestClient

from api.webui.server import app
import api.webui.routes.gradebook as gradebook
import api.webui.routes.gradebook_snapshot as gradebook_snapshot

client = TestClient(app)


def test_gradebook_facade_reexports_snapshot_route():
    assert gradebook.api_gradebook is gradebook_snapshot.api_gradebook
    assert gradebook.router is not None
    # Slice 00c: the broken gradebook sweep helper is deleted; the
    # single sweep-compute owner is the operation-ledger sweep adapter.
    assert not hasattr(gradebook, "_sweep_compute")


def test_gradebook_snapshot_route_aggregates_mocked_canvas_data(monkeypatch):
    monkeypatch.setattr(
        gradebook_snapshot,
        "_course_students",
        lambda course_id: ([{
            "id": 1,
            "name": "Learner One",
            "sortable_name": "One, Learner",
        }, {
            "id": 2,
            "name": "Learner Two",
            "sortable_name": "Two, Learner",
        }], None),
    )
    monkeypatch.setattr(
        gradebook_snapshot,
        "_course_assignments",
        lambda course_id: ([{
            "id": 10,
            "name": "Quiz 1",
            "due_at": "2026-07-01T23:59:00Z",
            "points_possible": 10,
            "html_url": "https://example.invalid/quiz-1",
            "published": True,
        }, {
            "id": 11,
            "name": "Hidden draft",
            "published": False,
        }], None),
    )
    monkeypatch.setattr(
        gradebook_snapshot,
        "_course_submissions",
        lambda course_id: ([{
            "assignment_id": 10,
            "user_id": 1,
            "workflow_state": "graded",
            "score": 9,
            "submitted_at": "2026-07-01T20:00:00Z",
        }, {
            "assignment_id": 10,
            "user_id": 2,
            "workflow_state": "submitted",
            "submitted_at": "2026-07-01T21:00:00Z",
        }], None),
    )

    resp = client.get("/api/gradebook?course_id=42")
    assert resp.status_code == 200
    data = resp.json()

    assert data["ok"] is True
    assert data["class_avg"] == 90.0
    assert data["student_count"] == 2
    assert data["total_missing"] == 0
    assert data["total_ungraded"] == 1
    assert data["assignments"] == [{
        "id": "10",
        "name": "Quiz 1",
        "due_at": "2026-07-01",
        "points": 10,
        "html_url": "https://example.invalid/quiz-1",
        "submitted": 2,
        "graded": 1,
        "ungraded": 1,
        "partially_scored": 0,
        "late_ungraded": 0,
        "missing": 0,
        "late": 0,
        "avg_pct": 90,
        "family_role": "ordinary",
        "family_title": "",
        "bridge_assignment_id": "",
        "source_assignment_ids": [],
    }]
    assert data["students"] == [
        {"name": "One, Learner", "missing": 0, "late": 0, "ungraded": 0, "pct": 90.0},
        {"name": "Two, Learner", "missing": 0, "late": 0, "ungraded": 1, "pct": None},
    ]
