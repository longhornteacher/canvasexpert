"""Roster Console configuration — student settings and local roster context.

Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
from datetime import date
import re

from . import _io as _io_mod


# --------------------------------------------------------------------------
# Roster student settings
# --------------------------------------------------------------------------

def get_roster_student_settings(course_id: str) -> dict:
    return _io_mod._synced_state().get("roster_student_settings", {}).get(str(course_id), {})


def set_roster_student_settings(course_id: str, settings: dict):
    _io_mod._modify_synced(
        lambda state: state.setdefault("roster_student_settings", {}).__setitem__(
            str(course_id), settings
        )
    )


def update_roster_student_settings(course_id: str, user_id: str, patch: dict):
    def mutate(state):
        all_settings = state.setdefault("roster_student_settings", {})
        course_settings = all_settings.setdefault(str(course_id), {})
        student = course_settings.setdefault(str(user_id), {})
        for key, value in patch.items():
            if value is None:
                student.pop(key, None)
            else:
                student[key] = value

    _io_mod._modify_synced(mutate)


# --------------------------------------------------------------------------
# Classroom-facing student profile
# --------------------------------------------------------------------------

CLASSROOM_PROFILE_KEYS = frozenset({"birthday", "celebrations"})
CLASSROOM_CELEBRATION_KEYS = frozenset({"id", "label", "start", "end"})
_MONTH_DAY_RE = re.compile(r"^\d{2}-\d{2}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _plain_text(value: object, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be plain text.")
    if required and not value.strip():
        raise ValueError(f"{field} is required.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field} cannot contain control characters.")
    if "<" in value or ">" in value:
        raise ValueError(f"{field} cannot contain HTML.")
    return value.strip()


def validate_classroom_profile(value: object) -> dict:
    """Validate and return the exact persisted classroom profile shape."""
    if not isinstance(value, dict):
        raise ValueError("classroom_profile must be an object.")
    unknown = set(value) - CLASSROOM_PROFILE_KEYS
    if unknown:
        raise ValueError(f"classroom_profile has unknown keys: {sorted(unknown)}")

    birthday = value.get("birthday", "")
    if not isinstance(birthday, str) or (birthday and not _MONTH_DAY_RE.fullmatch(birthday)):
        raise ValueError("classroom_profile.birthday must be MM-DD or empty.")
    if birthday:
        try:
            date(2000, int(birthday[:2]), int(birthday[3:]))
        except ValueError:
            raise ValueError("classroom_profile.birthday is not a real month/day.")

    celebrations = value.get("celebrations", [])
    if not isinstance(celebrations, list):
        raise ValueError("classroom_profile.celebrations must be a list.")
    normalized = []
    seen_ids = set()
    for index, item in enumerate(celebrations):
        if not isinstance(item, dict):
            raise ValueError(f"celebrations[{index}] must be an object.")
        unknown = set(item) - CLASSROOM_CELEBRATION_KEYS
        if unknown:
            raise ValueError(f"celebrations[{index}] has unknown keys: {sorted(unknown)}")
        celebration_id = _plain_text(item.get("id"), f"celebrations[{index}].id")
        if celebration_id in seen_ids:
            raise ValueError(f"celebrations has duplicate id '{celebration_id}'.")
        seen_ids.add(celebration_id)
        label = _plain_text(item.get("label"), f"celebrations[{index}].label")
        if len(label) > 160:
            raise ValueError(f"celebrations[{index}].label is limited to 160 characters.")
        dates = {}
        for key in ("start", "end"):
            raw = item.get(key)
            if not isinstance(raw, str) or not _ISO_DATE_RE.fullmatch(raw):
                raise ValueError(f"celebrations[{index}].{key} must be YYYY-MM-DD.")
            try:
                parsed = date.fromisoformat(raw)
            except ValueError:
                raise ValueError(f"celebrations[{index}].{key} is not a real date.")
            if parsed.isoformat() != raw:
                raise ValueError(f"celebrations[{index}].{key} must be YYYY-MM-DD.")
            dates[key] = raw
        if dates["start"] > dates["end"]:
            raise ValueError(f"celebrations[{index}] start cannot be after end.")
        normalized.append({"id": celebration_id, "label": label,
                           "start": dates["start"], "end": dates["end"]})
    return {"birthday": birthday, "celebrations": normalized}


def empty_classroom_profile() -> dict:
    return {"birthday": "", "celebrations": []}


# --------------------------------------------------------------------------
# Roster score matrix
# --------------------------------------------------------------------------

ROSTER_SCORE_MATRIX_DEFAULT = {"columns": [], "values_by_section": {}}


def get_roster_score_matrix(course_id: str) -> dict:
    matrices = _io_mod._synced_state().get("roster_score_matrices", {})
    return matrices.get(str(course_id), ROSTER_SCORE_MATRIX_DEFAULT)


def set_roster_score_matrix(course_id: str, matrix: dict):
    _io_mod._modify_synced(
        lambda state: state.setdefault("roster_score_matrices", {}).__setitem__(
            str(course_id), matrix
        )
    )


# --------------------------------------------------------------------------
# Section relationships
# --------------------------------------------------------------------------

ROSTER_RELATIONSHIPS_DEFAULT = {"by_section": {}}


def get_roster_relationships(course_id: str) -> dict:
    relationships = _io_mod._synced_state().get("roster_relationships", {})
    return relationships.get(str(course_id), ROSTER_RELATIONSHIPS_DEFAULT)


def set_roster_relationships(course_id: str, relationships: dict):
    _io_mod._modify_synced(
        lambda state: state.setdefault("roster_relationships", {}).__setitem__(
            str(course_id), relationships
        )
    )


# --------------------------------------------------------------------------
# Roster-change acknowledgment baseline
#
# Synced (not machine-local): this is the same course-scoped, Canvas-id-keyed
# shape as roster_score_matrices/monitored_students
# above, all of which already sync so a teacher's own second PC sees the same
# local roster state. It holds Canvas student and section ids only, never a
# real name, so it sits on the same side of the privacy wall those do.
# --------------------------------------------------------------------------

ROSTER_BASELINE_DEFAULT = {"acknowledged_at": "", "students": {}}


def get_roster_baseline(course_id: str) -> dict:
    baselines = _io_mod._synced_state().get("roster_baselines", {})
    return baselines.get(str(course_id), ROSTER_BASELINE_DEFAULT)


def set_roster_baseline(course_id: str, baseline: dict):
    _io_mod._modify_synced(
        lambda state: state.setdefault("roster_baselines", {}).__setitem__(
            str(course_id), baseline
        )
    )
