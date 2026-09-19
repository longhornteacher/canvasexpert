"""Student-free synced registration for SIS grade bridges."""

from __future__ import annotations

from copy import deepcopy

from . import _io as _io_mod


SETTINGS_KEY = "sis_grade_bridges"
_REGISTRATION_KEYS = (
    "family_title",
    "source_assignment_ids",
    "source_titles",
    "bridge_assignment_id",
    "bridge_state_digest",
)
_OPTIONAL_REGISTRATION_KEYS = ("family_key", "grading_excluded")


def _clean_text(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _normalize_registration(registration: dict) -> dict:
    if not isinstance(registration, dict):
        raise ValueError("bridge registration must be an object")
    clean = {
        "family_title": _clean_text(registration.get("family_title"), "family_title"),
        "source_assignment_ids": [
            _clean_text(value, "source_assignment_id")
            for value in (registration.get("source_assignment_ids") or [])
        ],
        "source_titles": [
            _clean_text(value, "source_title")
            for value in (registration.get("source_titles") or [])
        ],
        "bridge_assignment_id": _clean_text(
            registration.get("bridge_assignment_id"), "bridge_assignment_id"
        ),
        "bridge_state_digest": _clean_text(
            registration.get("bridge_state_digest"), "bridge_state_digest"
        ),
    }
    if len(clean["source_assignment_ids"]) < 2:
        raise ValueError("a bridge registration requires at least two source assignments")
    if len(clean["source_assignment_ids"]) != len(clean["source_titles"]):
        raise ValueError("source IDs and titles must have the same length")
    if len(set(clean["source_assignment_ids"])) != len(clean["source_assignment_ids"]):
        raise ValueError("source assignment IDs must be unique")
    if len(clean["bridge_state_digest"]) != 64:
        raise ValueError("bridge_state_digest must be a SHA-256 digest")
    if registration.get("family_key") not in (None, ""):
        clean["family_key"] = _clean_text(registration.get("family_key"), "family_key")
    if registration.get("grading_excluded") is not None:
        clean["grading_excluded"] = bool(registration.get("grading_excluded"))
    return clean


def get_sis_grade_bridge(course_id: str, family_title: str) -> dict | None:
    course_key = str(course_id or "").strip()
    title_key = str(family_title or "").strip()
    if not course_key or not title_key:
        return None
    registrations = _io_mod._synced_state().get(SETTINGS_KEY, {})
    record = (registrations.get(course_key) or {}).get(title_key)
    return deepcopy(record) if isinstance(record, dict) else None


def list_sis_grade_bridges(course_id: str) -> list[dict]:
    course_key = _clean_text(course_id, "course_id")
    registrations = _io_mod._synced_state().get(SETTINGS_KEY, {})
    records = registrations.get(course_key) or {}
    return [
        deepcopy(records[title])
        for title in sorted(records, key=lambda value: value.casefold())
        if isinstance(records[title], dict)
    ]


def save_sis_grade_bridge(course_id: str, registration: dict) -> dict:
    course_key = _clean_text(course_id, "course_id")
    clean = _normalize_registration(registration)

    def mutate(settings):
        course_records = settings.setdefault(SETTINGS_KEY, {}).setdefault(course_key, {})
        course_records[clean["family_title"]] = deepcopy(clean)
        return settings

    _io_mod._modify_synced(mutate)
    return deepcopy(clean)


__all__ = [
    "SETTINGS_KEY",
    "get_sis_grade_bridge",
    "list_sis_grade_bridges",
    "save_sis_grade_bridge",
]
