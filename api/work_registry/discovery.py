"""Bounded, aggregate-only cross-course Work Registry discovery."""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

from api.platform_services import config
from api.platform_services.canvas_client import canvas_get_all

from . import storage
from .models import validate_job, validate_registry_document
from .providers import (CalendarNeedsAttention, CourseTimeout, DiscoveryDeadline,
                         CourseUnavailable, ProviderFailure, WorkCourseReads)
from .providers import grading_debt, home_attention, late_work, roster_warnings


MAX_COURSE_WORKERS = 3
REQUEST_TIMEOUT_SECONDS = 10
SCAN_TIMEOUT_SECONDS = 30
CACHE_TTL_SECONDS = 300


def _now(value=None) -> str:
    if isinstance(value, datetime):
        current = value
    else:
        current = datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds")


def _error_code(exc: Exception) -> str:
    if isinstance(exc, (CourseTimeout, DiscoveryDeadline, TimeoutError)):
        return "course_timeout"
    if isinstance(exc, CourseUnavailable):
        return "course_unavailable"
    if isinstance(exc, CalendarNeedsAttention):
        return "calendar_needs_attention"
    if isinstance(exc, ProviderFailure):
        return "provider_failed"
    return "provider_failed"


def _course_id(course: dict) -> str:
    value = course.get("id") if isinstance(course, dict) else ""
    return str(value) if value not in (None, "") else ""


def _scan_course(course: dict, *, now: str, deadline: float, canvas_get_all=None) -> dict:
    course_id = _course_id(course)
    if not course_id:
        raise ProviderFailure()
    source_get_all = canvas_get_all if canvas_get_all is None else canvas_get_all
    reads = WorkCourseReads(course_id, deadline=deadline, live_reader=source_get_all)
    findings = []
    errors = []
    providers = (
        grading_debt.scan_course,
        home_attention.scan_comment_follow_up,
        late_work.scan_course,
        roster_warnings.scan_course,
    )
    for provider in providers:
        try:
            findings.extend(provider(course_id, now=now, reads=reads))
        except Exception as exc:
            errors.append(_error_code(exc))
    for finding in findings:
        validate_job(finding)
    return {
        "course_id": course_id,
        "checked_at": now,
        "findings": findings,
        "stale": bool(errors),
        "error_code": errors[0] if errors else "",
    }


def _last_good(cache: dict, course_id: str, *, now: str, error_code: str) -> dict:
    previous = (cache.get("courses") or {}).get(course_id) or {}
    findings = previous.get("findings") if isinstance(previous.get("findings"), list) else []
    return {
        "checked_at": now,
        "findings": findings,
        "stale": True,
        "error_code": error_code,
    }


def scan_active_courses(now=None) -> dict:
    """Scan bookmarked courses within fixed concurrency and time bounds."""
    started = time.monotonic()
    timestamp = _now(now)
    cache = storage.read_discovery_cache()
    courses = [course for course in config.active_courses() if _course_id(course)]
    deadline = started + SCAN_TIMEOUT_SECONDS
    records = {}
    errors = []

    executor = ThreadPoolExecutor(max_workers=MAX_COURSE_WORKERS)
    futures = {
        executor.submit(_scan_course, course, now=timestamp, deadline=deadline): _course_id(course)
        for course in courses
    }
    try:
        pending = set(futures)
        while pending:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            completed, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not completed:
                break
            for future in completed:
                course_id = futures[future]
                try:
                    record = future.result()
                except Exception as exc:
                    code = _error_code(exc)
                    record = _last_good(cache, course_id, now=timestamp, error_code=code)
                    errors.append(code)
                if record.get("stale"):
                    code = record.get("error_code") or "provider_failed"
                    errors.append(code)
                    record = _last_good(cache, course_id, now=timestamp, error_code=code)
                records[course_id] = {
                    key: record.get(key)
                    for key in ("checked_at", "findings", "stale", "error_code")
                }
        for future in pending:
            course_id = futures[future]
            future.cancel()
            records[course_id] = _last_good(cache, course_id, now=timestamp, error_code="course_timeout")
            errors.append("course_timeout")
    except TimeoutError:
        for future in pending:
            course_id = futures[future]
            future.cancel()
            records[course_id] = _last_good(cache, course_id, now=timestamp, error_code="course_timeout")
            errors.append("course_timeout")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    document = {
        "version": 1,
        "updated_at": timestamp,
        "courses": records,
    }
    write_result = storage.write_discovery_cache(document)
    if not write_result.get("ok"):
        return {
            "ok": False,
            "partial": True,
            "courses_scanned": 0,
            "findings": 0,
            "stale_course_ids": sorted(records),
            "error_codes": [write_result.get("error", "workspace_not_configured")],
            "courses": records,
        }
    findings = [finding for record in records.values() for finding in record.get("findings", [])]
    return {
        "ok": True,
        "partial": bool(errors),
        "courses_scanned": len(courses),
        "findings": len(findings),
        "stale_course_ids": sorted(course_id for course_id, record in records.items() if record.get("stale")),
        "error_codes": sorted(set(errors)),
        "courses": records,
    }


def merge_into_registry(result: dict) -> dict:
    """Replace canvas_finding jobs for the scanned courses with fresh findings.

    Shared by the manual scan route and the background heartbeat so both refresh
    detected work the same way. Returns the storage write result.
    """
    current = storage.read_registry()
    scanned_courses = set((result.get("courses") or {}).keys())
    current_jobs = []
    for job in current.get("jobs", []):
        source_ref = job.get("source_ref") if isinstance(job, dict) else {}
        course_ids = set(job.get("course_ids") or []) if isinstance(job, dict) else set()
        if source_ref.get("type") == "canvas_finding" and course_ids & scanned_courses:
            continue
        current_jobs.append(job)
    for record in (result.get("courses") or {}).values():
        current_jobs.extend(record.get("findings") or [])
    current["jobs"] = current_jobs
    current["updated_at"] = _now()
    validate_registry_document(current)
    return storage.write_registry(current)


def cache_is_fresh(now=None) -> bool:
    document = storage.read_discovery_cache()
    updated = document.get("updated_at")
    try:
        stamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return False
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - stamp).total_seconds() < CACHE_TTL_SECONDS


__all__ = [
    "CACHE_TTL_SECONDS", "MAX_COURSE_WORKERS", "REQUEST_TIMEOUT_SECONDS",
    "SCAN_TIMEOUT_SECONDS", "cache_is_fresh", "merge_into_registry", "scan_active_courses",
]
