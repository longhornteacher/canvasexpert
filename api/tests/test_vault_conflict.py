"""Offline tests for vault multi-machine hardening (CanvasMirror v3, slice 6):
machine stamping on save and OneDrive conflict-copy detection.

Mirrors ``test_feedback_vault.py``'s tmp_path convention and
``test_mcp_server_tools.py``'s vault/course fixture pattern for the MCP
fail-closed check. Vault is always isolated to ``tmp_path`` -- never the real
global vault. Fabricated data uses generic names and large made-up Canvas IDs.
"""
from __future__ import annotations

import json
import os
import sys

# api/mcp_server/pseudonym.py reaches api.webui.routes.names, which (like the
# rest of the webui package) imports sibling top-level api/ modules with bare
# names ("import feedback_scrub"). That only resolves once the api/ directory
# itself is on sys.path -- normally guaranteed by api/mcp_server/__main__.py's
# bootstrap at runtime. Standalone test collection needs the same bootstrap
# (copied from test_mcp_server_tools.py).
_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_API_DIR)
for _path in (_API_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from api.feedback_vault import Vault
from api.mcp_server import pseudonym, tools
from api.mirror import store as mirror_store
from api.feedback_vault import machine_id
from api.platform_services import workspace

FIXTURE_USERS = [
    {
        "id": 900001,
        "name": "Learner One",
        "sortable_name": "One, Learner",
        "short_name": "Lee",
        "sis_user_id": "SIS-900001",
        "enrollments": [{"course_section_id": 800001}],
    },
    {
        "id": 900002,
        "name": "Learner Two",
        "sortable_name": "Two, Learner",
        "short_name": "Learner Two",
        "sis_user_id": "SIS-900002",
        "enrollments": [{"course_section_id": 800002}],
    },
]
SECTION_MAP = {"800001": "Period 1", "800002": "Period 2"}

_LEAKS = [
    "Learner One", "Learner Two", "One, Learner", "Two, Learner", "Lee",
    "900001", "900002", "SIS-900001", "SIS-900002",
]


def _assert_no_leaks(payload: dict):
    dumped = json.dumps(payload)
    for leak in _LEAKS:
        assert leak not in dumped, f"{leak!r} leaked into payload: {dumped}"


def _use_vault(monkeypatch, vault_path: str):
    monkeypatch.setattr(tools, "_vault_factory", lambda: Vault(vault_path))


def _set_active_courses(monkeypatch, course_ids):
    monkeypatch.setattr(
        tools.config, "active_courses",
        lambda: [{"id": cid, "name": f"Course {cid}"} for cid in course_ids],
    )


# --- machine stamping on save -------------------------------------------------

def test_stamped_save_round_trips_and_fresh_load_ignores_stamp(tmp_path):
    vault_path = str(tmp_path / "vault.json")
    v = Vault(vault_path)
    p1 = v.get_or_assign("900001", "Learner One", "SIS-900001")
    v.save()

    with open(vault_path, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["written_by"] == machine_id()
    assert doc["entry_count"] == 1
    assert doc["written_at"]  # non-empty ISO-ish timestamp
    assert doc["by_canvas_id"]["900001"]["pseudonym"] == p1

    # A fresh load must ignore the stamp keys entirely -- entries intact,
    # nothing bleeds into the by-id map.
    v2 = Vault(vault_path)
    assert len(v2) == 1
    assert v2.get_or_assign("900001") == p1
    assert v2.reverse(p1)["real_name"] == "Learner One"
    assert "written_by" not in v2._by_id
    assert "written_at" not in v2._by_id
    assert "entry_count" not in v2._by_id
    assert v2.conflicts() == []


# --- legacy vault (schema v3, but no OneDrive stamp keys at all) -------------

def test_legacy_vault_without_stamp_keys_loads_fine(tmp_path):
    vault_path = str(tmp_path / "vault.json")
    with open(vault_path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 3, "by_canvas_id": {
            "900001": {
                "pseudonym": "Pikachu", "real_name": "Learner One",
                "sis_id": "", "nicknames": [], "first_seen": "",
            },
        }}, f)

    v = Vault(vault_path)
    assert len(v) == 1
    assert v.get_or_assign("900001") == "Pikachu"
    assert v.conflicts() == []


# --- conflict-copy detection --------------------------------------------------

def test_conflict_copy_detected(tmp_path):
    vault_path = tmp_path / "vault.json"
    vault_path.write_text(json.dumps({"schema_version": 3, "by_canvas_id": {}}), encoding="utf-8")
    (tmp_path / "vault-OTHERPC.json").write_text("{}", encoding="utf-8")

    v = Vault(str(vault_path))
    assert v.conflicts() == ["vault-OTHERPC.json"]


def test_parenthesized_conflict_copy_also_detected(tmp_path):
    vault_path = tmp_path / "vault.json"
    vault_path.write_text(json.dumps({"schema_version": 3, "by_canvas_id": {}}), encoding="utf-8")
    (tmp_path / "vault (1).json").write_text("{}", encoding="utf-8")

    v = Vault(str(vault_path))
    assert v.conflicts() == ["vault (1).json"]


def test_lock_file_and_canonical_name_not_flagged(tmp_path):
    vault_path = tmp_path / "vault.json"
    vault_path.write_text(json.dumps({"schema_version": 3, "by_canvas_id": {}}), encoding="utf-8")
    (tmp_path / "vault.json.lock").write_text("0", encoding="utf-8")

    v = Vault(str(vault_path))
    assert v.conflicts() == []


def test_no_conflict_when_no_extra_files(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    assert v.conflicts() == []


# --- MCP student-data tools fail closed on conflict ---------------------------

def test_get_roster_fails_closed_on_vault_conflict(monkeypatch, tmp_path):
    vault_path = tmp_path / "vault.json"
    vault_path.write_text(json.dumps({"schema_version": 3, "by_canvas_id": {}}), encoding="utf-8")
    (tmp_path / "vault-OTHERPC.json").write_text("{}", encoding="utf-8")

    _use_vault(monkeypatch, str(vault_path))
    _set_active_courses(monkeypatch, ["111"])
    monkeypatch.setattr(pseudonym, "_fetch_students",
                       lambda course_id: (FIXTURE_USERS, None))
    monkeypatch.setattr(tools, "_fetch_sections",
                       lambda course_id, canvas_get_all: SECTION_MAP)

    result = tools.get_roster("111")
    assert result == {
        "ok": False,
        "error": ("shared workspace conflict detected — open Local workspace & privacy in "
                  "Canvas Expert to review it before student data is used"),
    }
    # No file basenames, and no student data, in the dumped result.
    dumped = json.dumps(result)
    assert "vault-OTHERPC" not in dumped
    _assert_no_leaks(result)


def test_get_roster_normal_when_no_conflict(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(workspace.runtime_paths, "local_cache_dir", lambda: tmp_path / "local-cache")
    vault_path = str(tmp_path / "vault.json")
    _use_vault(monkeypatch, vault_path)
    _set_active_courses(monkeypatch, ["111"])
    mirror_store.write_roster("111", FIXTURE_USERS, SECTION_MAP)

    result = tools.get_roster("111")
    assert result["ok"] is True
    assert len(result["roster"]["rows"]) == 2
    _assert_no_leaks(result)
