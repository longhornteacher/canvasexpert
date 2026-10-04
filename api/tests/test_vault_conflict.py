"""Shared Identity Vault pseudonym and conflict-boundary safety laws."""
from __future__ import annotations

import json
import os
import sys

# api.mcp_server.pseudonym reaches web UI modules that import api siblings by
# their top-level names, matching the runtime's normal bootstrap.
_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_API_DIR)
for _path in (_API_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from api.mcp_server import tools
from api.mirror import store as mirror_store
from api.local_runtime import machine_id
from api.platform_services import workspace
from api.feedback_vault import _REGISTRY_WORDS
from api.shared_vault import SharedVault

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


def _use_shared_vault(monkeypatch, root):
    monkeypatch.setattr(
        tools, "_vault_factory",
        lambda: SharedVault(
            root / "_Shared" / "vault", workspace_root=root,
            secret_provider=lambda: b"s" * 32,
        ),
    )


def _set_active_courses(monkeypatch, course_ids):
    monkeypatch.setattr(
        tools.config, "active_courses",
        lambda: [{"id": cid, "name": f"Course {cid}"} for cid in course_ids],
    )


def test_get_roster_fails_closed_on_shared_vault_conflict(monkeypatch, tmp_path):
    root = tmp_path
    vault_dir = root / "_Shared" / "vault"
    vault_dir.mkdir(parents=True)
    canonical = vault_dir / f"journal.{machine_id()}.jsonl"
    event = {
        "v": 1, "ts": "2026-01-01T00:00:00Z", "machine": machine_id(),
        "op": "assign", "canvas_user_id": "synthetic-id-conflict",
        "pokemon": _REGISTRY_WORDS[0], "k": 0,
    }
    canonical.write_text(json.dumps(event) + "\n", encoding="utf-8")
    conflict = vault_dir / f"journal.{machine_id()}-OTHERPC.jsonl"
    conflict.write_text(json.dumps(event) + "\n", encoding="utf-8")

    _use_shared_vault(monkeypatch, root)
    _set_active_courses(monkeypatch, ["111"])

    result = tools.get_roster("111")
    assert result == {
        "ok": False,
        "error": ("shared workspace conflict detected — open Local workspace & privacy in "
                  "Canvas Expert to review it before student data is used"),
    }
    assert conflict.name not in json.dumps(result)
    _assert_no_leaks(result)


def test_get_roster_uses_stable_pseudonyms_without_leaking_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(workspace.runtime_paths, "local_cache_dir", lambda: tmp_path / "local-cache")
    _use_shared_vault(monkeypatch, tmp_path)
    _set_active_courses(monkeypatch, ["111"])
    mirror_store.write_roster("111", FIXTURE_USERS, SECTION_MAP, root=str(tmp_path))

    first = tools.get_roster("111")
    second = tools.get_roster("111")
    assert first["ok"] is True
    assert first["roster"] == second["roster"]
    assert len(first["roster"]["rows"]) == 2
    assert len({row[0] for row in first["roster"]["rows"]}) == 2
    _assert_no_leaks(first)
