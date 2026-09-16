"""HTML page routes for Canvas Expert.

One APIRouter; all 9 GET page routes + the /api/open-path utility POST.
Imported by server.py via app.include_router(router).

Routes: GET /, /about, /ai-expert, /course, /course-expert,
        /gradebook, /roster, /routines, /settings
        POST /api/open-path
"""
import glob
import json
import os
import subprocess
import sys

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from api.platform_services import config, workspace
from .. import deps
from api import operational_log, runtime_paths
from ..local_request_guard import csrf_token
from ..deps import (
    API_DIR, REPO_ROOT, _CUSTOM_DIR, templates,
    list_ai_ta_files, list_assignment_files,
    list_page_files, list_quiz_files,
)

router = APIRouter(tags=["pages"])


# --------------------------------------------------------------------------
# Page routes
# --------------------------------------------------------------------------

def _routines_template_context() -> dict:
    """Template values shared by the standalone route and the Gradebook tab."""
    def _entry(name):
        return {"name": name, "path": os.path.normpath(os.path.join(_CUSTOM_DIR, name))}

    custom_active, custom_templates = [], []
    if os.path.isdir(_CUSTOM_DIR):
        for path in sorted(glob.glob(os.path.join(_CUSTOM_DIR, "*.py"))):
            name = os.path.basename(path)
            (custom_templates if name.startswith("_") else custom_active).append(_entry(name))

    return {
        "custom_dir":       os.path.normpath(_CUSTOM_DIR),
        "authoring_path":   os.path.normpath(os.path.join(_CUSTOM_DIR, "AUTHORING.md")),
        "custom_active":    custom_active,
        "custom_templates": custom_templates,
        "active_count":     len(config.active_courses()),
    }


@router.get("/course-expert", response_class=HTMLResponse)
def course_expert_page(request: Request):
    if request.query_params.get("tab") == "students":
        return RedirectResponse("/roster?focus=reports", status_code=307)
    skills = list_ai_ta_files()
    return templates.TemplateResponse(request, "course_expert.html", {
        **_push_base_ctx(request),
        "quiz_files":       list_quiz_files(),
        "assignment_files": list_assignment_files(),
        "page_files":       list_page_files(),
        "authoring_skills": {
            "quiz":       _authoring_skill(skills, "Author a Quiz"),
            "assignment": _authoring_skill(skills, "Author an Assignment"),
            "page":       _authoring_skill(skills, "Author a Page"),
        },
    })


@router.get("/students/reports")
def student_reports_page():
    """Reports are a view inside the Students page; keep old links working."""
    return RedirectResponse("/roster?focus=reports", status_code=307)


def _authoring_skill(skills: list, prefix: str) -> str:
    for f in skills:
        if f["label"].startswith(prefix):
            return f["label"]
    return ""


def _push_base_ctx(request: Request) -> dict:
    return {
        "nav_section":   "create",
        "token_is_set":  config.token_is_set(),
        "canvas_base":   config.get_canvas_base(),
        "saved_courses": config.active_courses(),
        "csrf_token":    csrf_token(),
    }


@router.get("/ai-expert", response_class=HTMLResponse)
def ai_expert_page(request: Request):
    ai_ta_files = list_ai_ta_files()
    ai_ta_dir = runtime_paths.ai_ta_dir()
    toolkit_dir = os.path.join(ai_ta_dir, "MagicSchool Toolkit")
    toolkit_files = []
    if os.path.isdir(toolkit_dir):
        canonical_toolkit_dir = os.path.join(REPO_ROOT, "api", "default_docs", "AI Authoring", "MagicSchool Toolkit")
        toolkit_files = sorted(
            os.path.basename(p)
            for p in glob.glob(os.path.join(toolkit_dir, "*.txt"))
            if os.path.isfile(os.path.join(canonical_toolkit_dir, os.path.basename(p)))
        )
    return templates.TemplateResponse(request, "ai_expert.html", {
        "nav_section":    "help",
        "token_is_set":   config.token_is_set(),
        "ai_ta_files":    ai_ta_files,
        "ai_ta_dir":      str(ai_ta_dir),
        "toolkit_files":  toolkit_files,
        "workspace_root": workspace.workspace_root(),
    })


