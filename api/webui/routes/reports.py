"""Reports router for Canvas Expert.

Student Reports and local file operations.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

import requests

from api import nq_report, portfolio, portfolio_service, student_packet
from api.mirror import read_service, store as mirror_store
from api.platform_services import config, workspace
from api.platform_services.canvas_client import canvas_get_all, canvas_headers
from ..gradebook_service import _load_curve_events

router = APIRouter(prefix="/api", tags=["reports"])


# --------------------------------------------------------------------------
# Download settings + submission downloads
# --------------------------------------------------------------------------

@router.get("/download-root")
def get_download_root():
    return JSONResponse({"root": config.get_download_root()})


@router.get("/assignments-full")
def list_assignments_full(course_id: str):
    """All assignments for a course. Its last browser consumer went away with
    the due-date extension UI; kept as a catalog-backed read with no current
    caller.
    Returns id, name, submission_types, due_at, points_possible, is_quiz, quiz_kind.
    Served from Course Catalog when current; falls back to a live fetch otherwise.
    """
    assignment_scope = read_service.catalog_assignments(course_id, max_age_hours=None)
    group_scope = read_service.catalog_assignment_groups(course_id, max_age_hours=None)
    if assignment_scope["state"] == "current" and group_scope["state"] == "current":
        group_names = {g["id"]: g["name"] for g in group_scope["records"]}
        assignments = [
            {
                "id":                   record["id"],
                "name":                 record["name"],
                "submission_types":     record["submission_types"],
                "due_at":               record["due_at"][:10],
                "points_possible":      record["points_possible"],
                "quiz_id":              record["quiz_id"],
                "assignment_group_id":  record["assignment_group_id"],
                "assignment_group_name": group_names.get(record["assignment_group_id"], ""),
                "is_quiz":              record["is_quiz"],
                "quiz_kind":            record["quiz_kind"],
                "is_quiz_lti_assignment": record["is_quiz_lti_assignment"],
            }
            for record in assignment_scope["records"]
        ]
        assignments.sort(
            key=lambda a: a["due_at"] if a["due_at"] else "0000-00-00",
            reverse=True,
        )
        return JSONResponse({"ok": True, "assignments": assignments})

    hdrs, base = canvas_headers()
    if not hdrs:
        return JSONResponse({"ok": False, "error": "No token saved."})
    results, params = [], {"per_page": 100}
    url = f"{base}/api/v1/courses/{course_id}/assignments"
    while url:
        try:
            r = requests.get(url, headers=hdrs, params=params, timeout=20)
        except requests.RequestException as e:
            return JSONResponse({"ok": False, "error": str(e)})
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"HTTP {r.status_code}"})
        results.extend(r.json())
        params = {}
        url = None
        for part in r.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break
    # Pre-build a lookup for assignment group names from the full Canvas
    # response. The list endpoint often includes inline group name data.
    group_names: dict[str, str] = {}
    for a in results:
        gid = a.get("assignment_group_id")
        if gid is not None:
            gid_str = str(gid)
            # Canvas sometimes embeds a mini assignment_group object
            inline = a.get("assignment_group") or {}
            if isinstance(inline, dict) and inline.get("name"):
                group_names[gid_str] = inline["name"]
            elif gid_str not in group_names:
                group_names[gid_str] = ""  # will be resolved later

    def _is_quiz(a: dict) -> bool:
        st = a.get("submission_types") or []
        return bool(
            "online_quiz" in st
            or a.get("quiz_id") is not None
            or a.get("quiz_type") is not None
            or a.get("is_quiz_lti_assignment") is True  # New Quizzes
        )

    def _quiz_kind(a: dict) -> str:
        st = a.get("submission_types") or []
        qt = a.get("quiz_type") or ""
        if a.get("is_quiz_lti_assignment") is True:
            return "new_quiz"
        if "online_quiz" in st:
            if "new_quiz" in qt.lower() or qt.lower() == "quizzes.next":
                return "new_quiz"
            # external_tool with a quiz-like URL can hint new quiz
            ext = a.get("external_tool_tag_attributes") or {}
            if isinstance(ext, dict):
                url = (ext.get("url") or "").lower()
                content_type = (ext.get("content_type") or "").lower()
                if "quiz" in url or "quiz" in content_type or "new_quiz" in content_type:
                    return "new_quiz"
            return "classic_quiz"
        if a.get("quiz_id") is not None or a.get("quiz_type") is not None:
            return "quiz"
        return ""

    assignments = [
        {
            "id":                   str(a["id"]),
            "name":                 a.get("name", ""),
            "submission_types":     a.get("submission_types") or [],
            "due_at":               (a.get("due_at") or "")[:10],
            "points_possible":      a.get("points_possible"),
            "quiz_id":              str(a.get("quiz_id") or ""),
            "assignment_group_id":  str(a.get("assignment_group_id") or ""),
            "assignment_group_name":
                group_names.get(str(a["assignment_group_id"]), "")
                if a.get("assignment_group_id") is not None else "",
            "is_quiz":              _is_quiz(a),
            "quiz_kind":            _quiz_kind(a),
            "is_quiz_lti_assignment": a.get("is_quiz_lti_assignment") is True,
        }
        for a in results
    ]
    assignments.sort(
        key=lambda a: a["due_at"] if a["due_at"] else "0000-00-00",
        reverse=True,
    )
    return JSONResponse({"ok": True, "assignments": assignments})


@router.get("/course-folder")
def course_folder(course_name: str, course_id: str = ""):
    """Return the local download folder path for a course and whether it exists."""
    if not course_id:
        for course in config.active_courses():
            if course.get("name") == course_name or course.get("nickname") == course_name:
                course_id = str(course.get("id") or "")
                break
    path = workspace.course_folder(course_name, course_id or "unknown")
    return JSONResponse({"path": path, "exists": os.path.isdir(path)})


# --------------------------------------------------------------------------
# Student Reports
# --------------------------------------------------------------------------

@router.get("/students")
def api_students(course_id: str):
    """Roster for one course, annotated with the machine-local monitored flag.
    Served from the local roster mirror when current; falls back to live."""
    roster_scope = read_service.private_roster(course_id)
    if roster_scope["state"] == "current" and isinstance(roster_scope.get("records"), list):
        users = roster_scope["records"]
        has_ids = all(isinstance(u, dict) and u.get("id") is not None for u in users)
    else:
        has_ids = False

    if has_ids:
        pass  # records already in hand
    else:
        users, err = canvas_get_all(f"/api/v1/courses/{course_id}/users",
                                     {"enrollment_type[]": "student", "per_page": 100})
        if err:
            return JSONResponse({"ok": False, "error": err})
    mon = config.get_monitored_students()
    out = [{"id": str(u["id"]),
            "name": u.get("sortable_name") or u.get("name", ""),
            "monitored": str(u["id"]) in mon} for u in (users or [])]
    out.sort(key=lambda s: s["name"].lower())
    return JSONResponse({"ok": True, "students": out})


@router.post("/students/monitor")
def api_students_monitor(user_id: str = Form(...), name: str = Form(...),
                         monitored: str = Form(...), note: str = Form("")):
    if monitored == "true":
        config.set_monitored_student(user_id, name, note)
    else:
        config.remove_monitored_student(user_id)
    return JSONResponse({"ok": True})


@router.get("/students/monitored")
def api_students_monitored():
    mon = config.get_monitored_students()
    return JSONResponse({"ok": True, "students":
        [{"user_id": uid, "name": v["name"]} for uid, v in mon.items()]})


@router.get("/student-packet/stream")
def api_student_packet_stream(user_id: str, student_name: str,
                              sections: str = "", course_ids: str = ""):
    """SSE: build one student's packet across the given (or all active) courses."""
    secs = [s for s in sections.split(",") if s] or student_packet.SECTIONS
    if course_ids:
        want = {c.strip() for c in course_ids.split(",") if c.strip()}
        courses = [c for c in config.active_courses() if str(c["id"]) in want]
    else:
        courses = config.active_courses()
    base, token = config.get_canvas_base(), config.get_token()
    if not token:
        return StreamingResponse(_sse(["!! no token", "[exit 1]"]),
                                 media_type="text/event-stream")

    def lines():
        try:
            yield from student_packet.build_packet(
                user_id, student_name, secs,
                [{"id": c["id"], "name": c["name"]} for c in courses],
                base, token, config.get_student_reports_root(),
                _load_curve_events(), skip_unchanged=False)
            yield "[exit 0]"
        except Exception as e:
            yield f"!! {e}"
            yield "[exit 1]"

    return StreamingResponse(_sse(lines()), media_type="text/event-stream")


