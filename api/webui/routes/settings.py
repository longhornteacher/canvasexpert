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

from api import pseudonym_secret
from api.identity_ledger import SeedMismatchError
from api.identity_vault_service import open_vault
from api.platform_services import config
from api.shared_storage import SharedStoreConflictError
from api.shared_vault import PseudonymSecretRequired

router = APIRouter(tags=["settings"])


@router.get("/settings/identity-vault")
def identity_vault_status():
    """Return only local key status and a short fingerprint, never the key."""
    try:
        vault = open_vault()
        secret = pseudonym_secret.get_secret()
        return JSONResponse({
            "ok": True,
            "configured": secret is not None,
            "fingerprint": pseudonym_secret.fingerprint(secret),
            "conflicts": len(vault.conflicts()),
        })
    except SeedMismatchError as error:
        return JSONResponse({
            "ok": False, "error": "identity_seed_mismatch",
            "local_fingerprint": error.local_fingerprint,
            "shared_fingerprint": error.shared_fingerprint,
        })
    except SharedStoreConflictError:
        return JSONResponse({"ok": False, "error": "shared_workspace_conflict"})
    except PseudonymSecretRequired:
        return JSONResponse({"ok": True, "configured": False, "fingerprint": "", "conflicts": 0})
    except Exception:
        return JSONResponse({"ok": False, "error": "identity_vault_unavailable"})


@router.post("/settings/identity-vault-secret")
def update_identity_vault_secret(action: str = Form(...), secret: str = Form("")):
    """Manually reveal the existing transfer key or save one in Credential Manager."""
    if action == "reveal":
        try:
            value = pseudonym_secret.get_secret()
        except Exception:
            return JSONResponse({"ok": False, "error": "credential_store_unavailable"})
        if value is None:
            return JSONResponse({"ok": False, "error": "pseudonym_secret_not_configured"})
        return JSONResponse({
            "ok": True,
            "secret": value.hex(),
            "fingerprint": pseudonym_secret.fingerprint(value),
        })
    if action == "save":
        try:
            result = pseudonym_secret.set_secret(secret)
        except Exception:
            return JSONResponse({"ok": False, "error": "credential_store_unavailable"})
        return JSONResponse(result)
    return JSONResponse({"ok": False, "error": "unknown_action"})

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
