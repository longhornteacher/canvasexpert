"""Strict, versioned safe evidence records; no vault or Canvas dependencies."""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime

SCHEMA_VERSION = 1
FACT_FIELDS = frozenset({"schema_version", "kind", "source_key", "course_id", "entity_key", "payload"})
COMMIT_FIELDS = frozenset({"schema_version", "source_key", "course_id", "scope", "scope_id", "writer_key", "run_id", "parents", "acquisition_started_at", "acquisition_finished_at", "mode", "membership_complete", "record_refs", "member_keys", "gaps", "watermarks"})
SUBMISSION_FIELDS = frozenset({"assignment_id", "pseudonym", "attempt", "submitted_at", "body", "score", "grade", "late", "missing", "workflow_state", "updated_at"})
PAYLOAD_FIELDS = {
    "course": frozenset({"title", "workflow_state"}),
    "assignment": frozenset({"assignment_id", "title", "description", "points_possible", "due_at", "updated_at", "rubric"}),
    "student": frozenset({"pseudonym", "section_ids"}),
    "submission": SUBMISSION_FIELDS,
    "attempt_observation": SUBMISSION_FIELDS,
    "comment": frozenset({"assignment_id", "pseudonym", "attempt", "comment_id", "author_pseudonym", "author_role", "text", "created_at"}),
    "override": frozenset({"assignment_id", "override_id", "student_pseudonyms", "section_id", "group_id", "due_at", "unlock_at", "lock_at"}),
}
FACT_KINDS = frozenset(PAYLOAD_FIELDS)
SCOPE_KINDS = {
    "course.context": frozenset({"course"}),
    "course.roster": frozenset({"student"}),
    "course.assignments": frozenset({"assignment"}),
    "assignment.submissions": frozenset({"submission", "attempt_observation"}),
    "assignment.comments": frozenset({"comment"}),
    "assignment.overrides": frozenset({"override"}),
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_TIMESTAMPS = frozenset({"submitted_at", "updated_at", "created_at", "due_at", "unlock_at", "lock_at"})
_IDS = frozenset({"assignment_id", "comment_id", "override_id", "section_id", "group_id"})


class EvidenceValidationError(ValueError):
    """Sanitized structural refusal; never includes an offending value."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code="invalid_record"):
    raise EvidenceValidationError(code)


def validate_component(value: str) -> str:
    if not isinstance(value, str) or not _COMPONENT.fullmatch(value) or value in {".", ".."}:
        _fail("invalid_component")
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        _fail("invalid_component")
    return value


def validate_digest(value: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _fail("invalid_digest")
    return value


def _entity(value):
    if not isinstance(value, str) or not _ENTITY.fullmatch(value):
        _fail("invalid_entity")


def _timestamp(value, *, nullable=True):
    if nullable and value is None:
        return
    if not isinstance(value, str):
        _fail("invalid_timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            _fail("invalid_timestamp")
    except (ValueError, OverflowError):
        _fail("invalid_timestamp")


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        _fail()
    if set(value) - set(allowed) or set(required) - set(value):
        _fail("unexpected_fields")


def _strings(value, validator=_entity):
    if not isinstance(value, list):
        _fail()
    for entry in value:
        validator(entry)
    if len(set(value)) != len(value):
        _fail("duplicate_reference")


def _json_values(value, depth=0):
    if depth > 16:
        _fail("invalid_nesting")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_values(item, depth + 1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            _json_values(item, depth + 1)
        return
    _fail("non_json_value")


def canonical_bytes(record: dict) -> bytes:
    """Canonical finite JSON encoding, independent of insertion order."""
    _json_values(record)
    try:
        return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError):
        _fail("non_json_value")


def digest_record(record: dict) -> str:
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


def validate_fact(record: dict) -> dict:
    _object(record, FACT_FIELDS, FACT_FIELDS)
    canonical_bytes(record)
    if type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported_schema")
    kind = record["kind"]
    if not isinstance(kind, str) or kind not in PAYLOAD_FIELDS:
        _fail("unsupported_kind")
    validate_digest(record["source_key"])
    validate_component(record["course_id"])
    _entity(record["entity_key"])
    payload = record["payload"]
    required = {
        "course": {"title"}, "assignment": {"assignment_id", "title"},
        "student": {"pseudonym"}, "submission": {"assignment_id", "pseudonym", "attempt"},
        "attempt_observation": {"assignment_id", "pseudonym", "attempt", "submitted_at"},
        "comment": {"assignment_id", "pseudonym", "comment_id", "text"},
        "override": {"assignment_id", "override_id"},
    }[kind]
    _object(payload, PAYLOAD_FIELDS[kind], required)
    for key, value in payload.items():
        if key in _TIMESTAMPS:
            _timestamp(value)
        elif key in _IDS:
            if key in required and value is None:
                _fail()
            if value is not None:
                validate_component(value)
        elif key in {"pseudonym", "author_pseudonym"}:
            if key in required and value is None:
                _fail()
            if value is not None:
                _entity(value)
        elif key in {"section_ids", "student_pseudonyms"}:
            _strings(value)
        elif key == "attempt":
            if value is not None and (type(value) is not int or value < 1):
                _fail("invalid_attempt")
        elif key in {"score", "points_possible"}:
            if value is not None and type(value) not in {int, float}:
                _fail()
        elif key in {"late", "missing"}:
            if type(value) is not bool:
                _fail()
        elif key == "rubric":
            if not isinstance(value, list):
                _fail()
            for criterion in value:
                _object(criterion, {"criterion_id", "description", "points", "ratings"}, {"criterion_id", "description", "points"})
                _entity(criterion["criterion_id"])
                if not isinstance(criterion["description"], str) or type(criterion["points"]) not in {int, float}:
                    _fail()
                ratings = criterion.get("ratings", [])
                if not isinstance(ratings, list):
                    _fail()
                for rating in ratings:
                    _object(rating, {"rating_id", "description", "points"}, {"rating_id", "description", "points"})
                    _entity(rating["rating_id"])
                    if not isinstance(rating["description"], str) or type(rating["points"]) not in {int, float}:
                        _fail()
        elif value is not None and not isinstance(value, str):
            _fail()
    return deepcopy(record)


def validate_commit(record: dict) -> dict:
    _object(record, COMMIT_FIELDS, COMMIT_FIELDS)
    canonical_bytes(record)
    if type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported_schema")
    validate_digest(record["source_key"])
    for key in ("course_id", "scope_id", "writer_key", "run_id"):
        validate_component(record[key])
    if not isinstance(record["scope"], str) or record["scope"] not in SCOPE_KINDS:
        _fail("unsupported_scope")
    if not isinstance(record["mode"], str) or record["mode"] not in {"snapshot", "delta", "import"}:
        _fail("invalid_mode")
    if type(record["membership_complete"]) is not bool:
        _fail()
    if record["membership_complete"] and record["mode"] != "snapshot":
        _fail("invalid_completeness")
    for key in ("parents", "record_refs"):
        _strings(record[key], validate_digest)
    _strings(record["member_keys"])
    _timestamp(record["acquisition_started_at"], nullable=False)
    _timestamp(record["acquisition_finished_at"], nullable=False)
    started = datetime.fromisoformat(record["acquisition_started_at"].replace("Z", "+00:00"))
    finished = datetime.fromisoformat(record["acquisition_finished_at"].replace("Z", "+00:00"))
    if finished < started:
        _fail("invalid_acquisition_interval")
    if not isinstance(record["gaps"], list):
        _fail()
    for gap in record["gaps"]:
        _object(gap, {"code", "entity_key"}, {"code"})
        validate_component(gap["code"])
        if "entity_key" in gap:
            _entity(gap["entity_key"])
    if record["gaps"] and record["membership_complete"]:
        _fail("invalid_completeness")
    _object(record["watermarks"], {"submitted_since", "graded_since", "updated_since"})
    for value in record["watermarks"].values():
        _timestamp(value)
    return deepcopy(record)
