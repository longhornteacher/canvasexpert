"""Strict, PII-minimized models for the Work Registry contract."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
from copy import deepcopy
from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit


class JobStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    ATTENTION = "attention"
    IGNORED = "ignored"
    COMPLETED = "completed"
    FAILED = "failed"


class JobOrigin(str, Enum):
    INTENTIONAL = "intentional"
    DETECTED = "detected"
    SYSTEM = "system"


class SourceType(str, Enum):
    WORKSPACE_RELATIVE = "workspace_relative"
    CANVAS_FINDING = "canvas_finding"
    ROUTINE_STATE = "routine_state"
    OPERATION_RECEIPT = "operation_receipt"


class SuppressionMode(str, Enum):
    IGNORED = "ignored"
    SNOOZED = "snoozed"


class RegistryValidationError(ValueError):
    """Raised when a registry or suppression document is not contract-shaped."""


REGISTRY_VERSION = 1
_STATUSES = {item.value for item in JobStatus}
_ORIGINS = {item.value for item in JobOrigin}
_SOURCE_TYPES = {item.value for item in SourceType}
_SUPPRESSION_MODES = {item.value for item in SuppressionMode}
_JOB_KEYS = {
    "job_id", "fingerprint", "material_version", "origin", "kind", "status",
    "title", "description", "course_ids", "focused_course_id", "assignment_id",
    "resumable_url", "source_ref", "counts", "attention_reason", "created_at",
    "updated_at", "completed_at",
}
_SOURCE_KEYS = {"type", "value"}
_COUNTS_KEYS = {"total", "pending", "affected"}
_SUPPRESSION_KEYS = {"fingerprint", "material_version", "mode", "until", "updated_at"}


def _fail(message: str):
    raise RegistryValidationError(message)


def _require_exact_keys(value: dict, expected: set[str], label: str) -> None:
    if set(value) != expected:
        _fail(f"{label} keys do not match the supported schema")


def _require_text(value, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail(f"{label} must be a string")
    return value


def _is_iso(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


def _is_absolute_reference(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(
        parsed.netloc
        or (parsed.scheme and (value.casefold().startswith(("http:", "https:", "file:", "ftp:"))))
        or value.startswith("//")
        or value.startswith("\\\\")
        or posixpath.isabs(value)
        or value.startswith("\\")
        or (len(value) >= 2 and value[1] == ":" and (len(value) == 2 or value[2] in "\\/"))
        or os.path.isabs(value)
    )


def _validate_relative_url(value: str) -> None:
    _require_text(value, "resumable_url", allow_empty=False)
    if not value.startswith("/") or value.startswith("//") or _is_absolute_reference(value[1:]):
        _fail("resumable_url must be a local relative URL")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        _fail("resumable_url must not be an absolute URL")


def _validate_safe_reference_text(value: str) -> None:
    if _is_absolute_reference(value):
        _fail("absolute references are not allowed")
    lowered = value.casefold()
    if any(token in lowered for token in ("student", "user_id", "student_id", "submission", "comment")):
        _fail("private identity or payload references are not allowed")


def validate_source_ref(source_ref: dict) -> dict:
    if not isinstance(source_ref, dict):
        _fail("source_ref must be an object")
    _require_exact_keys(source_ref, _SOURCE_KEYS, "source_ref")
    source_type = _require_text(source_ref.get("type"), "source_ref.type")
    if source_type not in _SOURCE_TYPES:
        _fail("unsupported source_ref type")
    value = _require_text(source_ref.get("value"), "source_ref.value")
    _validate_safe_reference_text(value)
    return source_ref


def _validate_counts(counts: dict) -> None:
    if not isinstance(counts, dict):
        _fail("counts must be an object")
    _require_exact_keys(counts, _COUNTS_KEYS, "counts")
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"counts.{name} must be a non-negative integer")


def validate_job(job: dict) -> dict:
    if not isinstance(job, dict):
        _fail("job must be an object")
    _require_exact_keys(job, _JOB_KEYS, "job")

    for name in ("job_id", "fingerprint", "material_version", "kind", "title"):
        _require_text(job.get(name), f"job.{name}")
    origin = _require_text(job.get("origin"), "job.origin")
    status = _require_text(job.get("status"), "job.status")
    if origin not in _ORIGINS:
        _fail("unsupported job origin")
    if status not in _STATUSES:
        _fail("unsupported job status")

    course_ids = job.get("course_ids")
    if not isinstance(course_ids, list) or any(not isinstance(item, str) for item in course_ids):
        _fail("course_ids must be a list of strings")
    if len(set(course_ids)) != len(course_ids):
        _fail("course_ids must not contain duplicates")
    focused = _require_text(job.get("focused_course_id"), "focused_course_id", allow_empty=True)
    if focused and focused not in course_ids:
        _fail("focused_course_id must be a member of course_ids")
    _require_text(job.get("assignment_id"), "assignment_id", allow_empty=True)
    _validate_relative_url(job.get("resumable_url"))
    validate_source_ref(job.get("source_ref"))
    _validate_counts(job.get("counts"))
    _require_text(job.get("attention_reason"), "attention_reason", allow_empty=True)
    _require_text(job.get("description"), "description", allow_empty=True)
    for name in ("created_at", "updated_at"):
        if not _is_iso(job.get(name)):
            _fail(f"job.{name} must be ISO-8601")
    completed_at = job.get("completed_at")
    if completed_at and not _is_iso(completed_at):
        _fail("job.completed_at must be empty or ISO-8601")
    return job


def validate_registry_document(document: dict) -> dict:
    if not isinstance(document, dict):
        _fail("registry document must be an object")
    if set(document) != {"version", "updated_at", "jobs"}:
        _fail("registry document keys do not match the supported schema")
    if document.get("version") != REGISTRY_VERSION:
        _fail("unsupported registry document version")
    if not _is_iso(document.get("updated_at")):
        _fail("registry updated_at must be ISO-8601")
    if not isinstance(document.get("jobs"), list):
        _fail("registry jobs must be a list")
    for job in document["jobs"]:
        validate_job(job)
    return document


def validate_suppressions(document: dict) -> dict:
    if not isinstance(document, dict):
        _fail("suppressions document must be an object")
    if set(document) != {"version", "items"}:
        _fail("suppressions document keys do not match the supported schema")
    if document.get("version") != REGISTRY_VERSION:
        _fail("unsupported suppressions document version")
    if not isinstance(document.get("items"), list):
        _fail("suppression items must be a list")
    for item in document["items"]:
        if not isinstance(item, dict):
            _fail("suppression item must be an object")
        _require_exact_keys(item, _SUPPRESSION_KEYS, "suppression item")
        _require_text(item.get("fingerprint"), "suppression fingerprint")
        _require_text(item.get("material_version"), "suppression material_version")
        mode = _require_text(item.get("mode"), "suppression mode")
        if mode not in _SUPPRESSION_MODES:
            _fail("unsupported suppression mode")
        until = _require_text(item.get("until"), "suppression until", allow_empty=True)
        if until and not _is_iso(until):
            _fail("suppression until must be empty or ISO-8601")
        if not _is_iso(item.get("updated_at")):
            _fail("suppression updated_at must be ISO-8601")
    return document


_FORBIDDEN_FACT_KEY_PARTS = (
    "student", "user", "name", "grade", "comment", "submission", "content", "feedback", "note",
)


def _validate_hash_facts(value, path: str = "facts"):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail(f"{path} keys must be strings")
            lowered = key.casefold()
            if any(part in lowered for part in _FORBIDDEN_FACT_KEY_PARTS):
                _fail("student or private payload facts are not accepted")
            _validate_hash_facts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_hash_facts(child, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        _fail(f"{path} contains an unsupported value")


def _digest(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_fingerprint(kind: str, source_ref: dict, course_ids: list[str], assignment_id: str) -> str:
    _require_text(kind, "kind")
    validate_source_ref(source_ref)
    if not isinstance(course_ids, list) or any(not isinstance(item, str) for item in course_ids):
        _fail("course_ids must be a list of strings")
    _require_text(assignment_id, "assignment_id", allow_empty=True)
    facts = {
        "kind": kind,
        "source_ref": {"type": source_ref["type"], "value": source_ref["value"]},
        "course_ids": sorted(set(course_ids)),
        "assignment_id": assignment_id,
    }
    return _digest(facts)


def material_version(job_facts: dict) -> str:
    if not isinstance(job_facts, dict):
        _fail("job_facts must be an object")
    _validate_hash_facts(job_facts)
    return _digest(deepcopy(job_facts))


def generic_title(kind: str) -> str:
    if kind == "operation_receipt":
        return "Operation receipt"
    if kind == "routine_state":
        return "Routine attention"
    if kind.startswith("create."):
        return "Create work"
    return "Work item"


def generic_description(kind: str) -> str:
    """Teacher-legible phrase for a job's `kind`, for a secondary/detail line.

    A raw kind slug (e.g. "routine_state") is an internal machine code, not
    display text -- work_rail.js used to render `kind` directly for exactly
    that reason (feature-freeze-hardening-initiative.md D3). An unmapped kind
    returns "" so the rail falls back to showing nothing rather than a slug.
    """
    if kind == "grade.debt":
        return "Grading debt"
    if kind == "late.work":
        return "Late work"
    if kind == "roster.warning":
        return "Roster warning"
    if kind == "operation_receipt":
        return "Operation receipt"
    if kind == "routine_state":
        return "Routine attention"
    if kind.startswith("create."):
        return "Draft in progress"
    return ""


def public_job(job: dict) -> dict:
    """Return the exact public shape with generic display text."""
    validate_job(job)
    output = deepcopy(job)
    output["title"] = generic_title(output["kind"])
    output["description"] = generic_description(output["kind"])
    output["attention_reason"] = "Work needs attention" if output["status"] == "attention" else ""
    return output


def public_document(document: dict) -> dict:
    validate_registry_document(document)
    output = deepcopy(document)
    output["jobs"] = [public_job(job) for job in output["jobs"]]
    return output