@router.get("/roster", response_class=HTMLResponse)
def roster_page(request: Request):
    """Roster Console — unified student settings surface."""
    return templates.TemplateResponse(request, "roster.html", {
        "nav_section":   "manage",
        "token_is_set":  config.token_is_set(),
        "canvas_base":   config.get_canvas_base(),
        "saved_courses": config.active_courses(),
    })


@router.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {"nav_section": "help"})


@router.get("/course", response_class=HTMLResponse)
def course_page(request: Request, course_id: str = ""):
    """Detailed Course Info page — roster, groups, modules, assignments."""
    return templates.TemplateResponse(request, "course.html", {
        "nav_section":    "manage",
        "token_is_set":   config.token_is_set(),
        "canvas_base":    config.get_canvas_base(),
        "saved_courses":  config.active_courses(),
        "selected_id":    course_id,
        "history":        recent_pushes(),
    })


@router.get("/gradebook", response_class=HTMLResponse)
def gradebook_page(request: Request):
    return templates.TemplateResponse(request, "gradebook.html", {
        "nav_section":          "grade",
        "csrf_token":           csrf_token(),
        "token_is_set":         config.token_is_set(),
        "canvas_base":          config.get_canvas_base(),
        "saved_courses":        config.active_courses(),
        **_routines_template_context(),
    })


@router.get("/routines", response_class=HTMLResponse)
def routines_page(request: Request):
    """Routines — local automations (built-in + custom) and how to add your own.

    Scans the custom_routines folder so the page can show the real path and the
    files it found (active vs. _-prefixed templates)."""
    return templates.TemplateResponse(request, "routines.html", {
        "nav_section":      "automate",
        "token_is_set":     config.token_is_set(),
        **_routines_template_context(),
    })


