"""Canonical instruction download used by the root console."""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from api.runtime_paths import REPO_ROOT

router = APIRouter(tags=["library"])
_CONTRACTS = {
    "CanvasAgent": "START HERE - CanvasAgent.txt",
    "QuizForge": "Author a Quiz (QuizForge).txt",
    "AssignmentForge": "Author an Assignment (AssignmentForge).txt",
    "PageForge": "Author a Page (PageForge).txt",
}


def _content_disposition(filename: str) -> str:
    fallback = filename.encode("ascii", "replace").decode("ascii")
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


@router.get("/api/download-contract")
def download_contract(name: str):
    filename = _CONTRACTS.get(name)
    if filename is None:
        return JSONResponse({"ok": False, "error": "Contract not found."}, status_code=404)
    path = Path(REPO_ROOT) / "api" / "default_docs" / "AI Authoring" / filename
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return JSONResponse({"ok": False, "error": "Contract unavailable."}, status_code=404)
    return PlainTextResponse(text, headers={"Content-Disposition": _content_disposition(filename)})
