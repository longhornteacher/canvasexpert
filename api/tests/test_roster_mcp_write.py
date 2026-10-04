"""Privacy and concurrency coverage for the schema-v21 roster MCP surface."""

from __future__ import annotations

import json

import pytest

from api import feedback_vault
from api.feedback_vault import Vault
from api.mcp_server import tools


USER = {
    "id": 910001,
    "name": "Sam Student",
    "sortable_name": "Student, Sam",
    "short_name": "Sam",
    "sis_user_id": "SIS-910001",
    "enrollments": [{"course_section_id": 1}],
}


def _setup(monkeypatch, tmp_path):
    path = str(tmp_path / "vault.json")
    vault = Vault(path)
    with vault.transaction():
        pseudo = vault.get_or_assign(USER["id"], USER["name"], USER["sis_user_id"])
        vault.set_nicknames(USER["id"], ["Sam"])
    monkeypatch.setattr(tools, "_vault_factory", lambda: Vault(path))
    monkeypatch.setattr(tools, "_mirror_roster_doc", lambda course_id: {
        "students": [USER], "sections": {"1": "Period 1"}, "last_success_at": "now",
    })
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(tools.config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(tools.config, "set_extra_time", lambda course_id, value: None)
    monkeypatch.setattr(tools.config, "get_monitored_students", lambda: {})
    monkeypatch.setattr(tools.config, "set_monitored_student", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools.config, "remove_monitored_student", lambda user_id: None)
    monkeypatch.setattr(tools.config, "get_roster_student_settings", lambda course_id: {})
    return path, pseudo


def _assert_private(result, pseudo):
    dumped = json.dumps(result)
    assert result["ok"] is True
    assert result["pseudonym"] == pseudo or result.get("preview", {}).get("pseudonym") == pseudo
    assert "Sam Student" not in dumped
    assert "Student, Sam" not in dumped
    assert "910001" not in dumped
    assert "SIS-910001" not in dumped
    assert '"Sam"' not in dumped


def test_roster_settings_never_return_identity_or_stored_nickname(monkeypatch, tmp_path):
    path, pseudo = _setup(monkeypatch, tmp_path)

    result = tools.get_roster('course-1', pseudo)
    dumped = json.dumps(result)
    assert result["ok"] is True
    assert result["pseudonym"] == pseudo
    assert "Sam Student" not in dumped
    assert "910001" not in dumped
    assert "SIS-910001" not in dumped
    assert '"Sam"' not in dumped
    assert result["settings"]["pseudonym"] == pseudo


def test_get_preview_apply_and_clear_are_gated_and_private(monkeypatch, tmp_path):
    path, pseudo = _setup(monkeypatch, tmp_path)
    got = tools.get_roster('course-1', pseudo)
    _assert_private(got, pseudo)
    assert "nicknames" not in got["settings"]

    preview = tools.preview_roster_student_change(
        "course-1", pseudo, {"add_nicknames": ["Sammy"],
                              "extra_time": {"enabled": True, "days": 2}})
    _assert_private(preview, pseudo)
    assert preview["next"] == tools._NEXT_STEPS["preview_roster_student_change"]
    assert pseudo not in preview["next"]
    assert '"nicknames":' not in json.dumps(preview["preview"])
    assert preview["preview"]["patch"] == {
        "add_nicknames": ["Sammy"], "extra_time": {"enabled": True, "days": 2}}
    assert preview["preview"]["after"]["add_nicknames"] == ["Sammy"]

    applied = tools.apply_roster_student_change(
        "course-1", preview["preview"], preview["preview_digest"],
        preview["settings_digest"])
    _assert_private(applied, pseudo)
    clear = tools.preview_roster_student_change("course-1", pseudo, {"extra_time": None})
    cleared = tools.apply_roster_student_change("course-1", clear["preview"], clear["preview_digest"], applied["settings_digest"])
    _assert_private(cleared, pseudo)
    assert Vault(path).entries()[0]["nicknames"] == ["Sam", "Sammy"]


def test_add_nicknames_is_additive_and_clear_rejects_both_nickname_keys(monkeypatch, tmp_path):
    path, pseudo = _setup(monkeypatch, tmp_path)
    preview = tools.preview_roster_student_change(
        "course-1", pseudo, {"add_nicknames": ["Sammy"]})
    assert preview["ok"] is True
    assert preview["preview"]["after"]["add_nicknames"] == ["Sammy"]
    assert '"Sam"' not in json.dumps(preview)

    applied = tools.apply_roster_student_change(
        "course-1", preview["preview"], preview["preview_digest"],
        preview["settings_digest"])
    assert applied["ok"] is True
    saved = Vault(path)
    assert saved.entries()[0]["nicknames"] == ["Sam", "Sammy"]
    for field in ("nicknames", "add_nicknames"):
        rejected = tools.preview_roster_student_change("course-1", pseudo, {field: None})
        assert rejected["ok"] is False


def test_mcp_add_nicknames_never_calls_replacing_vault_method(monkeypatch, tmp_path):
    _, pseudo = _setup(monkeypatch, tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("MCP must use add_nicknames, never set_nicknames")
    monkeypatch.setattr(Vault, "set_nicknames", forbidden)
    current = tools.get_roster('course-1', pseudo)
    preview = tools.preview_roster_student_change(
        "course-1", pseudo, {"add_nicknames": ["Sammy"]})
    applied = tools.apply_roster_student_change(
        "course-1", preview["preview"], preview["preview_digest"],
        current["settings_digest"])
    assert applied["ok"] is True


def test_hidden_nickname_change_invalidates_settings_digest(monkeypatch, tmp_path):
    path, pseudo = _setup(monkeypatch, tmp_path)
    current = tools.get_roster('course-1', pseudo)
    changed = Vault(path)
    with changed.transaction():
        changed.add_nicknames(USER["id"], ["Sammy"])
    preview = tools.preview_roster_student_change(
        "course-1", pseudo, {"extra_time": {"enabled": True, "days": 1}})
    assert preview["settings_digest"] != current["settings_digest"]
    stale = tools.apply_roster_student_change(
        "course-1", preview["preview"], preview["preview_digest"],
        current["settings_digest"])
    assert stale["ok"] is False


def test_stale_preview_digest_and_settings_digest_never_write(monkeypatch, tmp_path):
    _, pseudo = _setup(monkeypatch, tmp_path)
    preview = tools.preview_roster_student_change(
        "course-1", pseudo, {"extra_time": {"enabled": True, "days": 1}})
    before = tools.get_roster('course-1', pseudo)
    writes = []
    monkeypatch.setattr(tools, "_apply_roster_update",
                        lambda *args: writes.append(args) or {"ok": True})
    bad_preview = tools.apply_roster_student_change(
        "course-1", preview["preview"], "wrong-preview-digest",
        preview["settings_digest"])
    bad_settings = tools.apply_roster_student_change(
        "course-1", preview["preview"], preview["preview_digest"],
        "wrong-settings-digest")
    assert bad_preview["ok"] is False
    assert bad_settings["ok"] is False
    assert writes == []
    assert tools.get_roster('course-1', pseudo)["settings_digest"] == before["settings_digest"]


def test_adapter_itself_refuses_the_replacing_nickname_key(monkeypatch, tmp_path):
    """The guard lives in the adapter, not only in the MCP tool above it.

    set_nicknames overwrites the teacher's scrub-coverage list. A caller that
    reached this adapter without going through _validate_mcp_roster_patch could
    otherwise erase it, so the replacing key is not in the adapter's allowlist.
    """
    from api.webui import roster_mcp

    path, _pseudo = _setup(monkeypatch, tmp_path)
    vault = Vault(path)
    calls = []
    monkeypatch.setattr(Vault, "set_nicknames",
                        lambda self, *a, **k: calls.append(a))

    with vault.transaction():
        result = roster_mcp.update_student("course-1", "910001", {"nicknames": ["Wipe"]}, vault)

    assert result["ok"] is False
    assert calls == []
    assert "nicknames" in result["error"]
    stored = next(entry for entry in Vault(path).entries()
                  if str(entry["canvas_id"]) == str(USER["id"]))
    assert stored["nicknames"] == ["Sam"]


