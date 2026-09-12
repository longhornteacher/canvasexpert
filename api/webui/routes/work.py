"""Local Work Registry projections and guarded state transitions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.work_registry import adapters, discovery, storage, suppressions
from api.work_registry.models import public_job, validate_registry_document
from api.platform_services import config
from ..local_request_guard import require_local_mutation


router = APIRouter(prefix="/api", tags=["work"])

# Detected Canvas findings that point at a single assignment. Their cards are
# relabeled with the real assignment name (resolved from the local mirror), and
# hidden entirely when no name can be resolved — a card with no specifics is
# just "go look at Canvas", which we deliberately do not surface.
_NAMED_FINDING_KINDS = {"grade.debt", "grade.followup", "grade.staff_check", "late.work"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raw_jobs() -> list[dict]:
    registry_jobs = storage.read_registry().get("jobs", [])
    projected = {job["job_id"]: job for job in registry_jobs if isinstance(job, dict)}
    for job in adapters.collect_local_jobs():
        existing = projected.get(job["job_id"])
        if (
            existing
            and existing.get("origin") == "intentional"
            and existing.get("status") == "completed"
            and existing.get("material_version") == job.get("material_version")
        ):
            continue
        projected[job["job_id"]] = job
    current_ids = {
        str(course.get("id") or "").strip()
        for course in config.active_courses()
        if str(course.get("id") or "").strip()
    }
    visible = []
    for job in projected.values():
        # Do not surface previously saved retired items; this is an inert filter,
        # not a provider or compatibility route.
        if _text(job.get("kind")) in {
            "grade.powergrader", "grade.powergrader.scheduled", "grade.powergrader_ready",
        }:
            continue
        course_ids = {
            str(course_id).strip()
            for course_id in (job.get("course_ids") or [])
            if str(course_id).strip()
        }
        if course_ids and not course_ids.intersection(current_ids):
            continue
        visible.append(job)
    return visible


def _all_jobs() -> list[dict]:
    return [public_job(job) for job in _raw_jobs()]


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _current_course_labels() -> dict[str, str]:
    try:
        courses = config.active_courses()
    except Exception:
        return {}
    labels = {}
    for course in courses if isinstance(courses, list) else []:
        if not isinstance(course, dict):
            continue
        course_id = _text(course.get("id"))
        label = _text(course.get("nickname")) or _text(course.get("name"))
        if course_id and label:
            labels[course_id] = label
    return labels


def _finding_assignment_names(jobs: list[dict]) -> dict[tuple[str, str], str]:
    """Resolve assignment titles for detected grading findings from the mirror.

    Read-only label lookup: it never starts a Scoring Session or scans Canvas.
    A finding whose name cannot be resolved keeps its generic title rather than
    disappearing, so real work is never hidden by a cold mirror.
    """
    wanted: dict[str, set[str]] = {}
    for job in jobs:
        if not isinstance(job, dict) or _text(job.get("kind")) not in _NAMED_FINDING_KINDS:
            continue
        course_id = _text(job.get("focused_course_id"))
        assignment_id = _text(job.get("assignment_id"))
        if course_id and assignment_id:
            wanted.setdefault(course_id, set()).add(assignment_id)
    if not wanted:
        return {}
    try:
        from api.mirror import queries as mirror_queries
    except Exception:
        return {}
    names: dict[tuple[str, str], str] = {}
    for course_id, assignment_ids in wanted.items():
        try:
            rows, error = mirror_queries.course_assignments(course_id)
        except Exception:
            continue
        if error or not isinstance(rows, list):
            continue
        by_id = {
            _text(row.get("id")): _text(row.get("name"))
            for row in rows
            if isinstance(row, dict)
        }
        for assignment_id in assignment_ids:
            name = by_id.get(assignment_id)
            if name:
                names[(course_id, assignment_id)] = name
    return names


def _course_label(job: dict, labels: dict[str, str]) -> str:
    focused = _text(job.get("focused_course_id"))
    if focused in labels:
        return labels[focused]
    for course_id in job.get("course_ids") or []:
        normalized = _text(course_id)
        if normalized in labels:
            return labels[normalized]
    return ""


def _aggregate_summary(job: dict) -> tuple[str, str, str]:
    counts = job.get("counts") if isinstance(job.get("counts"), dict) else {}
    pending = _count(counts.get("pending"))
    affected = _count(counts.get("affected"))
    total = _count(counts.get("total"))
    kind = _text(job.get("kind"))
    if kind == "grade.debt":
        count = pending
        return (
            "Grading needed",
            f"{count} {_plural(count, 'submission')} awaiting grading",
            "Open Gradebook",
        )
    if kind == "grade.followup":
        count = affected
        return (
            "Student follow-up",
            f"{count} {_plural(count, 'response')} {'needs' if count == 1 else 'need'} a human check",
            "Open Gradebook",
        )
    if kind == "grade.staff_check":
        count = affected
        return (
            "Staff response check",
            f"{count} {_plural(count, 'response')} needs a staff response check",
            "Open Gradebook",
        )
    if kind == "late.work":
        count = affected
        return "Late work", f"{count} late {_plural(count, 'submission')}", "Open Gradebook"
    if kind == "roster.warning":
        count = affected
        return (
            "Roster attention",
            f"{count} roster {_plural(count, 'issue')} need review",
            "Open Roster",
        )
    count = total or pending or affected
    title = _text(job.get("title")) or "Work item"
    if job.get("status") == "attention":
        summary = f"{count} {_plural(count, 'record')} need review" if count else "Work needs review"
        return title, summary, "Review"
    summary = f"{count} {_plural(count, 'record')} in progress" if count else "Work in progress"
    return title, summary, "Continue"


def _presentations(jobs: list[dict], finding_names: dict | None = None) -> dict[str, dict[str, str]]:
    labels = _current_course_labels()
    kinds = {_text(job.get("kind")) for job in jobs if isinstance(job, dict)}
    if finding_names is None:
        finding_names = _finding_assignment_names(jobs) if kinds & _NAMED_FINDING_KINDS else {}
    presentations = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = _text(job.get("job_id"))
        if not job_id:
            continue
        kind = _text(job.get("kind"))
        title, summary, action_label = _aggregate_summary(job)
        if kind in _NAMED_FINDING_KINDS:
            resolved = finding_names.get(
                (_text(job.get("focused_course_id")), _text(job.get("assignment_id")))
            )
            if resolved:
                title = resolved
        presentations[job_id] = {
            "course_label": _course_label(job, labels),
            "title": title,
            "summary": summary,
            "action_label": action_label,
        }
    return presentations


def _card_is_visible(job: dict, presentation: dict, finding_names: dict) -> bool:
    """Only surface a card that carries specifics — which assignment, how many.

    Detected findings are hidden when their assignment name cannot be resolved.
    A generic "go look at Canvas" prompt is a teacher's default state, not news.
    """
    kind = _text(job.get("kind"))
    if kind in _NAMED_FINDING_KINDS:
        key = (_text(job.get("focused_course_id")), _text(job.get("assignment_id")))
        return key in finding_names
    return True


def visible_work(section: str) -> tuple[list[dict], dict] | None:
    """Section jobs and their presentations with detail-free cards removed."""
    jobs = _section_jobs(section)
    if jobs is None:
        return None
    finding_names = _finding_assignment_names(jobs)
    presentations = _presentations(jobs, finding_names)
    kept = [
        job for job in jobs
        if _card_is_visible(job, presentations.get(job["job_id"], {}), finding_names)
    ]
    return kept, {job["job_id"]: presentations[job["job_id"]] for job in kept}


def _section_jobs(section: str) -> list[dict] | None:
    if section not in {"continue", "attention", "all"}:
        return None
    jobs = _all_jobs()
    selected = []
    for job in jobs:
        if suppressions.is_suppressed(job):
            continue
        if section == "continue" and job["status"] not in {"draft", "ready", "in_progress"}:
            continue
        if section == "attention" and job["status"] != "attention":
            continue
        selected.append(job)
    return selected


def _json_body_error():
    return JSONResponse({"ok": False, "error": "invalid request body"}, status_code=400)


async def _exact_body(request: Request, keys: set[str]):
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict) or set(body) != keys:
        return None
    return body


def _find_current(job_id: str):
    return next((job for job in _raw_jobs() if job["job_id"] == job_id), None)


def _conflict(message: str = "job is unknown or stale"):
    return JSONResponse({"ok": False, "error": message}, status_code=409)


def _write_result(result: dict):
    if result.get("ok"):
        return None
    return JSONResponse(result, status_code=503)


def _merge_discovery(result: dict) -> dict:
    return discovery.merge_into_registry(result)


@router.get("/work")
def get_work(section: str = "continue"):
    result = visible_work(section)
    if result is None:
        return JSONResponse({"ok": False, "error": "unknown section"}, status_code=400)
    jobs, presentations = result
    return JSONResponse({
        "ok": True,
        "jobs": jobs,
        "presentations": presentations,
        "start_sources": adapters.collect_start_sources(),
    })


@router.post("/work/scan")
def scan_work(request: Request):
    require_local_mutation(request)
    if storage.workspace.workspace_root() is None:
        return JSONResponse({"ok": False, "error": "workspace_not_configured"}, status_code=503)
    result = discovery.scan_active_courses()
    if not result.get("ok"):
        return JSONResponse(result, status_code=503)
    write_result = _merge_discovery(result)
    error = _write_result(write_result)
    if error:
        return error
    return JSONResponse({
        "ok": True,
        "partial": bool(result.get("partial")),
        "courses_scanned": result.get("courses_scanned", 0),
        "findings": result.get("findings", 0),
        "stale_course_ids": result.get("stale_course_ids", []),
        "error_codes": result.get("error_codes", []),
    })


@router.post("/work/{job_id}/ignore")
async def ignore_work(job_id: str, request: Request):
    require_local_mutation(request)
    body = await _exact_body(request, {"material_version"})
    if body is None or not isinstance(body.get("material_version"), str):
        return _json_body_error()
    job = _find_current(job_id)
    if job is None or body["material_version"] != job["material_version"]:
        return _conflict()
    result = suppressions.ignore(job)
    error = _write_result(result)
    if error:
        return error
    projection = deepcopy(job)
    projection["status"] = "ignored"
    projection["attention_reason"] = ""
    return JSONResponse({"ok": True, "job": public_job(projection)})


@router.post("/work/{job_id}/snooze")
async def snooze_work(job_id: str, request: Request):
    require_local_mutation(request)
    body = await _exact_body(request, {"material_version", "until"})
    if body is None or not isinstance(body.get("material_version"), str) or not isinstance(body.get("until"), str):
        return _json_body_error()
    job = _find_current(job_id)
    if job is None or body["material_version"] != job["material_version"]:
        return _conflict()
    try:
        result = suppressions.snooze(job, body["until"])
    except ValueError:
        return _conflict("until must be a future ISO-8601 timestamp")
    error = _write_result(result)
    if error:
        return error
    return JSONResponse({"ok": True, "job": public_job(job)})


@router.post("/work/{job_id}/complete")
async def complete_work(job_id: str, request: Request):
    require_local_mutation(request)
    body = await _exact_body(request, {"material_version"})
    if body is None or not isinstance(body.get("material_version"), str):
        return _json_body_error()
    job = _find_current(job_id)
    if job is None or body["material_version"] != job["material_version"]:
        return _conflict()
    if job["origin"] != "intentional":
        return _conflict("only intentional work can be completed")
    current = storage.read_registry()
    existing_index = next((index for index, item in enumerate(current["jobs"]) if item.get("job_id") == job_id), None)
    if existing_index is None:
        completed_record = deepcopy(job)
        completed_record.update({"status": "completed", "completed_at": _now(), "updated_at": _now()})
        current["jobs"].append(completed_record)
        existing_index = len(current["jobs"]) - 1
    else:
        current["jobs"][existing_index].update({"status": "completed", "completed_at": _now(), "updated_at": _now()})
    current["updated_at"] = _now()
    validate_registry_document(current)
    result = storage.write_registry(current)
    error = _write_result(result)
    if error:
        return error
    completed = deepcopy(current["jobs"][existing_index])
    completed.update({"updated_at": current["updated_at"], "attention_reason": ""})
    return JSONResponse({"ok": True, "job": public_job(completed)})
