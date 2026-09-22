"""Feedback tools persona routes."""

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from api.platform_services import config

router = APIRouter()


def _valid_persona_id(value: str) -> bool:
    return bool(value) and all(char.islower() or char.isdigit() or char == "_" for char in value)


@router.get("/personas")
def list_personas():
    return JSONResponse({"personas": config.list_personas()})


@router.post("/personas/custom")
def add_custom_persona(persona_id: str = Form(""), name: str = Form(""),
                       personality: str = Form("")):
    if not _valid_persona_id(persona_id.strip()) or not name.strip() or not personality.strip():
        return JSONResponse({"ok": False, "error": "Use a lowercase ID plus a name and personality."})
    config.save_custom_persona(persona_id.strip(), name.strip(), personality.strip())
    return JSONResponse({"ok": True, "personas": config.list_personas()})


@router.delete("/personas/custom")
def delete_custom_persona(persona_id: str = Form("")):
    config.remove_custom_persona(persona_id.strip())
    return JSONResponse({"ok": True, "personas": config.list_personas()})
