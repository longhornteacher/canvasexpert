"""Private I/O helpers for the config package.

Exports the machine-local and workspace-synced persistence primitives.
Every other config/* module imports from here.
"""
import json
import os
from copy import deepcopy
from pathlib import Path

import keyring

from api import runtime_paths
from api.storage_support import atomic_write_json, interprocess_lock
from .. import workspace

SERVICE   = "quizforge-api"
TOKEN_KEY = "canvas_token"
# Empty by default — the first-run wizard collects the teacher's Canvas URL.
# An empty base is the signal that onboarding is not yet complete.
CANVAS_BASE_DEFAULT   = ""
DOWNLOAD_ROOT_DEFAULT = os.path.join(os.path.expanduser("~"), "Desktop", "Canvas Downloads")
# Pre-0.75 machine-local config lived inside the app folder, which a
# self-update mirrors wholesale -- see runtime_paths.migrate_legacy_file().
LEGACY_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")
CONFIG_PATH = str(runtime_paths.local_app_dir() / "config.json")
SYNCED_KEYS = ("saved_courses", "extra_time", "late_sweep", "tier_tags",
               "ai_ta_persona", "roster_student_settings", "roster_tier_schemes",
               "roster_group_schemes", "roster_score_matrices", "roster_relationships",
               "monitored_students", "roster_baselines",
               "sis_grade_bridges")


def _machine_load():
    runtime_paths.migrate_legacy_file(LEGACY_CONFIG_PATH, CONFIG_PATH)
    if not os.path.exists(CONFIG_PATH):
        return {"canvas_base": CANVAS_BASE_DEFAULT, "saved_courses": []}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("canvas_base", CANVAS_BASE_DEFAULT)
    data.setdefault("saved_courses", [])
    return data


def _machine_save(state):
    with interprocess_lock(Path(CONFIG_PATH + ".lock")):
        atomic_write_json(Path(CONFIG_PATH), state)


def _workspace_settings_path() -> str | None:
    root = workspace.workspace_root()
    if not root:
        return None
    return os.path.join(root, "settings.json")


def _workspace_load():
    path = _workspace_settings_path()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # OSError [Errno 22] on Windows = OneDrive cloud-only file not yet downloaded
        return {}


def _workspace_save(state):
    path = _workspace_settings_path()
    if not path:
        return
    # OneDrive sync is last-writer-wins here; conflict copies like settings-<PC>.json
    # are ignored by the app and left for the user to reconcile manually.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with interprocess_lock(Path(path + ".lock")):
        atomic_write_json(Path(path), state)


def _synced_state():
    machine = _machine_load()
    path = _workspace_settings_path()
    if not path:
        return machine
    if not os.path.exists(path):
        _workspace_save({k: machine[k] for k in SYNCED_KEYS if k in machine})
    ws = _workspace_load()
    # Backfill synced keys that exist machine-local but were never written to the
    # workspace — covers keys promoted to SYNCED_KEYS after the workspace was first
    # seeded (e.g. monitored_students, now PII-synced). Machine data only fills gaps;
    # the workspace copy stays authoritative once present.
    missing = {k: machine[k] for k in SYNCED_KEYS if k in machine and k not in ws}
    if missing:
        ws.update(missing)
        _workspace_save(ws)
    merged = dict(machine)
    merged.update(ws)
    return merged


def _save_synced_key(key, value):
    _modify_synced(lambda state: state.__setitem__(key, value) or state)


def _modify_machine(mutator) -> dict:
    """Reload and atomically apply one machine-local JSON mutation."""
    with interprocess_lock(Path(CONFIG_PATH + ".lock")):
        state = _machine_load()
        updated = mutator(state)
        if updated is None:
            updated = state
        if not isinstance(updated, dict):
            raise TypeError("machine mutator must return a dict or None")
        atomic_write_json(Path(CONFIG_PATH), updated)
        return deepcopy(updated)


def _modify_workspace(mutator) -> dict | None:
    """Reload and atomically apply one workspace-settings mutation."""
    path = _workspace_settings_path()
    if not path:
        return None
    with interprocess_lock(Path(path + ".lock")):
        state = _workspace_load()
        updated = mutator(state)
        if updated is None:
            updated = state
        if not isinstance(updated, dict):
            raise TypeError("workspace mutator must return a dict or None")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_json(Path(path), updated)
        return deepcopy(updated)


def _modify_synced(mutator) -> dict:
    """Reload the latest merged synced state and save it under its owner lock."""
    path = _workspace_settings_path()
    if not path:
        return _modify_machine(mutator)

    machine_lock = Path(CONFIG_PATH + ".lock")
    workspace_lock = Path(path + ".lock")
    with interprocess_lock(machine_lock):
        with interprocess_lock(workspace_lock):
            machine = _machine_load()
            if not os.path.exists(path):
                atomic_write_json(
                    Path(path),
                    {k: machine[k] for k in SYNCED_KEYS if k in machine},
                )
            ws = _workspace_load()
            missing = {
                k: machine[k] for k in SYNCED_KEYS
                if k in machine and k not in ws
            }
            if missing:
                ws.update(deepcopy(missing))
                atomic_write_json(Path(path), ws)
            merged = dict(machine)
            merged.update(ws)
            before = deepcopy(merged)
            updated = mutator(merged)
            if updated is None:
                updated = merged
            if not isinstance(updated, dict):
                raise TypeError("synced mutator must return a dict or None")
            for key in set(before) | set(updated):
                if before.get(key) == updated.get(key):
                    continue
                if key in updated:
                    ws[key] = deepcopy(updated[key])
                else:
                    ws.pop(key, None)
            atomic_write_json(Path(path), ws)
            return deepcopy(updated)
