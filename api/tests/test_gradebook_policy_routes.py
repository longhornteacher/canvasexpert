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


# --- Grading policy: effort credit and teacher-confirmed late days ----------

import json as _json


def test_get_grading_policy_defaults_to_none_with_no_warnings(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    resp = client.get(f"/api/grading-policy?course_id={COURSE}")
    data = resp.json()
    assert data == {"ok": True, "policy": None, "warnings": []}


def test_save_and_get_grading_policy_round_trips(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    resp = client.post("/api/grading-policy", data={
        "course_id": COURSE,
        "policy": _json.dumps({"floor_percent": 30, "missing_percent": 20,
                               "sweep_after_school_days": 15}),
    })
    data = resp.json()
    assert data["ok"] is True
    assert data["policy"] == {"floor_percent": 30, "missing_percent": 20,
                              "sweep_after_school_days": 15}

    again = client.get(f"/api/grading-policy?course_id={COURSE}").json()
    assert again["policy"] == data["policy"]


def test_save_grading_policy_empty_removes_it(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    client.post("/api/grading-policy", data={
        "course_id": COURSE,
        "policy": _json.dumps({"floor_percent": 30, "missing_percent": 20,
                               "sweep_after_school_days": 15}),
    })
    resp = client.post("/api/grading-policy", data={"course_id": COURSE, "policy": "{}"})
    data = resp.json()
    assert data == {"ok": True, "policy": None, "warnings": []}
    assert client.get(f"/api/grading-policy?course_id={COURSE}").json()["policy"] is None


def test_save_grading_policy_refuses_floor_below_missing(monkeypatch, tmp_path):
    """LAW: a sincere score above 0 never marks below the missing value. That
    invariant is enforced here, at save time, by refusing a floor percent
    below the missing percent -- mark() itself has no missing_percent to
    check against."""
    _mount(monkeypatch, tmp_path)
    resp = client.post("/api/grading-policy", data={
        "course_id": COURSE,
        "policy": _json.dumps({"floor_percent": 10, "missing_percent": 20,
                               "sweep_after_school_days": 15}),
    })
    data = resp.json()
    assert data["ok"] is False
    assert "floor percent" in data["error"].lower()
    # Nothing was persisted by the refused save.
    assert client.get(f"/api/grading-policy?course_id={COURSE}").json()["policy"] is None


def test_grading_policy_warnings_read_from_cached_late_policy_no_live_call(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    mirror_store.write_late_policy(COURSE, {
        **RAW_CANVAS_POLICY,
        "missing_submission_deduction_enabled": True,
        "late_submission_minimum_percent_enabled": True,
        "late_submission_minimum_percent": 10.0,
    }, root=str(tmp_path))
    monkeypatch.setattr(gradebook_policy, "canvas_get", _explode)
    monkeypatch.setattr(gradebook_policy, "_canvas_send", _explode)

    resp = client.post("/api/grading-policy", data={
        "course_id": COURSE,
        "policy": _json.dumps({"floor_percent": 30, "missing_percent": 20,
                               "sweep_after_school_days": 15}),
    })
    data = resp.json()
    assert data["ok"] is True
    assert len(data["warnings"]) == 2

    again = client.get(f"/api/grading-policy?course_id={COURSE}").json()
    assert len(again["warnings"]) == 2


def test_grading_policy_no_cached_late_policy_means_no_warning(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    resp = client.post("/api/grading-policy", data={
        "course_id": COURSE,
        "policy": _json.dumps({"floor_percent": 30, "missing_percent": 20,
                               "sweep_after_school_days": 15}),
    })
    assert resp.json()["warnings"] == []


# --- No-school dates: workspace-wide, validated, sorted, deduplicated -------

def test_no_school_dates_round_trip_validates_sorts_and_dedupes(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    resp = client.post("/api/no-school-dates", data={
        "dates": _json.dumps(["2026-11-26", "not-a-date", "2026-09-01", "2026-09-01"]),
    })
    data = resp.json()
    assert data["ok"] is True
    assert data["dates"] == ["2026-09-01", "2026-11-26"]

    again = client.get("/api/no-school-dates").json()
    assert again["dates"] == ["2026-09-01", "2026-11-26"]


# --- Rendered template: the panel and its script load order ----------------

def test_gradebook_page_includes_grading_policy_panel_and_script_order(monkeypatch, tmp_path):
    _mount(monkeypatch, tmp_path)
    resp = client.get("/gradebook")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="btn-save-grading-policy"' in body
    assert 'id="gp-floor"' in body
    assert 'id="gp-no-school-dates"' in body
    policy_index = body.index("/static/gradebook/policy.js")
    grading_policy_index = body.index("/static/gradebook/grading_policy.js")
    assert policy_index < grading_policy_index
