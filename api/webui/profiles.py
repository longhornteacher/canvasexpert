"""Course profiles: friendly-name -> {base URL, course ID, cached course name}.

Non-secret profile data lives in profiles.json (gitignored, local-only). The
Canvas token is the secret half and never touches disk — it's stored in the
OS credential store via `keyring`, keyed by profile name.
"""
import json
import os

import keyring

from api import runtime_paths

SERVICE = "quizforge-api"
PROFILES_PATH = str(runtime_paths.local_app_dir() / "profiles.json")


def _load():
    if not os.path.exists(PROFILES_PATH):
        return {"profiles": []}
    with open(PROFILES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(state):
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def list_profiles():
    """Non-secret profile records (no tokens)."""
    return _load()["profiles"]


def get_profile(name):
    for p in list_profiles():
        if p["name"] == name:
            return p
    return None


def save_profile(name, base_url, course_id, course_name="", token=None):
    """Create or update a profile. `token` is optional on update — pass None
    to keep the previously-stored token."""
    state = _load()
    profiles = state["profiles"]
    record = {
        "name": name,
        "base_url": base_url.rstrip("/"),
        "course_id": str(course_id),
        "course_name": course_name,
    }
    for i, p in enumerate(profiles):
        if p["name"] == name:
            profiles[i] = record
            break
    else:
        profiles.append(record)
    _save(state)
    if token:
        keyring.set_password(SERVICE, name, token)


def delete_profile(name):
    state = _load()
    state["profiles"] = [p for p in state["profiles"] if p["name"] != name]
    _save(state)
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def get_token(name):
    return keyring.get_password(SERVICE, name)


def has_token(name):
    return bool(get_token(name))


def resolve_env(name):
    """CANVAS_BASE / COURSE_ID / CANVAS_TOKEN dict for a subprocess env."""
    profile = get_profile(name)
    if not profile:
        raise KeyError(f"no such profile: {name!r}")
    token = get_token(name)
    if not token:
        raise ValueError(f"profile {name!r} has no saved token")
    return {
        "CANVAS_BASE": profile["base_url"],
        "COURSE_ID": profile["course_id"],
        "CANVAS_TOKEN": token,
    }
