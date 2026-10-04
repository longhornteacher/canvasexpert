"""Read-only readiness endpoints for Workbench surfaces."""

from fastapi import APIRouter

from api import readiness


router = APIRouter(tags=["readiness"])




@router.post("/api/readiness/probe")
def post_readiness_probe(force: bool = False):
    return readiness.probe(force=force)