# --------------------------------------------------------------------------
# New Quizzes writing portfolio (from a manually-downloaded Student Analysis CSV)
# --------------------------------------------------------------------------

@router.post("/portfolio/from-nq-csv")
async def portfolio_from_nq_csv(file: UploadFile = File(...),
                                quiz_title: str = Form("")):
    """Parse an uploaded New Quizzes 'Student Analysis' CSV and render one writing
    portfolio DOCX per student into the synced Student Reports folder.

    The CSV is parsed in memory and never written to disk; only the per-student
    DOCX outputs land in the (FERPA-conscious, gitignored/synced) reports root.
    """
    title = (quiz_title or "").strip() or os.path.splitext(file.filename or "")[0] or "New Quiz"
    try:
        raw = await file.read()
        text = raw.decode("utf-8-sig", errors="replace")
        data = nq_report.parse_student_analysis(text)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Could not read CSV: {e}"})

    students = data.get("students") or []
    if not students:
        return JSONResponse({"ok": False,
                             "error": "No student rows found — is this a New Quizzes "
                                      "Student Analysis CSV?"})

    out_dir = os.path.join(config.get_student_reports_root(),
                           workspace.safe_component(title) + " - Portfolios")
    try:
        written = portfolio.render_portfolio(data, out_dir, quiz_title=title)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Render failed: {e}"})

    return JSONResponse({"ok": True, "folder": out_dir,
                         "count": len(written), "quiz_title": title,
                         "students": [s.get("name", "") for s in students]})


