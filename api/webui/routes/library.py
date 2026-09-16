"""Library and AI-TA file routes for Canvas Expert.

One APIRouter; 7 routes for file listing, AI-TA management, validation, and downloads.

Routes: GET  /api/files
        GET  /api/inbox-files
        GET  /api/ai-ta/files
        GET  /api/ai-ta/file
        GET  /api/ai-ta/toolkit-file
        POST /api/ai-ta/rebuild
        GET  /api/download-contract
"""
import os
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse

from .. import af, ai_ta, pf
from api import runtime_paths
from ..deps import REPO_ROOT, list_ai_ta_files, list_inbox_files, list_quiz_files

router = APIRouter(tags=["library"])

# Kinds Slice C's per-kind Inbox and this route both understand -- keeps the
# unknown-kind rejection explicit rather than leaking a ValueError from
# runtime_paths.inbox_folder.
_INBOX_KINDS = ("quiz", "assignment", "page")


@router.get("/api/files")
def api_files():
    return JSONResponse({"files": list_quiz_files()})


def _validate_inbox_entry(kind: str, path: str):
    """Run the same validator the matching /api/*/validate route uses.

    Returns (ok, problems), mirroring push_validation.py's ok computation for
    each kind exactly: quiz drops the QuizForge full-summary shape, the other
    three only need the ok/problems half. Never raises -- an unreadable file
    (e.g. a half-synced draft that slipped past the marker gate) becomes a
    problem string instead of a 500.
    """
    try:
        if kind == "quiz":
            from api import validate_qf
            problems = validate_qf.validate(path, set())
            return not problems, problems
        if kind == "assignment":
            data, problems = af.parse_file(path)
            return data is not None and not problems, problems
        if kind == "page":
            data, problems = pf.parse_file(path)
            return data is not None and not problems, problems
    except FileNotFoundError:
        return False, [f"file not found: {path}"]
    raise ValueError(f"unknown kind: {kind}")


@router.get("/api/inbox-files")
def api_inbox_files(kind: str):
    """Assistant-staged Inbox drafts for one content kind, pre-validated.

    Read-only: lists Slice C's marker-gated Inbox (``deps.list_inbox_files``)
    and runs each draft through the same validator its push tab already uses,
    so a malformed draft is visible to the teacher instead of failing silently
    once they try to push it. No mutation, no Canvas call.
    """
    if kind not in _INBOX_KINDS:
        return JSONResponse(
            {"ok": False, "error": f"unknown kind: {kind!r} (expected one of {_INBOX_KINDS})"},
            status_code=400,
        )
    files = []
    for entry in list_inbox_files(kind):
        ok, problems = _validate_inbox_entry(kind, entry["path"])
        files.append({
            "label": entry["label"],
            "path": entry["path"],
            "ok": ok,
            "problems": problems,
        })
    return JSONResponse({"ok": True, "files": files})


@router.get("/api/ai-ta/files")
def api_ai_ta_files():
    return JSONResponse({"files": list_ai_ta_files()})


def _content_disposition(filename: str) -> str:
    """Build an attachment Content-Disposition value carrying the real filename.

    Emits both forms: a quoted ``filename`` fallback and the RFC 5987
    ``filename*=UTF-8''...`` percent-encoded form, which browsers prefer when
    present and which is the only one that can carry the name exactly. The
    fallback is sanitized to plain ASCII rather than passed through raw --
    Starlette encodes header values as Latin-1, so a non-Latin-1 character
    there would crash the response instead of just losing fidelity.
    """
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    ascii_name = ascii_name.replace("\\", "\\\\").replace('"', '\\"')
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


@router.get("/api/ai-ta/file")
def api_ai_ta_file(name: str):
    """Return the content of one AI-TA flat file by basename.
    Validates the name is within the current AI-TA directory to prevent path traversal."""
    if not name or os.sep in name or "/" in name or ".." in name:
        return JSONResponse({"error": "invalid name"}, status_code=400)
    canonical = os.path.join(REPO_ROOT, "api", "default_docs", "AI Authoring", name)
    if not os.path.isfile(canonical):
        return JSONResponse({"error": "file not found"}, status_code=404)
    ai_ta_dir = runtime_paths.ai_ta_dir()
    path = os.path.join(ai_ta_dir, name)
    if not os.path.isfile(path) or not os.path.abspath(path).startswith(
            os.path.abspath(ai_ta_dir)):
        return JSONResponse({"error": "file not found"}, status_code=404)
    with open(path, encoding="utf-8") as f:
        return PlainTextResponse(
            f.read(), headers={"Content-Disposition": _content_disposition(name)})


@router.get("/api/ai-ta/toolkit-file")
def api_ai_ta_toolkit_file(name: str):
    """Return content of one MagicSchool Toolkit file by basename."""
    if not name or os.sep in name or "/" in name or ".." in name:
        return JSONResponse({"error": "invalid name"}, status_code=400)
    canonical = os.path.join(REPO_ROOT, "api", "default_docs", "AI Authoring", "MagicSchool Toolkit", name)
    if not os.path.isfile(canonical):
        return JSONResponse({"error": "file not found"}, status_code=404)
    toolkit_dir = os.path.join(runtime_paths.ai_ta_dir(), "MagicSchool Toolkit")
    path = os.path.join(toolkit_dir, name)
    if not os.path.isfile(path) or not os.path.abspath(path).startswith(
            os.path.abspath(toolkit_dir)):
        return JSONResponse({"error": "file not found"}, status_code=404)
    with open(path, encoding="utf-8") as f:
        return PlainTextResponse(
            f.read(), headers={"Content-Disposition": _content_disposition(name)})


@router.post("/api/ai-ta/rebuild")
def api_ai_ta_rebuild():
    try:
        files = ai_ta.build_library(runtime_paths.ai_ta_dir())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse({"ok": True, "files": [os.path.basename(p) for p in files]})


_CONTRACT_FILE_MAP = {
    "AssignmentForge_Base": "Author an Assignment (AssignmentForge).txt",
    "PageForge_Base": "Author a Page (PageForge).txt",
    "QuizForge_Base": "Author a Quiz (QuizForge).txt",
    # Not Forge contracts, but the same "hand this text to an AI" delivery and
    # the same canonical source, so they reuse this route rather than adding
    # one. Both are also served by the MCP get_product_guide tool, so a pasted
    # assistant and a connected one read identical bytes.
    "CanvasAgent": "START HERE - CanvasAgent.txt",
    "WritingTimeline": "Writing Timeline (tracked assignments).txt",
}


@router.get("/api/download-contract")
def api_download_contract(name: str):
    """Download a Forge contract file (e.g. AssignmentForge_Base, PageForge_Base).

    ``name`` is a stable identifier kept for URL compatibility; it maps to the
    one canonical file under ``api/default_docs/AI Authoring/`` that get_authoring_contract
    (the MCP tool) also reads, so both readers return the same bytes."""
    filename = _CONTRACT_FILE_MAP.get(name)
    if filename is None:
        return JSONResponse({"error": "unknown contract"}, status_code=400)
    path = os.path.join(REPO_ROOT, "api", "default_docs", "AI Authoring", filename)
    if not os.path.isfile(path):
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(path, media_type="text/plain", filename=filename)
