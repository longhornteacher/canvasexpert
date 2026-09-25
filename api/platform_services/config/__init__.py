"""QuizForge-API configuration.

Canvas base URL and the token remain machine-local. Synced user content moves to
the OneDrive workspace when it exists. One-time migration copies the synced keys
into workspace/settings.json and leaves the old machine keys in place as dead
data so the two-PC last-writer-wins sync model stays simple.

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
    get_download_root, set_download_root,
    save_canvas_account,
    get_workspace_path, set_workspace_path, ensure_workspace_pinned, get_whisper_model_cache,
    resolve_env,
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

# --- Feedback tools personas and teacher-authored contracts ---
from .feedback import (
    AI_TA_PERSONA_DEFAULT, DEFAULT_AI_DISCLOSURE_SIGNOFF,
    BUILTIN_PERSONAS, BUILTIN_PERSONAS_BY_ID,
    _persona_folder, get_persona_folder,
    _safe_persona_filename, _seed_persona_folder_once,
    _list_file_personas,
    list_personas, get_persona,
    save_custom_persona, remove_custom_persona,
    get_ai_ta_persona, set_ai_ta_persona,
    _feedback_contracts_folder, get_feedback_contracts_folder,
    _seed_feedback_contracts_folder_once, _list_file_feedback_contracts,
    list_feedback_contracts, get_feedback_contract,
)

# --- protected names ---
from .protected_names import (
    LITERARY_PACKS,
    list_protected_packs, set_pack_enabled,
    get_custom_protected_names, set_custom_protected_names,
    active_protected_names,
)

# --- routines state ---
from .routines import (
    get_routine_states, set_routine_state,
)

# --- student reports ---
from .reports import (
    STUDENT_REPORTS_DEFAULT,
    get_student_reports_root, set_student_reports_root,
    get_monitored_students, set_monitored_student, remove_monitored_student,
)

# --- Roster Console settings ---
from .roster import (
    ROSTER_SCORE_MATRIX_DEFAULT, ROSTER_RELATIONSHIPS_DEFAULT,
    ROSTER_BASELINE_DEFAULT,
    get_roster_student_settings, set_roster_student_settings,
    update_roster_student_settings,
    CLASSROOM_PROFILE_KEYS, CLASSROOM_CELEBRATION_KEYS,
    validate_classroom_profile, empty_classroom_profile,
    get_roster_score_matrix, set_roster_score_matrix,
    get_roster_relationships, set_roster_relationships,
    get_roster_baseline, set_roster_baseline,
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

# --- routine schedule policy ---
from .routines import get_late_sweep_holidays, get_routine_states, set_routine_state

# Re-export public constants from _io so consumers can still access config.SERVICE etc.
CANVAS_BASE_DEFAULT = _io.CANVAS_BASE_DEFAULT
DOWNLOAD_ROOT_DEFAULT = _io.DOWNLOAD_ROOT_DEFAULT
CONFIG_PATH = _io.CONFIG_PATH
SYNCED_KEYS = _io.SYNCED_KEYS
SERVICE = _io.SERVICE
TOKEN_KEY = _io.TOKEN_KEY
