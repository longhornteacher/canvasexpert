"""Private identity table, protected names, scrub test, and vault exports."""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Query
from fastapi.responses import JSONResponse

from api import feedback_scrub
from api.identity_vault_service import open_vault
from api import roster_service
from api.mirror import store as mirror_store
from api.platform_services import config, workspace
from api.shared_storage import (
    LegacyStorageReappearedError, SharedStoreConflictError,
    assert_store_writable, compare_and_remove, quarantine_conflict,
    reappeared_legacy_storage, scan_conflicts,
)
from api.storage_support import atomic_write_json

names_router = APIRouter(prefix="/api/names", tags=["names"])


def _vault():
    return open_vault()


@names_router.get("")
def course_names(course_id: str = Query("")):
    """Read one course's private who-is-who table without roster edit fields."""
    if not course_id:
        return JSONResponse({"ok": False, "error": "course_id required."})
    document = mirror_store.read_roster(course_id)
    if document is not None and document.get("state") == "current":
        users = list(document["students"].values())
        section_map = document["sections"]
    else:
        users, error = roster_service.fetch_students(course_id)
        if error:
            return JSONResponse({"ok": False, "error": f"Canvas fetch failed: {error}"})
        section_map = roster_service.fetch_sections(course_id)
    vault = _vault()
    with vault.transaction():
        roster_service.upsert_roster(vault, users)
        entries = {entry["canvas_id"]: entry for entry in vault.entries()}
    section_ids = roster_service.enrollment_section_ids(users)
    rows = []
    for user in users or []:
        uid = str(user["id"])
        entry = entries.get(uid, {})
        rows.append({
            "pseudonym": entry.get("pseudonym", ""),
            "real_name": entry.get("real_name") or user.get("name") or user.get("sortable_name", ""),
            "sections": [{"id": sid, "name": section_map.get(sid, f"Section {sid}")}
                         for sid in section_ids.get(uid, [])],
        })
    rows.sort(key=lambda row: row["real_name"].casefold())
    return JSONResponse({"ok": True, "students": rows})


@names_router.get("/protected")
def get_protected():
    """Return protected packs + custom names."""
    return JSONResponse({
        "packs": config.list_protected_packs(),
        "custom": config.get_custom_protected_names(),
        "active": sorted(config.active_protected_names()),
    })


@names_router.post("/protected")
def set_protected(data: str = Form("")):
    """Set pack enabled states and custom names. Expects JSON:
    {"packs": {"outsiders": true, ...}, "custom": ["Name1", "Name2"]}"""
    try:
        parsed = json.loads(data) if data.strip() else {}
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "error": "Invalid JSON."})
    packs = parsed.get("packs", {})
    for pack_id, enabled in packs.items():
        config.set_pack_enabled(pack_id, bool(enabled))
    custom = parsed.get("custom", [])
    config.set_custom_protected_names(custom)
    return JSONResponse({"ok": True})


@names_router.post("/scrub-test")
def scrub_test(text: str = Form(""), course_id: str = Form("")):
    """Live scrub preview: scrub the input using current vault + protected names."""
    vault = _vault()
    protected = config.active_protected_names()
    rmap = feedback_scrub.build_replacement_map(vault.entries(), protected)
    result = feedback_scrub.scrub_text(text, rmap)
    return JSONResponse({"ok": True, "original": text, "scrubbed": result})


@names_router.post("/who-is-who")
def export_who_is_who(course_id: str = Form("")):
    """Write a who-is-who.csv to Student Work/Grading Keys/ and return the path."""
    if not course_id:
        return JSONResponse({"ok": False, "error": "course_id required."})
    vault = _vault()
    private_dir = workspace.grading_keys_root()
    if not private_dir:
        return JSONResponse({"ok": False, "error": "No workspace configured."})
    os.makedirs(private_dir, exist_ok=True)
    stem = f"course_{course_id}"
    who_path = os.path.join(private_dir, f"{stem}__who-is-who.csv")
    with open(who_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Real Name", "Canvas ID", "SIS ID", "Pseudonym", "Nicknames"])
        for e in vault.entries():
            w.writerow([
                e.get("real_name", ""),
                e.get("canvas_id", ""),
                e.get("sis_id", ""),
                e.get("pseudonym", ""),
                ", ".join(e.get("nicknames", [])),
            ])
    return JSONResponse({"ok": True, "path": who_path})


@names_router.get("/vault-conflict")
def vault_conflict():
    """List conflict siblings throughout the shared workspace without reading
    their contents. The response contains only names and filesystem metadata."""
    root = workspace.shared_root()
    if not root:
        return JSONResponse({"ok": False, "error": "workspace_not_configured"})
    base = Path(root)
    files = []
    for conflict in scan_conflicts():
        path = conflict["path"]
        try:
            stat = os.stat(path)
        except OSError:
            continue
        files.append({
            "name": os.path.basename(path),
            "path": path,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "size_bytes": stat.st_size,
        })
    try:
        reappeared = reappeared_legacy_storage()
    except LegacyStorageReappearedError:
        reappeared = ["legacy storage"]
    return JSONResponse({"ok": True, "folder": root, "files": files,
                         "safety_blocked": bool(reappeared),
                         "safety_reasons": reappeared})


@names_router.post("/vault-conflict/compare")
def compare_vault_conflict(path: str = Form("")):
    """Compare a listed conflict copy; remove it only when hashes match."""
    result = compare_and_remove(Path(path), root=workspace.workspace_root())
    return JSONResponse(result)


@names_router.post("/vault-conflict/quarantine")
def quarantine_vault_conflict(path: str = Form("")):
    """Move a listed conflict copy into the teacher-visible shared quarantine."""
    result = quarantine_conflict(Path(path), root=workspace.workspace_root())
    return JSONResponse(result)


@names_router.post("/backup-vault")
def backup_vault():
    """Write a new immutable snapshot of the merged Identity Vault."""
    vault = _vault()
    backup_dir = vault.directory / "backups"
    try:
        assert_store_writable(vault.directory, root=workspace.workspace_root())
    except SharedStoreConflictError:
        return JSONResponse({"ok": False, "error": "shared_workspace_conflict"})
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / f"vault-{stamp}.json"
    atomic_write_json(backup_path, {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": vault._by_id,
    })
    return JSONResponse({"ok": True, "path": str(backup_path), "entries": len(vault)})
