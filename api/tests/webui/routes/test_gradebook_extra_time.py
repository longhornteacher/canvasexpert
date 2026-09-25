"""Focused tests for the extra-time student list's roster-mirror-first read
(1.0beta-04a). Fixture style mirrors ``api/tests/test_mirror_reads_helper.py``:
workspace root redirected to ``tmp_path``, roster populated via
``store.write_roster``. This is a pure display list (no email, no TTL —
matching ``api/webui/routes/courses.py``'s ``_local_students``, the existing
roster mirror read this slice reuses): freshness is state == "current" only.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.mirror import store as mirror_store
from api.platform_services import workspace
from api.platform_services import config
from api.platform_services.config import _io as config_io
from api.webui.server import app
from api.webui.routes import gradebook_extra_time
from engine.rendering.forge.palette import PALETTES, DEFAULT_TIER_COLORS

client = TestClient(app)

COURSE = "555401"

USERS = [
    {"id": 900401, "name": "Learner One", "sortable_name": "One, Learner",
     "short_name": "Lee", "sis_user_id": "SIS-900401", "email": "one@example.invalid",
     "enrollments": []},
    {"id": 900402, "name": "Learner Two", "sortable_name": "Two, Learner",
     "short_name": "L2", "sis_user_id": "SIS-900402", "email": "two@example.invalid",
     "enrollments": []},
]


def _explode(*_args, **_kwargs):
    raise AssertionError("live Canvas read attempted when the roster mirror should have served")


def test_students_list_serves_current_roster_mirror_with_zero_live_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    mirror_store.write_roster(COURSE, USERS, {}, root=str(tmp_path))
    monkeypatch.setattr(gradebook_extra_time, "_course_students", _explode)

    resp = client.get(f"/api/students/list?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    names = {s["name"] for s in data["students"]}
    assert names == {"One, Learner", "Two, Learner"}
    for student in data["students"]:
        assert "email" not in student
        assert set(student) == {"id", "name"}


def test_students_list_falls_back_live_when_roster_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    # No roster written at all -> read_roster returns None -> live fallback.
    live_rows = [{"id": 1, "name": "Live Student", "sortable_name": "Student, Live"}]
    monkeypatch.setattr(gradebook_extra_time, "_course_students",
                        lambda course_id: (live_rows, None))

    resp = client.get(f"/api/students/list?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["students"] == [{"id": "1", "name": "Student, Live"}]


def test_students_list_falls_back_live_when_roster_state_is_not_current(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    # "incomplete" is a valid roster envelope state (pagination didn't fully
    # complete) but is not "current" — the mirror-first helper must defer to
    # live rather than serve a partial roster as the student list.
    mirror_store.write_roster(COURSE, USERS, {}, root=str(tmp_path), state="incomplete")

    live_rows = [{"id": 2, "name": "Fallback Student", "sortable_name": "Student, Fallback"}]
    monkeypatch.setattr(gradebook_extra_time, "_course_students",
                        lambda course_id: (live_rows, None))

    resp = client.get(f"/api/students/list?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["students"] == [{"id": "2", "name": "Student, Fallback"}]


def test_students_list_live_error_still_surfaces(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(gradebook_extra_time, "_course_students",
                        lambda course_id: (None, "HTTP 401: unauthorized"))

    resp = client.get(f"/api/students/list?course_id={COURSE}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "401" in data["error"]


def test_tier_color_setting_routes_validate_preserve_and_default_without_writes(monkeypatch):
    state = {"tier_colors": {"Support": "not-a-swatch", "Core": "green", "old-tier": "legacy"}}
    writes = []
    monkeypatch.setattr(config_io, "_synced_state", lambda: state)

    def save(mutator):
        writes.append(True)
        mutator(state)
        return state

    monkeypatch.setattr(config_io, "_modify_synced", save)
    before = dict(state["tier_colors"])
    assert config.get_tier_colors() == {
        "Support": DEFAULT_TIER_COLORS["Support"], "Core": "green",
        "Accelerate": "blue", "untiered": "teal",
    }
    assert state["tier_colors"] == before
    assert writes == []
    state["tier_colors"] = {
        "Support": "purple", "Core": "purple", "Accelerate": "blue", "old-tier": "legacy",
    }
    assert config.get_tier_colors() == DEFAULT_TIER_COLORS
    assert writes == []

    getter = client.get("/api/tier-colors")
    assert getter.status_code == 200
    assert getter.json()["tier_colors"]["Support"] == "silver"

    for key in PALETTES:
        result = client.post("/api/tier-colors", data={"colors": json.dumps({
            "Support": "silver", "Core": "red", "Accelerate": "blue", "untiered": key,
        })})
        assert result.json()["ok"] is True
        assert state["tier_colors"]["untiered"] == key

    saved = dict(state["tier_colors"])
    count = len(writes)
    invalid = client.post("/api/tier-colors", data={"colors": json.dumps({
        "Support": "silver", "Core": "red", "Accelerate": "blue", "untiered": "free-hex",
    })})
    assert invalid.json() == {"ok": False, "error": "Choose one of the available swatches for every color."}
    duplicate = client.post("/api/tier-colors", data={"colors": json.dumps({
        "Support": "silver", "Core": "silver", "Accelerate": "blue", "untiered": "teal",
    })})
    assert duplicate.json() == {"ok": False, "error": "Support, Core, and Accelerate must use different colors."}
    missing = client.post("/api/tier-colors", data={"colors": json.dumps({
        "Support": "silver", "Core": "red", "Accelerate": "blue",
    })})
    assert missing.json() == {"ok": False, "error": "Choose a color for every tier and for Untiered and pages."}
    assert len(writes) == count
    assert state["tier_colors"]["old-tier"] == "legacy"
    assert state["tier_colors"] == saved
