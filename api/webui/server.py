"""Local setup, readiness, private names, recovery, and receipts console."""
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from api import operational_log
from api.mirror import coordinator as _mirror_coordinator

from .deps import WEBUI_DIR

from .routes.courses import router as _courses_router
from .routes.names import names_router as _names_router
from .routes.library import router as _library_router
from .routes.onboarding import router as _onboarding_router
from .routes.pages import router as _pages_router
from .routes.reports import router as _reports_router
from .routes.settings import router as _settings_router
from .routes.readiness import router as _readiness_router
from .routes.receipts import router as _receipts_router
from .routes.connections import router as _connections_router
from .routes.support import router as _support_router
from .routes.operations import router as _operations_router
from .routes.mirror import router as _mirror_router
from .routes.updates import router as _updates_router


class _StaticFiles(StaticFiles):
    """Serve bundled fonts to sandboxed iframes without diagnostics."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("fonts/"):
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response


app = FastAPI(title="Canvas Expert",
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", _StaticFiles(directory=os.path.join(WEBUI_DIR, "static")), name="static")

@app.middleware("http")
async def _foreground_gate(request: Request, call_next):
    # Yield background Canvas reads while the teacher uses the console.
    with _mirror_coordinator.foreground_interval():
        return await call_next(request)


@app.exception_handler(Exception)
async def _api_errors_return_json(request: Request, exc: Exception):
    """Keep the local API contract 'always JSON'.

    Every /api/ route is consumed by fetch() callers that parse the body with
    response.json(). Without this, an unhandled exception falls through to
    Starlette's default 500 handler, whose body is the plain text
    'Internal Server Error'; the browser's response.json() then raises the
    misleading 'Unexpected token I ... is not valid JSON', masking the true
    cause. Convert unhandled errors on API routes into a structured payload so
    the real reason reaches the teacher, and always print the traceback to the
    server console for debugging. Non-API (HTML) routes keep the plain-text 500.

    The error payload uses a fixed generic message and never exposes the
    exception type, message, absolute path, student detail, setting, or
    credential.  The traceback is still printed locally for debugging.
    """
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(formatted)
    operational_log.write_traceback(formatted)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"ok": False, "error": "An unexpected server error occurred. Check the server console for details."},
            status_code=500,
        )
    return PlainTextResponse("Internal Server Error", status_code=500)


app.include_router(_onboarding_router)
app.include_router(_courses_router)
app.include_router(_names_router)
app.include_router(_library_router)
app.include_router(_pages_router)
app.include_router(_reports_router)
app.include_router(_settings_router)
app.include_router(_readiness_router)
app.include_router(_receipts_router)
app.include_router(_connections_router)
app.include_router(_support_router)
app.include_router(_operations_router)
app.include_router(_mirror_router)
app.include_router(_updates_router)
