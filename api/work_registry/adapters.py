"""Read-only projections from local Canvas Expert authorities.

This module deliberately consumes summaries and metadata only. It never opens a
PowerGrader session payload, scans Canvas, or copies private authority fields.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import material_version, stable_fingerprint, validate_job


_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
_FORGE_FOLDERS = {
    "Quizzes": "quiz",
    "Assignments": "assignment",
    "Pages": "page",
    "Rubrics": "rubric",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _safe_token(value) -> str:
    value = _text(value)
    return value if value and _SAFE_TOKEN.fullmatch(value) else ""


def _job_id(kind: str, source_value: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{source_value}".encode("utf-8")).hexdigest()
    return f"job-{digest}"


def _project(
    *, kind: str, origin: str, source_type: str, source_value: str,
    course_ids: list[str], assignment_id: str, resume_url: str,
    status: str, counts: dict, attention_reason: str = "",
) -> dict:
    timestamp = _now()
    source_ref = {"type": source_type, "value": source_value}
    fingerprint = stable_fingerprint(kind, source_ref, course_ids, assignment_id)
    facts = {"kind": kind, "course_ids": sorted(course_ids), "assignment_id": assignment_id,
             "status": status, "counts": counts}
    job = {
        "job_id": _job_id(kind, source_value),
        "fingerprint": fingerprint,
        "material_version": material_version(facts),
        "origin": origin,
        "kind": kind,
        "status": status,
        "title": "Work item",
        "description": "",
        "course_ids": list(course_ids),
        "focused_course_id": course_ids[0] if len(course_ids) == 1 else "",
        "assignment_id": assignment_id,
        "resumable_url": resume_url,
        "source_ref": source_ref,
        "counts": dict(counts),
        "attention_reason": attention_reason,
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": timestamp if status == "completed" else "",
    }
    validate_job(job)
    return job


def _receipt_jobs() -> list[dict]:
    try:
        from api.operation_ledger import receipts
        summaries = receipts.list_receipts()
    except Exception:
        return []
    output = []
    for receipt in summaries if isinstance(summaries, list) else []:
        if not isinstance(receipt, dict):
            continue
        receipt_id = _safe_token(receipt.get("receipt_id"))
        if not receipt_id:
            continue
        state = _text(receipt.get("status"))
        if state in {"applied", "no_effect"}:
            status = "completed"
        elif state in {"partial", "failed", "blocked"}:
            status = "attention"
        else:
            continue
        output.append(_project(
            kind="operation_receipt", origin="system", source_type="operation_receipt",
            source_value=receipt_id, course_ids=[], assignment_id="",
            resume_url=f"/api/receipts/{receipt_id}", status=status,
            counts={"total": 0, "pending": 0, "affected": 0},
            attention_reason="Operation receipt needs attention" if status == "attention" else "",
        ))
    return output


def _routine_jobs() -> list[dict]:
    try:
        from api.webui.routes import routines
        definitions = routines._ROUTINE_DEFS
    except Exception:
        return []
    output = []
    for routine_id in sorted(definitions):
        try:
            state = routines._routine_state(routine_id)
            due = routines._routine_due(state)
        except Exception:
            continue
        if not state.get("enabled") or not due:
            continue
        safe_routine_id = _safe_token(routine_id)
        if not safe_routine_id:
            continue
        output.append(_project(
            kind="routine_state", origin="system", source_type="routine_state",
            source_value=safe_routine_id, course_ids=[], assignment_id="",
            resume_url="/routines", status="attention",
            counts={"total": 0, "pending": 0, "affected": 0},
            attention_reason="Enabled routine is due",
        ))
    return output


def collect_local_jobs() -> list[dict]:
    """Collect only local summary projections; never perform a Canvas read."""
    jobs = []
    for provider in (_receipt_jobs, _routine_jobs):
        jobs.extend(provider())
    return jobs


def collect_start_sources() -> list[dict]:
    """List workspace Forge files as relative, generic start metadata only."""
    try:
        from api.platform_services import workspace
        root_value = workspace.workspace_root()
    except Exception:
        return []
    if not root_value:
        return []
    root = Path(root_value)
    output = []
    for folder, singular in _FORGE_FOLDERS.items():
        directory = root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            output.append({
                "kind": f"create.{singular}",
                "title": f"{singular.title()} source",
                "path": relative,
            })
    return output
