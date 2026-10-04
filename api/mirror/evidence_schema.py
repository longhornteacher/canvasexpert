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
SUBMISSION_FIELDS = frozenset({"assignment_id", "pseudonym", "attempt", "submitted_at", "body", "score", "grade", "late", "missing", "workflow_state", "updated_at", "effective_due_at"})
PAYLOAD_FIELDS = {
    "course": frozenset({"title", "workflow_state", "start_at", "end_at", "conclude_at", "term_end_at", "course_concluded", "enrollment_states", "restrict_enrollments_to_course_dates"}),
    "assignment": frozenset({"assignment_id", "title", "description", "points_possible", "due_at", "unlock_at", "lock_at", "all_dates", "updated_at", "rubric", "assignment_group_id", "published", "submission_types"}),
    "group": frozenset({"group_id", "title", "student_pseudonyms"}),
    "module": frozenset({"module_id", "title", "position", "published", "items"}),
    "page": frozenset({"page_id", "title", "body", "published", "front_page", "updated_at"}),
    "assignment_group": frozenset({"assignment_group_id", "title", "position", "group_weight"}),
    "student": frozenset({"pseudonym", "section_ids"}),
    "submission": SUBMISSION_FIELDS,
    "attempt_observation": SUBMISSION_FIELDS,
    "comment": frozenset({"assignment_id", "pseudonym", "attempt", "comment_id", "author_pseudonym", "author_role", "text", "created_at"}),
    "override": frozenset({"assignment_id", "override_id", "student_pseudonyms", "section_id", "group_id", "due_at", "unlock_at", "lock_at"}),
    "attachment": frozenset({"assignment_id", "pseudonym", "attempt", "attachment_key", "original_digest", "media_type", "size", "status", "revision"}),
    "attachment_extraction": frozenset({"assignment_id", "pseudonym", "attempt", "attachment_key", "original_digest", "extractor_version", "extraction_schema_version", "privacy_policy_revision", "availability", "method", "blocks", "partial_reasons", "processed_units", "total_units"}),
}
FACT_KINDS = frozenset(PAYLOAD_FIELDS)
# Attachment capture status is a bounded, value-free lifecycle label. ``pending``
# means the association is known but the original bytes are not yet archived;
# ``captured`` means a verified private ZIP exists for ``original_digest``.
ATTACHMENT_STATUSES = frozenset({"pending", "captured", "failed", "too_large",
                                 "unavailable", "foreign_origin"})
