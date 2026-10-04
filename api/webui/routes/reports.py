"""Local workspace folder controls for the Settings console."""
import os
import subprocess

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["workspace"])


@router.post("/open-folder")
def open_folder(path: str = Form(...)):
    """Open a local folder in Windows Explorer (local server only)."""
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    try:
        subprocess.Popen(["explorer", path])
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
