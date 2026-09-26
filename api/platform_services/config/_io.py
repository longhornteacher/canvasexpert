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
from api.shared_kv import SharedKVStore
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
SYNCED_KEYS = ("saved_courses", "extra_time",
               "tier_tags", "tier_colors",
               "roster_student_settings",
               # Retired tier/group schemes remain registered as inert stored state.
               "roster_tier_schemes", "roster_group_schemes",
               "roster_score_matrices", "roster_relationships",
               "monitored_students", "roster_baselines",
               "sis_grade_bridges",
               # Retired feedback personas remain registered as inert stored state.
               "ai_ta_persona", "custom_personas",
               "protected_packs_enabled", "protected_names_custom")


def _machine_load():
    runtime_paths.migrate_legacy_file(LEGACY_CONFIG_PATH, CONFIG_PATH)
    if not os.path.exists(CONFIG_PATH):
        return {"canvas_base": CANVAS_BASE_DEFAULT, "saved_courses": []}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("canvas_base", CANVAS_BASE_DEFAULT)
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
    raise RuntimeError("workspace_settings_are_append_only")


def _synced_state():
    machine = _machine_load()
    path = _workspace_settings_path()
    if not path:
        machine.setdefault("saved_courses", [])
        return machine
    fallback = {k: machine[k] for k in SYNCED_KEYS if k in machine}
    store = SharedKVStore("settings", root=workspace.workspace_root(), legacy_path=path)
    ws = store.read(fallback)
    # Once the immutable snapshot exists, synced values have exactly one source:
    # the shared journal. Remove old local duplicates so a later device-local
    # write cannot resurrect a tombstoned setting.
    stale = set(SYNCED_KEYS) & set(machine)
    if stale:
        for key in stale:
            machine.pop(key, None)
        _machine_save(machine)
    merged = dict(machine)
    merged.update(ws)
    merged.setdefault("saved_courses", [])
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
    """Compatibility wrapper that now records changes as journal events."""
    if not _workspace_settings_path():
        return None
    return _modify_synced(mutator)


def _modify_synced(mutator) -> dict:
    """Reload the latest merged synced state and save it under its owner lock."""
    path = _workspace_settings_path()
    if not path:
        return _modify_machine(mutator)

    machine_lock = Path(CONFIG_PATH + ".lock")
    with interprocess_lock(machine_lock):
        machine = _machine_load()
        fallback = {k: machine[k] for k in SYNCED_KEYS if k in machine}
        store = SharedKVStore("settings", root=workspace.workspace_root(), legacy_path=path)
        ws = store.read(fallback)
        merged = dict(machine)
        for key in SYNCED_KEYS:
            merged.pop(key, None)
        merged.update(ws)
        before = deepcopy(merged)
        updated = mutator(merged)
        if updated is None:
            updated = merged
        if not isinstance(updated, dict):
            raise TypeError("synced mutator must return a dict or None")
        before_synced = {k: before[k] for k in SYNCED_KEYS if k in before}
        after_synced = {k: updated[k] for k in SYNCED_KEYS if k in updated}
        store.append_changes(before_synced, after_synced)
        local_state = _machine_load()
        changed = False
        for key in SYNCED_KEYS:
            if key in local_state:
                local_state.pop(key, None)
                changed = True
        if changed:
            atomic_write_json(Path(CONFIG_PATH), local_state)
        return deepcopy(updated)