# Extraction result vocabulary, mirrored from api.mirror.extraction.schema so the
# strict safe-record validator stays a dependency-free leaf.
EXTRACTION_BLOCK_KINDS = frozenset({
    "paragraph", "heading", "list_item", "table_cell", "table_row",
    "slide_text", "slide_notes", "sheet_cell", "formula", "cached_value",
    "pdf_page", "image_text", "text_line", "code", "comment",
})
EXTRACTION_AVAILABILITY = frozenset({"complete", "partial", "empty", "unavailable"})
EXTRACTION_METHODS = frozenset({"native", "ocr", "mixed", "none"})
EXTRACTION_PARTIAL_REASONS = frozenset({
    "no_extractable_text", "corruption", "encryption", "unsupported_type",
    "missing_dependency", "timeout", "truncated", "resource_limit",
    "visual_content_unprocessed", "recognition_gap", "page_failed",
    "uncached_formula", "external_relation_skipped",
})
SCOPE_KINDS = {
    "course.context": frozenset({"course"}),
    "course.roster": frozenset({"student"}),
    "course.assignments": frozenset({"assignment"}),
    "course.groups": frozenset({"group"}),
    "course.modules": frozenset({"module"}),
    "course.pages": frozenset({"page"}),
    "course.assignment_groups": frozenset({"assignment_group"}),
    "assignment.submissions": frozenset({"submission", "attempt_observation"}),
    "assignment.comments": frozenset({"comment"}),
    "assignment.overrides": frozenset({"override"}),
    "assignment.attachments": frozenset({"attachment"}),
    "assignment.extractions": frozenset({"attachment_extraction"}),
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
# Opaque attachment identity: a hash of the Canvas file id (or stable metadata),
# never the raw id, filename, or download URL.
_ATTACHMENT_KEY = re.compile(r"^[0-9a-f]{16,64}$")
_TIMESTAMPS = frozenset({"submitted_at", "updated_at", "created_at", "due_at", "unlock_at", "lock_at", "effective_due_at", "start_at", "end_at", "conclude_at", "term_end_at"})
_IDS = frozenset({"assignment_id", "comment_id", "override_id", "section_id", "group_id", "module_id", "page_id", "assignment_group_id", "item_id", "content_id"})
_STRUCTURE_IDS = frozenset({"module_id", "page_id", "assignment_group_id", "item_id", "content_id"})
MODULE_ITEM_TYPES = frozenset({"File", "Page", "Discussion", "Assignment", "Quiz", "SubHeader", "ExternalUrl", "ExternalTool"})


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


def _structure_id(value):
    validate_component(value)
    if not value.isascii() or not value.isdecimal():
        _fail("invalid_navigation_id")


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
        "attachment": {"assignment_id", "pseudonym", "attachment_key", "status"},
        "attachment_extraction": {"assignment_id", "pseudonym", "attachment_key", "original_digest", "availability", "method", "blocks"},
        "group": {"group_id", "title", "student_pseudonyms"},
        "module": {"module_id", "title", "position", "items"},
        "page": {"page_id", "title"},
        "assignment_group": {"assignment_group_id", "title", "position", "group_weight"},
    }[kind]
    _object(payload, PAYLOAD_FIELDS[kind], required)
    for key, value in payload.items():
        if key in _TIMESTAMPS:
            _timestamp(value)
        elif key in _IDS:
            if key in required and value is None:
                _fail()
            if value is not None:
                (_structure_id if key in _STRUCTURE_IDS else validate_component)(value)
        elif key in {"pseudonym", "author_pseudonym"}:
            if key in required and value is None:
                _fail()
            if value is not None:
                _entity(value)
        elif key in {"section_ids", "student_pseudonyms", "submission_types", "enrollment_states"}:
            _strings(value)
            if key == "enrollment_states" and not set(value) <= {"active", "invited", "completed", "inactive"}:
                _fail()
        elif key == "attempt":
            if value is not None and (type(value) is not int or value < 1):
                _fail("invalid_attempt")
        elif key == "attachment_key":
            if not isinstance(value, str) or not _ATTACHMENT_KEY.fullmatch(value):
                _fail("invalid_attachment_key")
        elif key == "original_digest":
            if value is not None:
                validate_digest(value)
        elif key == "status":
            if value not in ATTACHMENT_STATUSES:
                _fail("invalid_attachment_status")
        elif key == "size":
            if type(value) is not int or value < 0:
                _fail("invalid_size")
        elif key == "revision":
            if type(value) is not int or value < 1:
                _fail("invalid_revision")
        elif key == "media_type":
            if (not isinstance(value, str) or len(value) > 160
                    or any(ord(char) < 32 for char in value)):
                _fail("invalid_media_type")
        elif key in {"score", "points_possible", "group_weight"}:
            if key == "group_weight" and value is None:
                _fail()
            if value is not None and type(value) not in {int, float}:
                _fail()
        elif key in {"late", "missing", "published", "front_page", "course_concluded", "restrict_enrollments_to_course_dates"}:
            if type(value) is not bool:
                _fail()
        elif key == "position":
            if type(value) is not int or value < 0:
                _fail()
        elif key == "items":
            if not isinstance(value, list):
                _fail()
            item_ids = []
            for item in value:
                _object(item, {"item_id", "title", "type", "position", "content_id", "page_id"}, {"item_id", "title", "type", "position"})
                _structure_id(item["item_id"])
                item_ids.append(item["item_id"])
                if not all(isinstance(item[field], str) for field in ("title", "type")) or type(item["position"]) is not int or item["position"] < 0:
                    _fail()
                if item["type"] not in MODULE_ITEM_TYPES:
                    _fail("unsupported_module_item_type")
                for field in ("content_id", "page_id"):
                    if item.get(field) is not None:
                        _structure_id(item[field])
            if len(set(item_ids)) != len(item_ids):
                _fail("duplicate_reference")
        elif key == "all_dates":
            if not isinstance(value, list):
                _fail()
            for dates in value:
                _object(dates, {"due_at", "unlock_at", "lock_at", "base", "override_id", "title"}, {"base"})
                if type(dates["base"]) is not bool:
                    _fail()
                for field in ("due_at", "unlock_at", "lock_at"):
                    if field in dates:
                        _timestamp(dates[field])
                if dates.get("override_id") is not None:
                    validate_component(dates["override_id"])
                if dates.get("title") is not None and not isinstance(dates["title"], str):
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
        elif key == "title" and not isinstance(value, str):
            _fail()
        elif key == "blocks":
            if not isinstance(value, list):
                _fail()
            block_ids = []
            for block in value:
                _object(block, {"block_id", "kind", "text", "locator", "confidence", "method", "formatting"},
                        {"block_id", "kind", "text"})
                if not isinstance(block["block_id"], str) or not block["block_id"]:
                    _fail("invalid_block_id")
                block_ids.append(block["block_id"])
                if not isinstance(block["kind"], str) or block["kind"] not in EXTRACTION_BLOCK_KINDS:
                    _fail("invalid_block_kind")
                if not isinstance(block["text"], str):
                    _fail("invalid_block_text")
                if block.get("method") is not None and block["method"] not in EXTRACTION_METHODS:
                    _fail("invalid_method")
                if block.get("confidence") is not None:
                    confidence = block["confidence"]
                    if (type(confidence) not in {int, float} or not math.isfinite(confidence)
                            or not 0.0 <= confidence <= 1.0):
                        _fail("invalid_confidence")
                if block.get("locator") is not None:
                    _object(block["locator"], set(block["locator"]), ())
                    for locator_value in block["locator"].values():
                        if isinstance(locator_value, bool) or not isinstance(locator_value, (str, int, float, type(None))):
                            _fail("invalid_locator")
                if block.get("formatting") is not None:
                    _object(block["formatting"], set(block["formatting"]), ())
                    for formatting_value in block["formatting"].values():
                        if isinstance(formatting_value, bool) or not isinstance(formatting_value, (str, int, float, type(None))):
                            _fail("invalid_formatting")
            if len(set(block_ids)) != len(block_ids):
                _fail("duplicate_block_id")
        elif key == "partial_reasons":
            _strings(value, validator=lambda entry: None if entry in EXTRACTION_PARTIAL_REASONS else _fail("invalid_partial_reason"))
        elif key == "availability":
            if value not in EXTRACTION_AVAILABILITY:
                _fail("invalid_availability")
        elif key == "method":
            if value not in EXTRACTION_METHODS:
                _fail("invalid_method")
        elif key in {"extraction_schema_version", "privacy_policy_revision"}:
            if type(value) is not int or value < 1:
                _fail("invalid_revision")
        elif key in {"processed_units", "total_units"}:
            if type(value) is not int or value < 0:
                _fail("invalid_counts")
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
