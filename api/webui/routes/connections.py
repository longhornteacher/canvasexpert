"""Local copy-only MCP connection guidance routes."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask

from api import __version__, ai_clients, connections, runtime_paths
from api.shared_storage import LegacyStorageReappearedError, reappeared_legacy_storage
from .. import mirror_service
from ..local_request_guard import csrf_token
from ..deps import templates


router = APIRouter(tags=["connections"])

_RETIRED_STORAGE_DETAIL = (
    "Canvas Expert found a file at a retired vault or settings location. "
    "Student-data access and shared settings are blocked; the file is never opened. "
    "Review Local workspace & privacy."
)


def _retired_storage_refusal() -> dict | None:
    """Return a safe Web UI status when the storage guard blocks access."""
    try:
        blocked = reappeared_legacy_storage()
    except LegacyStorageReappearedError:
        blocked = ["retired storage"]
    if blocked:
        return {"ok": False, "error": "legacy_storage_reappeared",
                "detail": _RETIRED_STORAGE_DETAIL}
    return None


def _page_setup_context() -> dict:
    """Keep setup controls renderable when health reads are refused."""
    return {
        "clients": ai_clients.clients_status(),
        "generic_stdio_config": connections.generic_stdio_config(),
    }


def _client_action(action) -> JSONResponse:
    """Run a connect/disconnect and map failures to teacher-readable JSON."""
    try:
        return JSONResponse({"ok": True, "status": action()})
    except ai_clients.ClientError as error:
        return JSONResponse(
            {"ok": False, "code": error.code, "detail": error.detail},
            status_code=409,
        )
    except Exception:
        return JSONResponse(
            {
                "ok": False,
                "code": "error",
                "detail": "Something went wrong writing the config file. Nothing was changed.",
            },
            status_code=500,
        )


def _cleanup(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _download_path(suffix: str) -> Path:
    directory = Path(runtime_paths.temp_dir())
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"CanvasExpert-{uuid4().hex}{suffix}"


@router.get("/", response_class=HTMLResponse)
def canvasagent_page(request: Request):
    refusal = _retired_storage_refusal()
    if refusal:
        connection = _page_setup_context()
        mirror = {**refusal, "status": "unavailable", "courses": []}
    else:
        try:
            connection = connections.connection_context()
        except LegacyStorageReappearedError:
            connection = _page_setup_context()
            refusal = {"ok": False, "error": "legacy_storage_reappeared",
                       "detail": _RETIRED_STORAGE_DETAIL}
        try:
            mirror = mirror_service.status()
        except LegacyStorageReappearedError:
            refusal = {"ok": False, "error": "legacy_storage_reappeared",
                       "detail": _RETIRED_STORAGE_DETAIL}
            mirror = {**refusal, "status": "unavailable", "courses": []}
    return templates.TemplateResponse(request, "canvasagent.html", {
        "nav_section": "connections",
        "connection": connection,
        "mirror": mirror,
        "csrf_token": csrf_token(),
    })


@router.get("/api/connections/health")
def connections_health():
    refusal = _retired_storage_refusal()
    if refusal:
        return JSONResponse(refusal)
    try:
        return JSONResponse(connections.connection_context()["health"])
    except LegacyStorageReappearedError:
        return JSONResponse({"ok": False, "error": "legacy_storage_reappeared",
                             "detail": _RETIRED_STORAGE_DETAIL})


@router.post("/api/connections/claude/connect")
def claude_connect():
    return _client_action(ai_clients.connect_claude)


@router.post("/api/connections/claude/disconnect")
def claude_disconnect():
    return _client_action(ai_clients.disconnect_claude)


@router.post("/api/connections/chatgpt/connect")
def chatgpt_connect():
    return _client_action(ai_clients.connect_chatgpt)


@router.post("/api/connections/chatgpt/disconnect")
def chatgpt_disconnect():
    return _client_action(ai_clients.disconnect_chatgpt)


@router.post("/api/connections/claude-package")
def claude_package():
    destination = _download_path(".mcpb")
    connections.build_claude_mcpb(destination)
    return FileResponse(
        destination,
        media_type="application/zip",
        filename=f"CanvasExpert-{__version__}.mcpb",
        background=BackgroundTask(_cleanup, destination),
    )
