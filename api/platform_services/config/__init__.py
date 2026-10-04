"""QuizForge-API configuration.

Canvas base URL, token, and device runtime settings stay machine-local. Synced
settings live in the workspace's append-only `_Shared/kv/settings` store.

This package re-exports all public configuration functions. Consumers import it
as `from .. import config` and call `config.get_token()`, etc. — no import-path
changes needed.
"""

# Re-export every public name from the sub-modules.
# The `_io` private helpers are imported only by sibling sub-modules, not here.

# --- canvas account, workspace path, runtime env ---
from .canvas import (
    get_canvas_base, set_canvas_base,
    get_token, set_token, token_is_set,
    save_canvas_account,
    get_workspace_path, set_workspace_path, ensure_workspace_pinned,
)

# --- bookmarked courses ---
from .courses import (
    saved_courses, active_courses,
    course_display_name,
    set_course_active, bookmark_course, remove_course,
)

# --- CanvasMirror ---
from .mirror import (
    mirror_enabled, set_mirror_enabled,
    mirror_serve_max_age_hours, set_mirror_serve_max_age_hours,
)

# --- Gradebook tools ---
from .gradebook import (
    TIER_NAMES,
    get_extra_time, set_extra_time,
    get_tier_tags, set_tier_tags,
    get_tier_colors, set_tier_colors,
)

# --- Feedback tools teacher-authored contracts ---
from .feedback import (
    _feedback_contracts_folder, get_feedback_contracts_folder,
    _list_file_feedback_contracts,
    list_feedback_contracts, get_feedback_contract,
)

# --- protected names ---
from .protected_names import (
    LITERARY_PACKS,
    list_protected_packs, set_pack_enabled,
    get_custom_protected_names, set_custom_protected_names,
    active_protected_names,
)

# --- student reports ---
from .reports import (
    get_monitored_students, set_monitored_student, remove_monitored_student,
)

# --- Roster Console settings ---
from .roster import (
    get_roster_student_settings, set_roster_student_settings,
    update_roster_student_settings,
    CLASSROOM_PROFILE_KEYS, CLASSROOM_CELEBRATION_KEYS,
    validate_classroom_profile, empty_classroom_profile,
)

# --- SIS grade bridge registrations ---
from .sis_grade_bridge import (
    get_sis_grade_bridge, list_sis_grade_bridges, normalize_sis_grade_bridge,
    save_sis_grade_bridge,
)


def save_sis_grade_bridge_verified(course_id: str, registration: dict) -> bool:
    """Save a family link and confirm the read-back equals what was stored.

    The one check every writer uses. Save normalizes (it drops blank fields
    such as an empty module_name), so comparing against the raw input always
    fails. Defined here so it resolves this package's names at call time.
    """
    stored = save_sis_grade_bridge(course_id, registration)
    if not isinstance(stored, dict):
        stored = normalize_sis_grade_bridge(registration)
    return get_sis_grade_bridge(course_id, stored["family_title"]) == stored

# --- private I/O helpers (needed by sibling sub-modules and test monkeypatches) ---
from . import _io

# Re-export public constants from _io so consumers can still access config.SERVICE etc.
CANVAS_BASE_DEFAULT = _io.CANVAS_BASE_DEFAULT
CONFIG_PATH = _io.CONFIG_PATH
SYNCED_KEYS = _io.SYNCED_KEYS
SERVICE = _io.SERVICE
TOKEN_KEY = _io.TOKEN_KEY
