"""Canvas account and workspace path configuration.

Machine-local config (base URL, token, workspace path).
Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
from . import _io as _io_code

import keyring
import os
from .. import workspace


# --------------------------------------------------------------------------
# Canvas account (base URL + token)
# --------------------------------------------------------------------------

def get_canvas_base() -> str:
    return _io_code._machine_load()["canvas_base"]


def set_canvas_base(base_url: str):
    _io_code._modify_machine(
        lambda state: state.__setitem__("canvas_base", base_url.rstrip("/")) or state
    )


def get_token() -> str | None:
    return keyring.get_password(_io_code.SERVICE, _io_code.TOKEN_KEY)


def set_token(token: str):
    keyring.set_password(_io_code.SERVICE, _io_code.TOKEN_KEY, token)


def token_is_set() -> bool:
    t = get_token()
    return bool(t and not t.startswith("PASTE"))


def get_download_root() -> str:
    state = _io_code._machine_load()
    if state.get("download_root"):
        return state["download_root"]
    root = workspace.workspace_root()
    if root:
        return os.path.join(root, "Exports")
    return _io_code.DOWNLOAD_ROOT_DEFAULT


def set_download_root(path: str):
    _io_code._modify_machine(
        lambda state: state.__setitem__("download_root", path) or state
    )


def save_canvas_account(base_url: str, token: str | None = None):
    """Save base URL (always) and token (only if provided)."""
    set_canvas_base(base_url)
    if token:
        set_token(token)


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Workspace path (machine-local override)
# --------------------------------------------------------------------------

def get_workspace_path() -> str | None:
    return _io_code._machine_load().get("workspace_path") or None


def set_workspace_path(path: str):
    _io_code._modify_machine(
        lambda state: state.__setitem__("workspace_path", path.strip()) or state
    )


def ensure_workspace_pinned() -> str | None:
    """Persist the resolved workspace path into machine-local config.

    The workspace normally resolves from the ``OneDrive``/``OneDriveCommercial``
    environment variable. Headless subprocesses launched by another app (the MCP
    server started by Claude Desktop or the ChatGPT desktop app) do not inherit
    that variable, so without a persisted path they cannot find the workspace and
    fall back to stale machine-local state. Pinning the resolved path here makes
    workspace resolution deterministic and environment-independent for every
    process. No-op once a path is already pinned, or when none can be resolved.
    """
    existing = get_workspace_path()
    if existing:
        return existing
    root = workspace.workspace_root()
    if root:
        set_workspace_path(root)
        return root
    return None


def get_whisper_model_cache() -> str:
    """Machine-local read-aloud model cache; it is never workspace state."""
    override = os.environ.get("CANVAS_EXPERT_WHISPER_MODEL_CACHE", "").strip()
    return override or os.path.join(os.environ.get("LOCALAPPDATA", ""), "CanvasExpert", "speech-models")


# --------------------------------------------------------------------------
# Runtime credential bundle
# --------------------------------------------------------------------------

def resolve_env(course_id: str) -> dict:
    """CANVAS_BASE / COURSE_ID / CANVAS_TOKEN dict for subprocess env."""
    token = get_token()
    if not token:
        raise ValueError("No Canvas token saved — go to Settings and paste your token.")
    return {
        "CANVAS_BASE": get_canvas_base(),
        "COURSE_ID":   str(course_id),
        "CANVAS_TOKEN": token,
    }
