"""Loopback ASGI host shared by MCP-first and console-first entry points."""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route


console_app = None
_console_import_error_logged = False


async def _ping(_request: Request):
    return JSONResponse({"ok": True, "service": "canvas-expert"})


def create_host_app():
    """Create the minimal host; a broken optional console cannot disable MCP."""
    global console_app, _console_import_error_logged
    web_app = None
    try:
        from api.webui.server import app as web_app

        console_app = web_app
    except Exception as exc:
        console_app = None
        if not _console_import_error_logged:
            print(
                f"Canvas Expert console unavailable ({type(exc).__name__}); MCP remains available.",
                file=sys.stderr,
            )
            _console_import_error_logged = True

    from api.mcp_server.server import mcp

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    from starlette.responses import PlainTextResponse

    async def _not_found(_scope, _receive, send):
        response = PlainTextResponse("Not Found", status_code=404)
        await response(_scope, _receive, send)

    routes = [
        Route("/api/runtime/ping", _ping, methods=["GET"]),
        Route("/mcp", endpoint=mcp_app),
        Route("/mcp/", endpoint=mcp_app),
    ]
    if web_app is not None:
        routes.append(Mount("/", app=web_app))
    else:
        routes.append(Mount("/", app=_not_found))

    return Starlette(
        lifespan=lifespan,
        routes=routes,
    )


def mounted_console_app():
    """Return the imported child app so entry points can set its restart hook."""
    return console_app