@router.post("/portfolio/merged")
async def portfolio_merged(course_id: str = Form(...),
                           cohort: str = Form("monitored"),
                           quiz_title: str = Form(""),
                           date_from: str = Form(""),
                           date_to: str = Form(""),
                           file: UploadFile = File(None)):
    """Build one merged, chronological writing portfolio per student: Assignment text
    entries + uploaded files/photos (live) plus New Quizzes responses (optional CSV,
    matched by Canvas id). Synchronous — scope to the monitored cohort for speed.
    """
    token, base = config.get_token(), config.get_canvas_base()
    if not token:
        return JSONResponse({"ok": False, "error": "No Canvas token saved."})

    course = {"id": course_id, "name": config.course_display_name(course_id) or str(course_id)}

    if cohort == "monitored":
        students = [{"id": uid, "name": v["name"]}
                    for uid, v in config.get_monitored_students().items()]
    else:
        roster_scope = read_service.private_roster(course_id)
        if roster_scope["state"] == "current" and isinstance(roster_scope.get("records"), list):
            users = roster_scope["records"]
        else:
            users, err = canvas_get_all(f"/api/v1/courses/{course_id}/users",
                                         {"enrollment_type[]": "student", "per_page": 100})
            if err:
                return JSONResponse({"ok": False, "error": err})
        students = [{"id": str(u["id"]),
                     "name": u.get("sortable_name") or u.get("name", "")}
                    for u in (users or [])]
    if not students:
        return JSONResponse({"ok": False, "error": "No students in the selected cohort."})

    parsed_nq, title = None, (quiz_title or "").strip()
    if file is not None and file.filename:
        try:
            parsed_nq = nq_report.parse_student_analysis(
                (await file.read()).decode("utf-8-sig", errors="replace"))
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Could not read CSV: {e}"})
        if not title:
            title = os.path.splitext(file.filename)[0]

    log, folder = [], config.get_student_reports_root()
    try:
        for line in portfolio_service.build_merged_portfolios(
                course, students, parsed_nq, title, base, token, folder,
                date_from.strip() or None, date_to.strip() or None):
            if line.startswith("FOLDER: "):
                folder = line[len("FOLDER: "):]
            else:
                log.append(line)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "log": log})

    made = sum(1 for ln in log if ln.startswith("✓"))
    return JSONResponse({"ok": True, "folder": folder, "count": made, "log": log})


# --------------------------------------------------------------------------
# Local file operations
# --------------------------------------------------------------------------

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


@router.post("/open-file")
def open_file(path: str = Form(...)):
    """Open a local file with its default app (local server only) — used by the
    Feedback tools manual lane to open a pseudonymized bundle for MagicSchool/Copilot."""
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        return JSONResponse({"ok": False, "error": "File not found."})
    try:
        os.startfile(path)  # noqa: Windows-only; this app is local Windows-only
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.post("/pick-download-folder")
def pick_download_folder():
    """Native folder picker → set as the download root (local app only)."""
    cur = config.get_download_root() or ""
    script = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        "p = filedialog.askdirectory(initialdir=sys.argv[1] or None,\n"
        "                            title='Choose download location')\n"
        "sys.stdout.write(p or '')\n"
    )
    try:
        out = subprocess.run([sys.executable, "-c", script, cur],
                             capture_output=True, text=True, timeout=300)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
    chosen = (out.stdout or "").strip()
    if not chosen:
        return JSONResponse({"ok": False, "cancelled": True})
    config.set_download_root(chosen)
    return JSONResponse({"ok": True, "root": chosen})


