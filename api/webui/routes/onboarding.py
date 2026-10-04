"""Onboarding wizard routes — first-run setup for Canvas Expert.

These routes drive the stepped welcome flow (workspace → Canvas URL → API token
→ optional calendars) for brand-new users. The wizard remains available from Settings.

Routes: GET  /welcome
        POST /welcome/workspace
        POST /welcome/browse-workspace
"""
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from api.platform_services import config, workspace
from ..deps import templates

router = APIRouter(tags=["onboarding"])


@router.get("/welcome")
def welcome_page(request: Request):
    """Render the stepped onboarding wizard."""
    # Only suggest a path we actually know is a sync folder (an existing saved
    # path, or a detected OneDrive root). A silent fallback to the bare home
    # directory would sit behind one "Confirm" click and quietly put a
    # teacher's work somewhere with no sync and no backup -- leave the field
    # empty instead so the wizard shows the Browse button and forces a choice.
    existing = config.get_workspace_path()
    onedrive = workspace.onedrive_root()
    if existing:
        suggestion = existing
    elif onedrive:
        suggestion = os.path.join(onedrive, workspace.WORKSPACE_NAME)
    else:
        suggestion = ""
    return templates.TemplateResponse(request, "welcome.html", {
        "nav_section":         "",
        "workspace_suggestion": suggestion,
        "canvas_base":         config.get_canvas_base(),
        "token_is_set":        config.token_is_set(),
    })


def _ask_directory() -> str:
    """Open a native OS folder-picker dialog and return the chosen path, or
    "" if the teacher cancels. Raises if tkinter isn't available or a dialog
    can't be shown. Isolated from the route so tests can monkeypatch it
    without a real display.
    """
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title="Choose a folder for Canvas Expert")
    finally:
        root.destroy()


@router.post("/welcome/browse-workspace")
def browse_workspace():
    """Open a native folder picker and return the chosen path.

    Runs a real OS folder dialog since this app is local desktop software
    with full filesystem access -- an HTML file input can't return a real
    absolute path. Returns ok:false (never raises) if no dialog can be shown
    on this machine.
    """
    try:
        chosen = _ask_directory()
    except Exception:
        return JSONResponse({"ok": False, "path": None, "error": "no_dialog_available"})
    return JSONResponse({"ok": True, "path": chosen or None})


@router.post("/welcome/workspace")
def set_workspace(path: str = Form(...)):
    """Save the workspace path and seed it with default folders/files."""
    stripped = path.strip()
    if not stripped:
        return JSONResponse({"ok": False, "error": "Path cannot be empty"})
    config.set_workspace_path(stripped)
    root = workspace.ensure_workspace()
    return JSONResponse({
        "ok": True,
        "root": root,
        "subfolders": [
            workspace.LIBRARY_NAME, workspace.TO_REVIEW_NAME,
            workspace.CANVAS_UPLOADS_NAME,
            workspace.STUDENT_WORK_NAME, workspace.FOR_AI_NAME,
        ],
    })
