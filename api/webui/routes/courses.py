"""Settings course listing and live group helpers used by roster services."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.platform_services.canvas_client import canvas_get

router = APIRouter(tags=["courses"])




@router.get("/api/courses")
def list_all_courses():
    """All courses the saved token can see (used by both settings and dashboard)."""
    data, err = canvas_get("/api/v1/courses", {"per_page": 100, "state[]": "available"})
    if err:
        return JSONResponse({"ok": False, "error": err})
    courses = [{"id": str(c["id"]), "name": c.get("name", f"course {c['id']}")}
               for c in data if "id" in c]
    courses.sort(key=lambda c: c["name"].lower())
    return JSONResponse({"ok": True, "courses": courses})
