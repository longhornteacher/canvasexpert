"""Settings routes for Canvas Expert.

One APIRouter; all 6 POST settings routes (Canvas account, course bookmarks,
download root, connection test). All are thin pass-throughs to config.*.

Routes: POST /settings/canvas
        POST /settings/courses/bookmark
        POST /settings/courses/{course_id}/remove
        POST /settings/courses/{course_id}/set-active
        POST /settings/download-root
        POST /settings/test-connection
"""
import requests

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from api.platform_services import config

router = APIRouter(tags=["settings"])

@router.post("/settings/canvas")
def save_canvas_account(base_url: str = Form(...), token: str = Form("")):
    config.save_canvas_account(base_url, token or None)
    return JSONResponse({"ok": True})


@router.post("/settings/courses/bookmark")
def bookmark_course(course_id: str = Form(...), course_name: str = Form(...),
                    nickname: str = Form("")):
    config.bookmark_course(course_id, course_name, nickname)
    return JSONResponse({"ok": True})


@router.post("/settings/courses/{course_id}/remove")
def remove_course(course_id: str):
    config.remove_course(course_id)
    return JSONResponse({"ok": True})


@router.post("/settings/courses/{course_id}/set-active")
def set_course_active(course_id: str, active: str = Form(...)):
    config.set_course_active(course_id, active.lower() in ("true", "1", "yes"))
    return JSONResponse({"ok": True})


@router.post("/settings/download-root")
def save_download_root(path: str = Form(...)):
    config.set_download_root(path.strip())
    return JSONResponse({"ok": True})


@router.post("/settings/test-connection")
def test_connection(base_url: str = Form(...), token: str = Form("")):
    """Read-only probe — verifies token+base before saving.

    When `token` is omitted (empty string), falls back to the token already
    stored in the OS keychain. This lets the teacher hit "Test connection"
    without re-entering the token they saved previously.
    """
    base = base_url.rstrip("/")
    effective_token = token or config.get_token()
    if not effective_token:
        return JSONResponse({"ok": False,
                             "error": "No token provided and none saved yet. Paste a token first."})
    try:
        r = requests.get(f"{base}/api/v1/users/self",
                         headers={"Authorization": f"Bearer {effective_token}"},
                         timeout=15)
    except requests.RequestException as e:
        return JSONResponse({"ok": False, "error": str(e)})
    if r.status_code != 200:
        return JSONResponse({"ok": False,
                             "error": f"HTTP {r.status_code}: {r.text[:300]}"})
    d = r.json()
    return JSONResponse({"ok": True,
                         "display_name": d.get("name", d.get("short_name", "Unknown"))})
