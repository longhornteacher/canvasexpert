"""Local copy-only MCP connection guidance routes."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask

from api import __version__, ai_clients, connections, runtime_paths
from .. import mirror_service
from ..local_request_guard import csrf_token
from ..deps import templates


router = APIRouter(tags=["connections"])


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
    return templates.TemplateResponse(request, "canvasagent.html", {
        "nav_section": "connections",
        "connection": connections.connection_context(),
        "mirror": mirror_service.status(),
        "csrf_token": csrf_token(),
    })


@router.get("/api/connections/health")
def connections_health():
    return JSONResponse(connections.connection_context()["health"])


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
