"""Focused route tests for the split Gradebook backend."""

import json

from fastapi.testclient import TestClient

from api.webui.server import app
import api.webui.routes.gradebook as gradebook
import api.webui.routes.gradebook_curves as gradebook_curves
import api.webui.routes.gradebook_snapshot as gradebook_snapshot
from api.mirror import store as mirror_store
from api.platform_services import workspace
from api.webui import mirror_reads

client = TestClient(app)


def test_gradebook_facade_reexports_snapshot_route():
    assert gradebook.api_gradebook is gradebook_snapshot.api_gradebook
    assert gradebook.router is not None
    # Slice 00c: the broken gradebook_service._sweep_compute is deleted; the
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
    }]
    assert data["students"] == [
        {"name": "One, Learner", "missing": 0, "late": 0, "ungraded": 0, "pct": 90.0},
        {"name": "Two, Learner", "missing": 0, "late": 0, "ungraded": 1, "pct": None},
    ]


def test_curve_list_and_revert_route_shapes_remain_compatible(monkeypatch):
    event = {
        "id": "curve-opaque-1",
        "course_id": "course-opaque-1",
        "assignment_id": "assignment-opaque-1",
        "assignment_name": "Synthetic Assignment",
        "curve_type": "flat_bump",
        "curve_settings": {"bump": 1},
        "applied_at": "2026-07-11T12:00:00",
        "reverted": False,
        "students": [{
            "user_id": "student-opaque-1",
            "student_name": "Synthetic Learner",
            "original_score": 1,
            "curved_score": 2,
        }],
    }
    monkeypatch.setattr(gradebook_curves, "_load_curve_events", lambda: [event.copy()])

    listed = gradebook_curves.list_curve_events("course-opaque-1")
    assert json.loads(listed.body) == {
        "ok": True,
        "events": [{key: value for key, value in event.items() if key != "students"}],
    }

    sent = []
    saved = []
    monkeypatch.setattr(gradebook_curves, "canvas_get", lambda *_: ({"score": 2}, None))
    monkeypatch.setattr(
        gradebook_curves, "_canvas_send",
        lambda method, url, payload: (sent.append((method, url, payload)) or ({}, None)),
    )
    monkeypatch.setattr(
        gradebook_curves, "_save_curve_events",
        lambda events: saved.append(events),
    )

    reverted = gradebook_curves.revert_curve(
        "curve-opaque-1", "course-opaque-1", "[]")
    body = json.loads(reverted.body)
    assert body["ok"] is True
    assert body["results"][0]["reverted_to"] == 1
    assert sent[0][0] == "PUT"
    assert sent[0][1].endswith("/courses/course-opaque-1/assignments/assignment-opaque-1/submissions/student-opaque-1")
    assert saved[0][0]["reverted"] is True


# --- Slice 3: curve_assignments (picker list) is mirror-first; every other
# curve route (preview/apply/revert/events) reads score baselines that feed a
# Canvas write and must stay live no matter how fresh the mirror is. ---------

COURSE_MC = "555301"
ASSIGNMENTS_MC = [
    {"id": 700301, "name": "Mirror Curve Assignment", "due_at": "2026-07-01T23:59:00Z",
     "points_possible": 10, "published": True, "html_url": "u"},
    {"id": 700302, "name": "Unpublished draft", "due_at": "2026-07-02T23:59:00Z",
     "points_possible": 0, "published": False, "html_url": "u"},
]


def _populate_curve_mirror(root, *, fresh=True):
    stamp = mirror_store.now_iso() if fresh else "2020-01-01T00:00:00Z"
    mirror_store.write_assignments(COURSE_MC, ASSIGNMENTS_MC, root=root, attempted_at=stamp)
    mirror_store.record_pass(COURSE_MC, "full", ok=True, attempted_at=stamp, root=root)


def _explode(*_args, **_kwargs):
    raise AssertionError("mirror/live seam invoked when it should not have been")


def test_curve_assignments_serves_fresh_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    _populate_curve_mirror(str(tmp_path))
    monkeypatch.setattr(mirror_reads, "canvas_get_all", _explode)

    resp = client.get(f"/api/curve/assignments?course_id={COURSE_MC}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert [a["id"] for a in data["assignments"]] == ["700301"]


def test_curve_assignments_monkeypatched_seam_still_uses_live_path(monkeypatch, tmp_path):
    # A fresh mirror exists, but the test monkeypatches the seam directly —
    # the route must honor that (existing-test compatibility, locked
    # decision 4) instead of silently detouring through the mirror helper.
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    _populate_curve_mirror(str(tmp_path))
    monkeypatch.setattr(mirror_reads, "assignments_or_live", _explode)

    live_rows = [{"id": 999, "name": "Live-only Assignment", "points_possible": 5,
                  "due_at": "2026-08-01T00:00:00Z", "published": True}]
    monkeypatch.setattr(gradebook_curves, "_course_assignments", lambda course_id: (live_rows, None))

    resp = client.get(f"/api/curve/assignments?course_id={COURSE_MC}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert [a["id"] for a in data["assignments"]] == ["999"]


def test_curve_preview_never_consults_the_mirror(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mirror_reads, "assignments_or_live", _explode)
    monkeypatch.setattr(mirror_reads, "students_or_live", _explode)
    monkeypatch.setattr(mirror_reads, "submissions_or_live", _explode)

    monkeypatch.setattr(gradebook_curves, "_assignment",
                        lambda cid, aid: ({"id": aid, "name": "Quiz", "points_possible": 100}, None))
    monkeypatch.setattr(gradebook_curves, "_course_students",
                        lambda cid: ([{"id": 1, "sortable_name": "Smith, John"}], None))
    monkeypatch.setattr(gradebook_curves, "_assignment_submissions",
                        lambda cid, aid: ([{"user_id": 1, "score": 80, "workflow_state": "graded"}], None))

    resp = client.post("/api/curve/preview", data={
        "course_id": "42", "assignment_id": "10",
        "curve_type": "flat_bump", "settings": json.dumps({"bump": 5}),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["results"][0]["curved_score"] == 85


def test_curve_apply_never_consults_the_mirror(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(mirror_reads, "assignments_or_live", _explode)
    monkeypatch.setattr(mirror_reads, "students_or_live", _explode)
    monkeypatch.setattr(mirror_reads, "submissions_or_live", _explode)

    monkeypatch.setattr(gradebook_curves, "_assignment",
                        lambda cid, aid: ({"id": aid, "name": "Quiz", "points_possible": 100}, None))
    monkeypatch.setattr(gradebook_curves, "_assignment_submissions",
                        lambda cid, aid: ([{"user_id": 1, "score": 80, "workflow_state": "graded"}], None))
    monkeypatch.setattr(gradebook_curves, "_canvas_send", lambda method, url, payload: ({}, None))
    monkeypatch.setattr(gradebook_curves, "_load_curve_events", lambda: [])
    monkeypatch.setattr(gradebook_curves, "_save_curve_events", lambda events: None)
    monkeypatch.setattr(gradebook_curves.mirror_service, "notify_course_changed", lambda course_id: None)

    rows = [{"user_id": "1", "student_name": "Smith, John",
             "original_score": 80, "curved_score": 85, "changed": True}]
    resp = client.post("/api/curve/apply", data={
        "course_id": "42", "assignment_id": "10", "curve_type": "flat_bump",
        "settings": json.dumps({"bump": 5}), "results": json.dumps(rows),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
