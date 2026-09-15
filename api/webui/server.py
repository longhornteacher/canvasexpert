"""Local web UI for Canvas Expert (the live/token half of the platform).

QuizForge is one tool that plugs into this platform; UnitForge and future
forge tools will plug in alongside it.

One Canvas token (stored in the OS keychain, set once on the Settings page)
covers all of the teacher's courses. The teacher picks which course to target
from a live dropdown (or from their saved bookmarks) on the dashboard — no
more separate "profiles" for each class.

QuizForge planning and validation may delegate to existing CLI scripts as
subprocesses with credentials injected via environment variables. Live
differentiated delivery uses the reviewed Operation Ledger path. See runner.py.
"""
import os
import threading
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api import operational_log, student_packet
from api.mirror import coordinator as _mirror_coordinator
from api.operation_ledger import recovery as _operation_ledger_recovery

from api.platform_services import config
from . import af, ai_ta, pf, runner
from api.platform_services import workspace
from api import runtime_paths
from api.platform_services.canvas_client import canvas_headers, canvas_get, canvas_get_all, _canvas_send
from .schooldays import (
    parse_iso_local, _is_school_day, _school_days_late,
    school_days_late_detail, _add_school_days,
)

from .deps import (
    WEBUI_DIR, API_DIR, REPO_ROOT,
    templates,
    _CUSTOM_DIR, list_quiz_files, list_assignment_files, list_page_files,
    list_ai_ta_files,
)

from .routes.calendar import router as _calendar_router
from .routes.schedule import router as _schedule_router
from .routes.courses import router as _courses_router
from .routes.course_catalog import router as _course_catalog_router
from .routes.dailywriting import router as _dailywriting_router
from .routes.feedback import router as _feedback_router
from .routes.names import names_router as _names_router
from .routes.gradebook import router as _gradebook_router
from .routes.library import router as _library_router
from .routes.onboarding import router as _onboarding_router
from .routes.pages import router as _pages_router
from .routes.push import router as _push_router
from .routes.reports import router as _reports_router
from .routes.routines import router as _routines_router, _load_custom_routines, _routines_heartbeat
from .routes.roster import router as _roster_router
from .routes.settings import router as _settings_router
from .routes.readiness import router as _readiness_router
from .routes.receipts import router as _receipts_router
from .routes.connections import router as _connections_router
from .routes.support import router as _support_router
from .routes.work import router as _work_router
from .routes.operations import router as _operations_router
from .routes.mirror import router as _mirror_router
from .routes.updates import router as _updates_router
from .mirror_service import _mirror_heartbeat


class _StaticFiles(StaticFiles):
    """Serve bundled fonts to sandboxed iframes without diagnostics."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("fonts/"):
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response


@asynccontextmanager
async def _lifespan(app):
    """Startup work — kept out of module import so the app is cheap to import
    (route-contract test, tooling). uvicorn fires this when actually serving."""
    try:
        workspace.ensure_workspace()
    except Exception as e:
        print(f"Workspace setup note: {e}")
    try:
        workspace.migrate_legacy_panels_folder()
    except Exception as e:
        print(f"Legacy Panels folder migration note: {e}")
    try:
        # Pin the resolved workspace path so the headless MCP server (launched by
        # Claude Desktop / ChatGPT without the OneDrive env var) resolves the same
        # workspace instead of falling back to stale machine-local state.
        config.ensure_workspace_pinned()
    except Exception as e:
        print(f"Workspace pin note: {e}")
    try:
        ai_ta.build_library(runtime_paths.ai_ta_dir())
    except Exception as e:
        print(f"AI Authoring library build failed: {e}")
    try:
        # Reconcile any operation-ledger targets left claimed/sent_unknown by a
        # crash mid-write, before the routines heartbeat can claim the same
        # targets for new work. Usually a no-op (empty scan).
        _operation_ledger_recovery.recover_pending_operations()
    except Exception as e:
        print(f"Operation-ledger recovery note: {e}")
    _load_custom_routines()
    threading.Thread(target=_routines_heartbeat, daemon=True).start()
    threading.Thread(target=_mirror_heartbeat, daemon=True).start()
    yield


app = FastAPI(title="Canvas Expert", lifespan=_lifespan)
app.mount("/static", _StaticFiles(directory=os.path.join(WEBUI_DIR, "static")), name="static")

# ── Onboarding gate ──────────────────────────────────────────────────────
# If Canvas URL or token is not yet configured, redirect HTML page requests
# to the /welcome wizard. Never gate API/static endpoints or the wizard itself.

_ALLOWLIST_PREFIXES = ("/welcome", "/settings", "/static", "/api", "/openapi.json", "/docs", "/redoc")


@app.middleware("http")
async def _onboarding_gate(request: Request, call_next):
    # The interval covers routing, response construction, and every local API
    # request.  Background Canvas GET workers inspect this shared gate before
    # each physical request and cooperatively yield to the teacher.
    with _mirror_coordinator.foreground_interval():
        if not config.token_is_set() or not config.get_canvas_base():
            path = request.url.path
            wants_html = "text/html" in request.headers.get("accept", "")
            allowlisted = path == "/" or any(path.startswith(p) for p in _ALLOWLIST_PREFIXES)
            if wants_html and not allowlisted:
                from fastapi.responses import RedirectResponse
                return RedirectResponse(url="/welcome", status_code=303)
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
app.include_router(_calendar_router)
app.include_router(_schedule_router)
app.include_router(_courses_router)
app.include_router(_course_catalog_router)
app.include_router(_dailywriting_router)
app.include_router(_feedback_router)
app.include_router(_names_router)
app.include_router(_gradebook_router)
app.include_router(_library_router)
app.include_router(_pages_router)
app.include_router(_push_router)
app.include_router(_reports_router)
app.include_router(_routines_router)
app.include_router(_roster_router)
app.include_router(_settings_router)
app.include_router(_readiness_router)
app.include_router(_receipts_router)
app.include_router(_connections_router)
app.include_router(_support_router)
app.include_router(_work_router)
app.include_router(_operations_router)
app.include_router(_mirror_router)
app.include_router(_updates_router)
