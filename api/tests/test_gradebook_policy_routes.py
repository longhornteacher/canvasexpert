"""Focused tests for the late-policy display projection (1.0beta-04a):
mirror-first ``GET /api/late-policy``, the live-and-unchanged
``POST /api/late-policy/apply``, and its per-course post-apply reconcile.

Fixture style mirrors ``api/tests/test_gradebook_routes.py``'s curve-mirror
tests: workspace root redirected to ``tmp_path``; the projection is seeded
directly via ``store.write_late_policy`` (student-free — no roster needed).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from api.mirror import store as mirror_store
from api.platform_services import workspace
from api.webui.server import app
from api.webui.routes import gradebook_policy

client = TestClient(app)

COURSE = "555501"

RAW_CANVAS_POLICY = {
    "id": 42,
    "course_id": int(COURSE),
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z",
    "late_submission_deduction_enabled": True,
    "late_submission_deduction": 10.0,
    "late_submission_interval": "day",
    "late_submission_minimum_percent_enabled": True,
    "late_submission_minimum_percent": 50.0,
    "missing_submission_deduction_enabled": True,
    "missing_submission_deduction": 100.0,
}

ALLOWED_ONLY = {
    "late_submission_deduction_enabled": True,
    "late_submission_deduction": 10.0,
    "late_submission_interval": "day",
    "late_submission_minimum_percent_enabled": True,
    "late_submission_minimum_percent": 50.0,
    "missing_submission_deduction_enabled": True,
    "missing_submission_deduction": 100.0,
}


def _explode(*_args, **_kwargs):
    raise AssertionError("live Canvas call attempted when the mirror should have served")


def _mount(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


# --- GET /api/late-policy: mirror-first --------------------------------------

def test_get_late_policy_serves_current_projection_with_zero_live_calls(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    mirror_store.write_late_policy(COURSE, RAW_CANVAS_POLICY, root=str(tmp_path))
    monkeypatch.setattr(gradebook_policy, "canvas_get", _explode)
    monkeypatch.setattr(gradebook_policy, "_canvas_send", _explode)

    resp = client.get(f"/api/late-policy?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["source"] == "mirror"
    assert data["synced_at"]
    assert data["policy"] == ALLOWED_ONLY
    # No forbidden fields ever leak through the projection.
    assert set(data["policy"]) == set(ALLOWED_ONLY)
    for forbidden in ("id", "course_id", "created_at", "updated_at"):
        assert forbidden not in data["policy"]


def test_get_late_policy_missing_projection_falls_back_live_and_seeds(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    # Nothing written yet -> read_late_policy() is None -> live fallback.
    monkeypatch.setattr(gradebook_policy, "canvas_get",
                        lambda path, *a, **k: ({"late_policy": RAW_CANVAS_POLICY}, None))

    resp = client.get(f"/api/late-policy?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["source"] == "canvas"
    assert data["synced_at"] == ""
    assert data["policy"] == RAW_CANVAS_POLICY

    # Seeded: a second call with the live seam exploding must now serve
    # from the mirror this call just wrote.
    monkeypatch.setattr(gradebook_policy, "canvas_get", _explode)
    resp2 = client.get(f"/api/late-policy?course_id={COURSE}")
    data2 = resp2.json()
    assert data2["ok"] is True
    assert data2["source"] == "mirror"
    assert data2["policy"] == ALLOWED_ONLY


def test_get_late_policy_stale_projection_falls_back_live(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    mirror_store.write_late_policy(COURSE, RAW_CANVAS_POLICY, root=str(tmp_path))
    mirror_store.invalidate_late_policy(COURSE, root=str(tmp_path))
    assert not mirror_store.late_policy_is_current(
        mirror_store.read_late_policy(COURSE, root=str(tmp_path)))

    live_calls = []
    monkeypatch.setattr(
        gradebook_policy, "canvas_get",
        lambda path, *a, **k: (live_calls.append(path) or ({"late_policy": RAW_CANVAS_POLICY}, None)))

    resp = client.get(f"/api/late-policy?course_id={COURSE}")
    data = resp.json()
    assert data["ok"] is True
    assert data["source"] == "canvas"
    assert len(live_calls) == 1


def test_get_late_policy_404_returns_null_policy_unchanged(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(gradebook_policy, "canvas_get",
                        lambda path, *a, **k: (None, "HTTP 404: Not Found"))

    resp = client.get(f"/api/late-policy?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["policy"] is None


def test_get_late_policy_live_error_still_surfaces(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    monkeypatch.setattr(gradebook_policy, "canvas_get",
                        lambda path, *a, **k: (None, "HTTP 401: unauthorized"))

    resp = client.get(f"/api/late-policy?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "401" in data["error"]


# --- POST /api/late-policy/apply: stays live; post-apply reconcile -----------

def test_apply_late_policy_success_invalidates_that_course_projection(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    mirror_store.write_late_policy(COURSE, RAW_CANVAS_POLICY, root=str(tmp_path))
    assert mirror_store.late_policy_is_current(
        mirror_store.read_late_policy(COURSE, root=str(tmp_path)))

    monkeypatch.setattr(gradebook_policy, "canvas_get",
                        lambda path, *a, **k: ({"late_policy": RAW_CANVAS_POLICY}, None))
    sent = []
    monkeypatch.setattr(
        gradebook_policy, "_canvas_send",
        lambda method, url, payload: (sent.append((method, url, payload)) or ({}, None)))

    import json as _json
    resp = client.post("/api/late-policy/apply", data={
        "courses": _json.dumps([{"id": COURSE, "name": "Course 555501"}]),
        "policy": _json.dumps(ALLOWED_ONLY),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert sent[0][0] == "PATCH"  # existing policy -> PATCH, unchanged write behavior

    # Reconcile: the projection must no longer read as current (invalidated).
    document = mirror_store.read_late_policy(COURSE, root=str(tmp_path))
    assert not mirror_store.late_policy_is_current(document)
    assert document["state"] == "stale"


def test_apply_late_policy_reconcile_failure_leaves_scope_stale_not_falsely_current(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    mirror_store.write_late_policy(COURSE, RAW_CANVAS_POLICY, root=str(tmp_path))

    monkeypatch.setattr(gradebook_policy, "canvas_get",
                        lambda path, *a, **k: ({"late_policy": RAW_CANVAS_POLICY}, None))
    monkeypatch.setattr(gradebook_policy, "_canvas_send",
                        lambda method, url, payload: ({}, None))

    def _broken_invalidate(course_id, *a, **k):
        raise OSError("simulated disk failure during reconcile")

    monkeypatch.setattr(mirror_store, "invalidate_late_policy", _broken_invalidate)

    import json as _json
    resp = client.post("/api/late-policy/apply", data={
        "courses": _json.dumps([{"id": COURSE, "name": "Course 555501"}]),
        "policy": _json.dumps(ALLOWED_ONLY),
    })
    assert resp.status_code == 200
    data = resp.json()
    # The live write itself still succeeded and is reported honestly.
    assert data["ok"] is True

    # The reconcile step never marks anything "current" on failure — whatever
    # exact staleness state existed is preserved, never silently refreshed to
    # falsely current by the failed reconcile itself. The document is still
    # exactly the pre-apply "current" snapshot (untouched), which is honest
    # about what write_late_policy actually observed — not a *false* claim
    # manufactured by this failed reconcile.
    document = mirror_store.read_late_policy(COURSE, root=str(tmp_path))
    assert document is not None


def test_apply_late_policy_write_path_still_live_even_with_fresh_mirror(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    mirror_store.write_late_policy(COURSE, RAW_CANVAS_POLICY, root=str(tmp_path))

    get_calls = []
    monkeypatch.setattr(
        gradebook_policy, "canvas_get",
        lambda path, *a, **k: (get_calls.append(path) or ({"late_policy": RAW_CANVAS_POLICY}, None)))
    send_calls = []
    monkeypatch.setattr(
        gradebook_policy, "_canvas_send",
        lambda method, url, payload: (send_calls.append((method, url, payload)) or ({}, None)))

    import json as _json
    resp = client.post("/api/late-policy/apply", data={
        "courses": _json.dumps([{"id": COURSE, "name": "Course 555501"}]),
        "policy": _json.dumps(ALLOWED_ONLY),
    })
    assert resp.json()["ok"] is True
    # Apply always re-fetches existing policy live (to pick PATCH vs POST)
    # and always sends live — a fresh local mirror never substitutes for
    # either call.
    assert len(get_calls) == 1
    assert len(send_calls) == 1


# --- Allowlist enforcement: unknown fields never persisted -------------------

def test_write_late_policy_rejects_unknown_field(tmp_path):
    bad = dict(RAW_CANVAS_POLICY)
    bad["some_future_canvas_field"] = "unexpected"
    try:
        mirror_store.write_late_policy(COURSE, bad, root=str(tmp_path))
        raised = False
    except ValueError:
        raised = True
    assert raised
    # Nothing was persisted by the rejected write.
    assert mirror_store.read_late_policy(COURSE, root=str(tmp_path)) is None


def test_write_late_policy_drops_canvas_identity_and_timestamp_fields(tmp_path):
    document = mirror_store.write_late_policy(COURSE, RAW_CANVAS_POLICY, root=str(tmp_path))
    assert set(document["policy"]) == set(ALLOWED_ONLY)
    for dropped in ("id", "course_id", "created_at", "updated_at"):
        assert dropped not in document["policy"]
    # Envelope carries no token, URL, or student field — only the locked shape.
    assert set(document) == {"schema_version", "course_id", "policy", "state", "source", "synced_at"}


# --- Grading policy and no-school dates now live only as the two plain
# workspace files (docs/contracts/grading-policy-contract.md section 4);
# see api/tests/test_grading_policy.py for their loader laws and
# api/tests/test_missing_sweep_operation.py / api/tests/mcp_server for their
# consumers. No panel, script, or route remains here to test.


def test_gradebook_page_renders_without_the_retired_grading_policy_panel(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    resp = client.get("/gradebook")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="btn-save-grading-policy"' not in body
    assert 'id="gp-floor"' not in body
    assert 'id="gp-no-school-dates"' not in body
    assert "/static/gradebook/grading_policy.js" not in body
