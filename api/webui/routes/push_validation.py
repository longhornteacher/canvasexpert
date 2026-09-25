"""Validation, physical-render, and dry-run preview routes used by the push UI."""
import os
import uuid as _uuid

from fastapi import File, Form, UploadFile
from fastapi.responses import JSONResponse

from api.platform_services import config
from .. import af, pf, runner
from api import operational_log, runtime_paths
from ..deps import TEMP_DIR, REPO_ROOT


def register_validation_routes(
    router,
    printables_dir_func=runtime_paths.printables_dir,
    workspace_folder_func=runtime_paths.workspace_folder,
):
    @router.post("/api/temp-upload")
    async def api_temp_upload(
        content: str = Form(default=""),
        file: UploadFile = File(default=None),
    ):
        """Save pasted JSON text or an uploaded file to a temp location; return the path."""
        fname = f"temp_{_uuid.uuid4().hex}.json"
        path = os.path.join(TEMP_DIR, fname)
        os.makedirs(TEMP_DIR, exist_ok=True)
        if file and file.filename:
            data = await file.read()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                return JSONResponse({"ok": False, "error": (
                    "this file is not text (it looks like a Word document, PDF, or "
                    "other binary file): ask your AI chat for a .md, .json, or .txt "
                    "file, or paste the JSON directly"
                )})
            with open(path, "wb") as fh:
                fh.write(data)
        elif content.strip():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        else:
            return JSONResponse({"ok": False, "error": "No content or file provided"})
        return JSONResponse({"ok": True, "path": path})

    @router.post("/api/push/preview")
    def api_push_preview(course_id: str = Form(...), path: str = Form(...), settings: str = Form("")):
        """Dry-run preview: run qf_pusher.py with --dry-run for one course."""
        try:
            env = config.resolve_env(course_id)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)})
        if settings:
            env["QF_PUSH_SETTINGS"] = settings
        code, output = runner.run_capture(["qf_pusher.py", path, "--dry-run"], env)
        return JSONResponse({"ok": code == 0, "output": output})

    @router.post("/api/validate")
    def api_validate(path: str = Form(...)):
        from api import validate_qf
        seen = set()
        try:
            problems = validate_qf.validate(path, seen)
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": f"file not found: {path}"})
        _, data, _ = validate_qf._load(path)
        advisories = validate_qf.advise(data) if data is not None else []
        return JSONResponse({"ok": not problems, "problems": problems, "advisories": advisories})

    @router.post("/api/physical/quiz")
    def api_physical_quiz(path: str = Form(...)):
        """Compile printable PDF + DOCX files from a <QUIZFORGE_JSON> file."""
        try:
            from engine.importers import import_quiz_from_llm
            from engine.validation.point_calculator import calculate_points
            from engine.validation.answer_balancer import balance_answers
            from engine.rendering.physical.styles.default_styles import DEFAULT_QUIZ_POINTS
            from engine.packagers.physical_handler import generate_physical_outputs
            from engine.packaging.folder_creator import create_quiz_folder
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"engine unavailable: {e}"})

        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"cannot read file: {e}"})

        try:
            from pathlib import Path as _Path
            quiz = import_quiz_from_llm(text).quiz
            try:
                quiz.questions = calculate_points(quiz.questions, total_points=DEFAULT_QUIZ_POINTS)
                quiz.questions = balance_answers(quiz.questions)
            except Exception as exc:
                operational_log.emit("quiz.points_recalculate", "failed", error_class=type(exc))
            base = printables_dir_func()
            os.makedirs(base, exist_ok=True)
            folder = create_quiz_folder(_Path(base), quiz.title)
            results = generate_physical_outputs(quiz, str(folder))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

        log_path = results.get("log_path")
        warnings = []
        if log_path:
            try:
                with open(log_path, encoding="utf-8") as fh:
                    warnings = [
                        line.strip()
                        for line in fh
                        if line.startswith("PHYSICAL RENDER WARNING")
                    ]
                os.remove(log_path)
            except OSError as exc:
                operational_log.emit("quiz.render_warnings_read", "failed", error_class=type(exc))

        files = [
            os.path.basename(results[k])
            for k in ("quiz_path", "quiz_pdf_path", "key_path", "key_pdf_path", "rationale_path")
            if results.get(k)
        ]
        fallback = not bool(workspace_folder_func("Printables"))
        return JSONResponse({"ok": True, "folder": str(folder),
                             "files": files, "warnings": warnings,
                             "warning": warnings[0] if warnings else "",
                             "fallback": fallback})

    @router.post("/api/af/validate")
    def api_af_validate(path: str = Form(...)):
        """Validate an <ASSIGNMENTFORGE_JSON> file and summarize what it would push."""
        data, problems = af.parse_file(path)
        summary = None
        if isinstance(data, dict):
            tiers = data.get("tiers") if isinstance(data.get("tiers"), list) else []
            directions = data.get("directions") if isinstance(data.get("directions"), list) else []
            sections = data.get("sections") if isinstance(data.get("sections"), list) else []
            submission = data.get("submission") if isinstance(data.get("submission"), dict) else {}
            summary = {
                "version": data.get("version"),
                "type": data.get("type"),
                "title": data.get("title"),
                "points": data.get("points"),
                "submission_types": submission.get("types", ["online_text_entry"]),
                "directions": len(directions),
                "sections": len(sections),
                "rubric": bool(data.get("rubric")),
                "supports": bool(data.get("supports")),
                "tiers": [{"label": tier.get("label"),
                           "overrides": sorted(set(tier) & {"overview", "directions"}),
                           "supports": bool(tier.get("supports"))}
                          for tier in tiers if isinstance(tier, dict)],
            }
        return JSONResponse({"ok": data is not None and not problems,
                             "problems": problems, "summary": summary})
    @router.post("/api/pf/validate")
    def api_pf_validate(path: str = Form(...)):
        """Validate a <PAGEFORGE_JSON> file and summarize what it would push."""
        data, problems = pf.parse_file(path)
        summary = None
        if isinstance(data, dict):
            sections = data.get("sections") if isinstance(data.get("sections"), list) else []
            extras = data.get("extras") if isinstance(data.get("extras"), list) else []
            summary = {
                "version": data.get("version"),
                "type": data.get("type"),
                "title": data.get("title"),
                "layout": data.get("layout", "standard"),
                "sections": len(sections),
                "extras": len(extras),
            }
        return JSONResponse({"ok": data is not None and not problems,
                             "problems": problems, "summary": summary})
