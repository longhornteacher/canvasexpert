"""Call-time paths for the portable Canvas Expert application.

Stable application paths come from this module's location. Workspace-derived
paths are resolved through the current workspace setting on every call so a
workspace switch does not require a Python restart.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


_API_ROOT = Path(__file__).resolve().parent
_APP_ROOT = _API_ROOT.parent


def local_app_dir() -> Path:
    """Machine-local, per-user application data directory for Canvas Expert.

    Everything that lives here survives a self-update's whole-folder mirror,
    because it lives outside the app folder entirely -- unlike
    ``app_root()``, which a self-update replaces wholesale. Modeled on
    ``api/operation_ledger/paths.py``'s ``private_root()``, which already
    does exactly this.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), "AppData", "Local")
    return Path(base) / "CanvasExpert"


def local_cache_dir() -> Path:
    """Disposable Canvas projections, isolated to this Windows profile."""
    return local_app_dir() / "cache"


def machine_identity_path() -> Path:
    """Stable local identity used to name this machine's append-only files."""
    return local_app_dir() / "machine.json"


def process_lock_path() -> Path:
    """OS-level lock shared by every Canvas Expert entry point on this machine."""
    return local_app_dir() / "ce.lock"


def runtime_instance_path() -> Path:
    """Local rendezvous metadata for a process that already owns ce.lock."""
    return local_app_dir() / "runtime.json"


def migrate_legacy_file(legacy_path, new_path) -> None:
    """One-time copy of a legacy in-app-folder file to its new machine-local
    home, the first time the new path is read and found missing.

    The legacy file is left in place on purpose: an older copy of the app on
    the same machine may still depend on it, and the self-update preserve
    list keeps it alive across updates anyway, so deleting it here would add
    risk for no benefit. Safe to call on every read -- once the new path
    exists this is a single ``exists()`` check and returns immediately.
    """
    legacy_path = str(legacy_path)
    new_path = str(new_path)
    if os.path.exists(new_path) or not os.path.exists(legacy_path):
        return
    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    shutil.copy2(legacy_path, new_path)


def _workspace_module():
    """Return the canonical platform workspace module without an import cycle."""
    loaded = sys.modules.get("api.platform_services.workspace")
    if loaded is not None:
        return loaded
    from .platform_services import workspace
    return workspace


def app_root() -> Path:
    """The single unzipped Canvas Expert application root."""
    return _APP_ROOT


def api_root() -> Path:
    return _API_ROOT


def python_executable() -> Path:
    return Path(sys.executable).resolve()


def mcp_entrypoint() -> Path:
    return api_root() / "mcp_server" / "__main__.py"


def workspace_root() -> Path | None:
    value = _workspace_module().workspace_root()
    return Path(value) if value else None


def workspace_folder(name: str) -> Path | None:
    value = _workspace_module().folder(name)
    return Path(value) if value else None


def library_folder(name: str) -> Path | None:
    value = _workspace_module().library_folder(name)
    return Path(value) if value else None


def assignments_root() -> Path | None:
    """The sole authored/staged assignment source tree."""
    value = _workspace_module().assignments_root()
    return Path(value) if value else None


def shared_assignments_root() -> Path | None:
    value = _workspace_module().shared_assignments_root()
    return Path(value) if value else None


def course_assignments_root(course_id: str, course_nickname: str = "") -> Path | None:
    value = _workspace_module().course_assignments_root(course_id, course_nickname)
    return Path(value) if value else None


def printables_dir() -> Path:
    return workspace_folder("Printables") or (app_root() / "Finished_Exports" / "Printables")


def canvas_uploads_dir() -> Path:
    return workspace_folder("Canvas Uploads") or (app_root() / "Finished_Exports" / "Canvas Uploads")


def temp_dir() -> Path:
    return api_root() / "temp"


_KIND_WORKSPACE_NAMES = {
    "quiz": "Quizzes",
    "assignment": "Assignments",
    "page": "Pages",
}


def content_folders(kind: str) -> list[Path]:
    workspace_name = _KIND_WORKSPACE_NAMES.get(kind)
    if workspace_name is None:
        raise ValueError(f"unknown content folder kind: {kind}")

    # AssignmentForge content has one canonical source tree. Bundled
    # qf_materials files are examples, not current workspace content, and must
    # never appear in a picker or become a silent fallback.
    folders: list[Path] = []
    if kind == "assignment":
        current = assignments_root()
        if current:
            folders.append(current)
    else:
        current = library_folder(workspace_name)
        if current:
            folders.append(current)
    # An unconfigured workspace yields an empty list rather than silently
    # serving bundled repo copies; the picker's existing setup guidance is the
    # pointer to configure one.
    return folders


def inbox_folder(kind: str) -> Path | None:
    """Per-kind To Review drop folder where an MCP-capable assistant stages a
    draft for the teacher to review and push.

    Distinct from the teacher's own content folders returned by
    ``content_folders`` (Library/Quizzes, Assignments, Library/Pages): this is
    a separate, marker-gated pickup surface -- see
    ``webui.deps.list_inbox_files``. Not included in ``content_folders``'s
    plain glob, since that glob has no marker gate and would surface a
    half-synced drop.

    Resolves against the workspace root the same way ``workspace_folder``
    does, and returns None when the workspace is unavailable. When the
    workspace is available, ensures the folder exists (parents included) so
    the assistant always has a stable place to drop a file.
    """
    workspace_name = _KIND_WORKSPACE_NAMES.get(kind)
    if workspace_name is None:
        raise ValueError(f"unknown content folder kind: {kind}")

    root = workspace_root()
    if not root:
        return None
    folder = root / "To Review" / workspace_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def ai_ta_dir() -> Path:
    return library_folder("AI Authoring") or (app_root() / "AI Authoring")