def _mirror_relative(age_hours) -> str:
    """Human 'synced N ago' from an age in hours (None → never synced)."""
    if age_hours is None:
        return "not yet"
    minutes = int(age_hours * 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = int(age_hours)
    if hours < 24:
        return f"{hours} h ago"
    return f"{int(age_hours // 24)} d ago"


def _mirror_settings_context() -> dict:
    """Read-only CanvasMirror freshness for the Settings panel. Never raises."""
    default = {
        "mirror_enabled": False, "mirror_configured": False,
        "mirror_courses": [], "mirror_serve_max_age_hours": 6,
    }
    try:
        from .. import mirror_service
        from api.mirror import store as mirror_store
        status = mirror_service.status()
        now = mirror_store.now_iso()
    except Exception:
        return default
    courses = []
    for course in status.get("courses", []) if isinstance(status, dict) else []:
        passes = course.get("passes", {}) if isinstance(course, dict) else {}
        full = (passes.get("full") or {}).get("last_success_at", "")
        delta = (passes.get("delta") or {}).get("last_success_at", "")
        newest = max(full, delta)  # ISO-Z strings compare lexically
        age = mirror_store.age_hours(newest, now) if newest else None
        courses.append({
            "name": course.get("course_name") or course.get("course_id") or "Course",
            "synced_relative": _mirror_relative(age),
        })
    serve = status.get("serve_max_age_hours", 6)
    if isinstance(serve, float) and serve.is_integer():
        serve = int(serve)
    return {
        "mirror_enabled": bool(status.get("enabled")),
        "mirror_configured": bool(status.get("workspace_configured")),
        "mirror_courses": courses,
        "mirror_serve_max_age_hours": serve,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    root = workspace.workspace_root()
    saved_courses = config.saved_courses()
    return templates.TemplateResponse(request, "settings.html", {
        **_mirror_settings_context(),
        "nav_section":   "settings",
        "canvas_base":   config.get_canvas_base(),
        "token_is_set":  config.token_is_set(),
        "saved_courses": saved_courses,
        "current_courses": [course for course in saved_courses if course.get("active", True)],
        "previous_courses": [course for course in saved_courses if not course.get("active", True)],
        "base_default":  config.CANVAS_BASE_DEFAULT,
        "download_root": config.get_download_root(),
        "ai_ta_dir":     str(runtime_paths.ai_ta_dir()),
        "workspace_root": root,
        "workspace_files": [
            {"name": "Library / AI Authoring", "path": workspace.library_folder("AI Authoring")},
            {"name": "Library / Quizzes", "path": workspace.library_folder("Quizzes")},
            {"name": "Library / Assignments", "path": workspace.library_folder("Assignments")},
            {"name": "Library / Pages", "path": workspace.library_folder("Pages")},
            {"name": "Library / Source Materials", "path": workspace.library_folder("Source Materials")},
            {"name": "To Review", "path": workspace.to_review_root()},
            {"name": "Printables", "path": workspace.printables_root()},
            {"name": "Canvas Uploads", "path": workspace.canvas_uploads_root()},
            {"name": "Student Work (PRIVATE)", "path": workspace.student_work_root()},
            {"name": "For AI (review before sharing)", "path": workspace.for_ai_root()},
            {"name": "_System (PRIVATE)", "path": workspace.system_root()},
        ],
        "computer_name": os.environ.get("COMPUTERNAME", "this PC"),
        "tier_tags": config.get_tier_tags(),
        "workspace_status": (root or "no OneDrive found — using local folders"),
        "workspace_is_local": not bool(root),
    })


# --------------------------------------------------------------------------
# OS file-reveal utility (used from multiple page UIs)
# --------------------------------------------------------------------------

def _open_in_os(path):
    """Open a file or folder in the OS file manager. Windows-first."""
    if sys.platform.startswith("win"):
        os.startfile(path)                      # noqa: S606 — local-only desktop app
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _allowed_open_roots():
    """Real paths the open-path endpoint may reveal — app-known roots only."""
    candidates = [_CUSTOM_DIR, config.get_download_root(),
                  config.get_student_reports_root(), workspace.workspace_root(),
                  os.path.join(REPO_ROOT, "Finished_Exports")]
    roots = []
    for r in candidates:
        if not r:
            continue
        try:
            roots.append(os.path.realpath(r))
        except Exception as exc:
            operational_log.emit("pages.allowed_roots_resolve", "failed", error_class=type(exc))
    return roots


@router.post("/api/open-path")
def api_open_path(path: str = Form(...)):
    """Reveal a file/folder in the OS file manager. Local-only desktop app, but
    restricted to existing paths inside app-known roots so a stray localhost POST
    can't launch an arbitrary executable.

    Only invokes the OS opener with plain (unprefixed) paths that fit within
    the teacher-visible budget.  A legacy deep path is resolved through the
    extended fallback for containment/read purposes, but the opener action
    is refused with a fixed safe error if the plain form exceeds the limit.
    """
    # Resolve through extended fallback for containment checks
    rp = os.path.realpath(workspace.extended_path(path))
    plain_rp = os.path.abspath(path)
    if not os.path.exists(rp):
        return JSONResponse({"ok": False, "error": "The file or folder was not found."})
    inside = False
    for root in _allowed_open_roots():
        try:
            if os.path.commonpath([os.path.normcase(rp), os.path.normcase(root)]) == os.path.normcase(root):
                inside = True
                break
        except ValueError:
            continue
    if not inside:
        return JSONResponse({"ok": False, "error": "The path is not inside the workspace."})
    # Only open with the plain (unprefixed) path; reject if it exceeds budget
    if len(plain_rp) > workspace.TEACHER_VISIBLE_BUDGET:
        return JSONResponse(
            {"ok": False, "error": "The path is too deep to open in the file manager."}
        )
    try:
        _open_in_os(plain_rp)
        return JSONResponse({"ok": True})
    except Exception:
        return JSONResponse({"ok": False, "error": "Could not open the file or folder."})


# --------------------------------------------------------------------------
# Page-private helpers
# --------------------------------------------------------------------------

def recent_pushes(limit=10):
    p = os.path.join(API_DIR, ".experiment_state.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        state = json.load(f)
    return list(reversed(state.get("quizzes", [])))[:limit]
