"""The private Names table exposes identities, never roster edit fields."""
import json

from api.platform_services import workspace
from api.webui.routes import names


def test_names_table_initializes_fresh_vault_and_strips_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(names.mirror_store, "read_roster", lambda _: None)
    monkeypatch.setattr(names.roster_service, "fetch_students", lambda _: ([{
        "id": 910001, "name": "Synthetic Student", "sis_user_id": "synthetic-sis",
        "short_name": "Synthetic", "enrollments": [{"course_section_id": 1}],
        "extra_time": {"enabled": True, "days": 2}, "monitored": {"note": "private"},
    }], None))
    monkeypatch.setattr(names.roster_service, "fetch_sections", lambda _: {"1": "Section A"})

    result = json.loads(names.course_names("course-1").body)

    assert result["ok"] is True
    row = result["students"][0]
    assert set(row) == {"pseudonym", "real_name", "sections"}
    assert row["pseudonym"]
    assert row["real_name"] == "Synthetic Student"
    assert row["sections"] == [{"id": "1", "name": "Section A"}]
    assert len(names._vault().entries()) == 1


def test_names_current_mirror_avoids_live_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(names.mirror_store, "read_roster", lambda _: {
        "state": "current", "students": {"910002": {
            "id": 910002, "name": "Another Synthetic", "enrollments": [],
        }}, "sections": {},
    })
    def unexpected_live_read(*args, **kwargs):
        raise AssertionError("Current mirror must serve the Names table")
    monkeypatch.setattr(names.roster_service, "fetch_students", unexpected_live_read)
    monkeypatch.setattr(names.roster_service, "fetch_sections", unexpected_live_read)

    result = json.loads(names.course_names("course-1").body)

    assert result["ok"] is True
    assert result["students"][0]["real_name"] == "Another Synthetic"
    assert result["students"][0]["sections"] == []


def test_names_missing_course_and_canvas_error_are_bounded(monkeypatch):
    assert json.loads(names.course_names("").body)["ok"] is False
    monkeypatch.setattr(names.mirror_store, "read_roster", lambda _: None)
    monkeypatch.setattr(names.roster_service, "fetch_students", lambda _: (None, "unavailable"))
    assert json.loads(names.course_names("course-1").body) == {
        "ok": False, "error": "Canvas fetch failed: unavailable",
    }
