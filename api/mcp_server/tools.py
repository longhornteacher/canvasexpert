"""Plain, testable implementations of the MCP tools.

The authoritative tool count and shape live in ``contract.TOOL_SCHEMA_VERSION``
and its snapshot file, never in prose here.

Every function returns a ``{"ok": ...}`` dict and never raises — that keeps
errors structured for the LLM and matches the rest of the app's route style.
Fetchers and the vault factory are bound to module-level names so tests can
monkeypatch them without touching the real Canvas API or identity vault
(same pattern as ``api/tests/test_gradebook_routes.py``).

Every ``course_id`` tool gates on ``config.active_courses()`` — the same
Current-course scope the web UI uses. ``list_courses``,
``get_authoring_contract``, ``get_product_guide``, ``list_staged_content``,
``get_bell_schedule``, ``get_day_schedule``, ``get_teacher_schedule`` and
``get_school_calendar`` are
the only tools with no ``course_id`` and no student data, so they skip both the
course gate and the outbound safety gate. ``get_writing_history`` breaks that
pairing on purpose: it has no ``course_id`` either (the daily-writing store has
no course concept), but it is student data, so it still runs the identity vault
and the outbound safety gate.

Strict mirror-only law: get_roster, get_submissions, and
get_gradebook_snapshot serve ONLY from the local CanvasMirror and refuse
(rather than falling back to a live Canvas fetch) when it isn't fresh
enough. refresh_mirror is the assistant's only way to move that forward —
it triggers Canvas Expert's own sync engine and reports freshness, never
Canvas data, keeping the AI's whole path to Canvas indirect. get_writing_history
is not mirror-backed (the daily-writing store is not Canvas data at all), so
no staleness refusal applies to it."""
from __future__ import annotations

import os
import copy
import hashlib
import json
import math
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone

from api import content_push, course_scope, feedback_scrub, gradebook_queries, gradebook_snapshot, learning_objectives, operational_log, roster_context, roster_service, sis_grade_bridge
from api.dataforge import canvas_join, grouping, history_store, paths as dataforge_paths, profile_export
from api.dataforge.identity import IdentityMigrationError, VaultIdentity
from api.mirror import queries as mirror_queries
from api.mirror import read_service
from api.mirror import store as mirror_store
from api.platform_services import config, workspace
from api.webui import bell_schedule, mirror_service, schedule_setup, school_calendar
from api.webui.deps import REPO_ROOT
from api.webui import deps
from api import feedback_safety, feedback_vault
from api.course_catalog import read_catalog
from api import runtime_paths
from api.dailywriting import projection as dailywriting_projection
from api.dailywriting.store.identity import IdentityError
from api.dailywriting.store.repo import Repository as DailyWritingRepository
from api.dailywriting.store.repo import StoreError as DailyWritingStoreError

from . import contract, pseudonym

_NEW_QUIZ_HTTP: ContextVar[object | None] = ContextVar("ce_new_quiz_http", default=None)


# Compatibility seams retained for existing route-style tests; the bound
# implementations all live in root-level shared use-case modules.
_assignment = gradebook_queries.assignment
_assignment_submissions = gradebook_queries.assignment_submissions
_fetch_sections = roster_service.fetch_sections
_ORIGINAL_ASSIGNMENT = _assignment
_ORIGINAL_ASSIGNMENT_SUBMISSIONS = _assignment_submissions
_ORIGINAL_FETCH_SECTIONS = _fetch_sections
_ORIGINAL_PSEUDONYM_FETCH_STUDENTS = pseudonym._fetch_students
_ORIGINAL_ROSTER_FETCH_STUDENTS = roster_service.fetch_students
_ORIGINAL_ROSTER_FETCH_SECTIONS = roster_service.fetch_sections

# Bound seams for the assistant-invokable refresh tool, so tests can point
# these at a fake coordinator without starting the real background workers.
_enqueue_sync = mirror_service.enqueue_sync
_wait_for_plan = mirror_service.wait_for_plan

# Bound so tests can point get_writing_history at a tmp_path store with a
# fixture MappingResolver instead of the real workspace + identity vault
# (same reason _vault_factory exists). `pseudonym.gate` is bound too: the
# tool's own parameter is named `pseudonym` (locked by the brief, matching
# the read pattern), which would otherwise shadow the `pseudonym` module
# inside that one function.
_dailywriting_repository_factory = DailyWritingRepository.default
_pseudonym_gate = pseudonym.gate


# ---------------------------------------------------------------------------
# Token-lean payload shaping.
#
# MCP tool results are carried in protocol responses, so list data goes out as
# one {"columns": [...],
# "rows": [[...]]} table instead of repeating JSON keys per row. The privacy
# gate always runs on the dict-row payload BEFORE tabulation — never on the
# tabular form, which the scanner's key-based walk cannot see into.
# ---------------------------------------------------------------------------

_DESCRIPTION_PREVIEW_CHARS = 300
_DEFAULT_MAX_TEXT_CHARS = 2000

_SUBMISSION_COLUMNS = ("pseudonym", "workflow_state", "submitted_at", "late",
                       "missing", "excused", "score", "grade", "text", "current_enrollment")
_ROSTER_COLUMNS = ("pseudonym", "section_names")
_ASSIGNMENT_COLUMNS = ("id", "title", "due_at", "points_possible",
                       "published", "description_text")
_GRADEBOOK_ASSIGNMENT_COLUMNS = ("id", "title", "due_at", "points", "has_submission",
                                 "has_grade", "ungraded", "partially_scored",
                                 "missing", "late", "avg_pct")
_GRADEBOOK_STUDENT_COLUMNS = ("pseudonym", "missing", "late", "ungraded", "pct")
_MODULE_COLUMNS = ("id", "name", "position", "item_count")
_MODULE_ITEM_COLUMNS = ("id", "type", "title", "position", "content_id")
_PAGE_COLUMNS = ("id", "title", "body_text", "published", "front_page", "updated_at")
_STAGED_CONTENT_COLUMNS = ("kind", "label")
_SCORING_SESSION_COLUMNS = (
    "scoring_session_id", "created", "status", "active_course", "active_assignment",
    "total", "completed", "completed_with_holds", "no_longer_needs_grading",
    "remaining", "failed",
)
_PACKET_ITEM_COLUMNS = ("item_id", "prompt", "possible")
_PACKET_STUDENT_COLUMNS = ("pseudonym", "item_id", "text")
_ASSESSMENT_CONTEXT_COLUMNS = (
    "pseudonym", "assessment_count", "latest_assessment_date",
    "latest_percentage", "weak_standard_codes", "standards",
)

_ASSESSMENT_GROUPING_PLACEMENT_COLUMNS = ("pseudonym", "score", "status", "group")

MAX_ASSESSMENT_CONTEXT_STUDENTS = 25
MAX_ASSESSMENT_CONTEXT_STANDARDS = 32
MAX_ASSESSMENT_CONTEXT_ASSESSED_IN = 8
MAX_ASSESSMENT_GROUPING_STUDENTS = 25
MAX_ASSESSMENT_GROUPING_RESULT_CHARS = 20000

_NEXT_STEPS = {
    "get_scoring_packet": (
        "Read total as response rows and students_total as people. Keep the scoring "
        "contract and rubric on page zero; use next_offset for later pages. After "
        "reading every page, call submit_scoring_results with one "
        "{pseudonym, item_id, score, feedback} row per packet student row and "
        "packet_digest as expected_packet_digest."
    ),
    "start_scoring_session": (
        "Call continue_scoring_session with scoring_session_id to prepare the first "
        "assignment in the frozen queue."
    ),
    "continue_scoring_session": (
        "When status is ready, call get_scoring_packet with scoring_session_id. "
        "If response_count is 0 and held is greater than zero, explain that held "
        "responses could not be scored from text."
    ),
    "preview_sis_grade_bridge": (
        "Summarize the aggregate review and get teacher confirmation, then call "
        "apply_sis_grade_bridge with batch_id, operation_id, and review_digest unchanged."
    ),
    "preview_learning_objective": (
        "Summarize the preview and get teacher confirmation, then call "
        "apply_learning_objective with course_id, preview, preview_digest, and "
        "current_revision as expected_revision."
    ),
    "preview_roster_student_change": (
        "Summarize the change and get teacher confirmation, then call "
        "apply_roster_student_change with course_id, preview, preview_digest, and "
        "settings_digest as expected_settings_digest."
    ),
    "preview_content_push": (
        "Tell the teacher what the preview says this will create, then call "
        "apply_content_push with operation_id, batch_id, and review_digest "
        "unchanged. A teacher who asked for the push has already authorized it."
    ),
    "preview_differentiated_quiz_push": (
        "Tell the teacher what the differentiated review says this will create, then call "
        "apply_content_push with operation_id, batch_id, and review_digest unchanged."
    ),
    "preview_assignment_update": (
        "Tell the teacher what the field diff says this will change, then call "
        "apply_assignment_update with operation_id, batch_id, and review_digest "
        "unchanged. A teacher who asked for the update has already authorized it."
    ),
}


def _with_next(tool_name: str, result: dict) -> dict:
    """Attach one static post-result procedure to an authorized success payload."""
    if result.get("ok"):
        return {**result, "next": _NEXT_STEPS[tool_name]}
    return result


def _tabulate(rows: list[dict], columns: tuple[str, ...]) -> dict:
    return {"columns": list(columns),
            "rows": [[row.get(col) for col in columns] for row in rows]}


_STUDENT_RESULT_KEYS = {
    "pseudonym", "roster", "submissions", "students", "student",
    "assessment_context", "placements", "writing_history",
    "extra_time", "monitored", "classroom_profile",
}


def _contains_student_result(value) -> bool:
    if isinstance(value, dict):
        if any(str(key).casefold() in _STUDENT_RESULT_KEYS for key in value):
            return True
        return any(_contains_student_result(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_student_result(item) for item in value)
    return False


def final_response_gate(payload: dict) -> dict:
    """Structural last-mile gate used by every MCP server wrapper.

    Student-data tools still gate their precise payloads before shaping them;
    this second chokepoint catches a new or changed wrapper that forgets that
    step. Public/course-only results pass through without requiring a vault.
    """
    if not _contains_student_result(payload):
        return payload
    vault, error = _open_vault()
    if error:
        return {"ok": False, "error": error}
    return pseudonym.gate(payload, vault)


def list_sis_grade_bridges(course_id: str) -> dict:
    """List student-free SIS grade-bridge registrations for one Current course."""
    return sis_grade_bridge.list_sis_grade_bridges(course_id)


def preview_sis_grade_bridge(course_id: str, family_title: str) -> dict:
    """Prepare one exact family bridge and return only aggregate review facts."""
    return _with_next(
        "preview_sis_grade_bridge",
        sis_grade_bridge.preview_sis_grade_bridge(course_id, family_title),
    )


def apply_sis_grade_bridge(
    operation_id: str, batch_id: str, review_digest: str
) -> dict:
    """Apply only the opaque, digest-protected SIS bridge review."""
    return sis_grade_bridge.apply_sis_grade_bridge(
        operation_id, batch_id, review_digest
    )


def _truncate_text(text: str, max_chars: int) -> str:
    """Trim with an explicit marker so the client knows to re-request the
    full text (max_text_chars=0) instead of assuming it saw everything."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + f" …[truncated {len(text) - max_chars} more chars]"


# ---------------------------------------------------------------------------
# Fetch-seam detection.
#
# Strict mirror-only law: the student-data tools never call live Canvas, so
# there is no fetch cache to protect here anymore. ``_cache_safe`` survives
# purely as the seam guard the mirror-first helpers below use to refuse
# serving mirror data out from under a test that has monkeypatched one of
# these fetchers for an unrelated purpose.
# ---------------------------------------------------------------------------


def _cache_safe() -> bool:
    """True only when every roster fetch seam is the real implementation."""
    return (
        pseudonym._fetch_students is _ORIGINAL_PSEUDONYM_FETCH_STUDENTS
        and roster_service.fetch_students is _ORIGINAL_ROSTER_FETCH_STUDENTS
        and _fetch_sections is _ORIGINAL_FETCH_SECTIONS
        and roster_service.fetch_sections is _ORIGINAL_ROSTER_FETCH_SECTIONS
    )


# ---------------------------------------------------------------------------
# Mirror-only reads.
#
# The student-data tools serve ONLY from the local CanvasMirror, never live
# Canvas: instant, offline-tolerant, zero Canvas round trips, and the AI's
# path to Canvas always stays indirect (through Canvas Expert's own sync
# engine, never a direct relay). When the mirror isn't fresh enough to serve
# (or a test seam is patched — the seam guard refuses rather than silently
# reading disk out from under it), these helpers return None so the caller
# refuses instead of fetching live. Mirror-only payloads are labeled
# source="mirror" + synced_at; a caller that joins private local context must
# label that boundary explicitly, so staleness is visible and never silent.
# ---------------------------------------------------------------------------

def _mirror_roster_doc(course_id: str):
    """The typed roster scope (students + sections) when current and
    unseamed, else None. Sections have no dedicated typed scope, so the
    raw roster document is read once more, only after the typed freshness
    check passes, purely to recover the section id -> name map."""
    if not _cache_safe():
        return None
    roster = read_service.private_roster(
        course_id, max_age_hours=mirror_queries._serve_max_age_hours())
    if roster["state"] != "current":
        return None
    document = mirror_store.read_roster(course_id)
    if document is None:
        return None
    return {"students": roster["records"], "sections": document["sections"],
            "last_success_at": roster["last_success_at"]}


def _mirror_submission_bundle(course_id: str, assignment_id: str):
    """``({assignment, rows, roster, synced_at}, None)`` from typed local
    scopes when roster, assignments, and submissions are ALL current and
    ``assignment_id`` is one of them. ``(None, None)`` means the mirror
    itself isn't fresh enough to serve (missing or stale any one piece,
    including the seam-guard case); ``(None, message)`` means the mirror IS
    fresh but no such assignment exists in this course — a distinct,
    non-staleness error worth reporting verbatim."""
    if not _cache_safe():
        return None, None
    if (_assignment is not _ORIGINAL_ASSIGNMENT
            or _assignment_submissions is not _ORIGINAL_ASSIGNMENT_SUBMISSIONS):
        return None, None
    max_age_hours = mirror_queries._serve_max_age_hours()
    roster = read_service.private_roster(course_id, max_age_hours=max_age_hours)
    assignments = read_service.private_assignments(course_id, max_age_hours=max_age_hours)
    submissions = read_service.private_submissions(course_id, max_age_hours=max_age_hours)
    if not (roster["state"] == "current" and assignments["state"] == "current"
            and submissions["state"] == "current"):
        return None, None
    assignment_row = next(
        (row for row in assignments["records"] if str(row.get("id")) == str(assignment_id)),
        None)
    if assignment_row is None:
        return None, "No such assignment in this course's local catalog."
    rows = [row for row in submissions["records"]
            if str(row.get("assignment_id")) == str(assignment_id)]
    synced_at = min(roster["last_success_at"], assignments["last_success_at"],
                    submissions["last_success_at"])
    return {"assignment": assignment_row, "rows": rows,
            "roster": roster["records"], "synced_at": synced_at}, None


def _load_snapshot(course_id: str):
    """``(snapshot, None)`` from the CanvasMirror ONLY when it's fresh enough
    to serve the whole gradebook (roster + assignments + submissions), else
    ``(None, error)``. Never falls back to live Canvas — unlike the shared
    ``gradebook_snapshot.load_snapshot`` the web UI's own gradebook route
    uses, which keeps that live fallback for its own grading flows."""
    namespace, synced_at = mirror_queries.snapshot_queries(course_id)
    if namespace is None:
        return None, _MIRROR_UNAVAILABLE_SNAPSHOT_ERROR
    snapshot, error = gradebook_snapshot.load_snapshot(course_id, queries=namespace)
    if error:
        return None, error
    snapshot["source"] = "mirror"
    snapshot["synced_at"] = synced_at
    return snapshot, None


_VAULT_UNAVAILABLE_ERROR = (
    "Canvas Expert cannot find your workspace, so student data is withheld. "
    "Open Canvas Expert on this computer once (or reconnect from the CanvasAgent page), "
    "then try again."
)


class _VaultUnavailable(Exception):
    """Raised when the identity vault directory cannot be resolved."""


def _default_vault() -> feedback_vault.Vault:
    """Mirror ``api/webui/routes/names.py::_vault`` — the one global identity
    vault, keyed by Canvas user id.

    Fails closed when the workspace (and thus the vault directory) cannot be
    resolved, rather than falling back to a stray ``./vault.json``. That stray
    fallback would misplace the re-identification map outside the protected
    workspace and hand out unstable pseudonyms, so student-data tools must
    refuse instead."""
    root = workspace.identity_vault_dir()
    if not root:
        raise _VaultUnavailable(_VAULT_UNAVAILABLE_ERROR)
    return feedback_vault.Vault(os.path.join(root, "vault.json"))


# Bound to a module-level name so tests can point it at a tmp_path vault.
_vault_factory = _default_vault


@contextmanager
def _vault_transaction(vault):
    """Use the durable vault transaction, with a narrow test-double fallback."""
    transaction = getattr(vault, "transaction", None)
    if transaction is not None:
        with transaction():
            yield vault
        return
    try:
        yield vault
    finally:
        save = getattr(vault, "save", None)
        if save is not None:
            save()


def _saved_course_gate_check(course_id: str) -> str | None:
    """Reject IDs absent from list_courses before suggesting any refresh."""
    course_key = str(course_id or "").strip()
    saved_ids = {
        str(course.get("id") or "").strip()
        for course in [*(config.saved_courses() or []), *(config.active_courses() or [])]
        if str(course.get("id") or "").strip()
    }
    if course_key not in saved_ids:
        return (
            f"Unknown course_id '{course_key}'; call list_courses and use a "
            "returned course_id."
        )
    return None


def _course_gate_check(course_id: str) -> str | None:
    """Current-course scope check, same as the web UI."""
    return course_scope.current_course_error(course_id, config.active_courses())


# ---------------------------------------------------------------------------
# Pseudonym-first local Roster settings tools (schema v21)
# ---------------------------------------------------------------------------

_MCP_ROSTER_PATCH_KEYS = {
    "pseudonym", "regenerate_pseudonym", "extra_time", "monitored",
    "canvas_group", "classroom_profile", "add_nicknames",
}
_MCP_ROSTER_CLEAR_KEYS = {"extra_time", "monitored", "classroom_profile"}


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _roster_group_for_user(course_id: str, user_id: str) -> dict | None:
    document = mirror_store.read_groups(course_id) or {}
    scheme = config.get_roster_group_scheme(course_id)
    selected = str(scheme.get("selected_group_category_id") or "")
    for category in document.get("categories") or document.get("groups") or []:
        category_id = str(category.get("category_id") or category.get("id") or "")
        if selected and category_id != selected:
            continue
        for group in category.get("groups") or []:
            members = group.get("student_ids") or []
            if str(user_id) in {str(member) for member in members}:
                return {"category_id": category_id, "group_id": str(group.get("id") or ""),
                        "group_name": group.get("name") or ""}
            for membership in group.get("memberships") or []:
                if str(membership.get("user_id") or "") == str(user_id):
                    return {"category_id": category_id, "group_id": str(group.get("id") or ""),
                            "group_name": group.get("name") or ""}
    return None


def _roster_full_record(course_id: str, user_id: str, vault) -> dict:
    vault_entry = next((entry for entry in vault.entries()
                        if str(entry.get("canvas_id")) == str(user_id)), {})
    local = config.get_roster_student_settings(course_id) or {}
    local_entry = local.get(str(user_id), {}) if isinstance(local, dict) else {}
    extra = next((entry for entry in config.get_extra_time(course_id) or []
                  if str(entry.get("id")) == str(user_id)), None)
    monitored = (config.get_monitored_students() or {}).get(str(user_id))
    return {
        "vault": vault_entry,
        "local": local_entry if isinstance(local_entry, dict) else {},
        "extra_time": extra,
        "monitored": monitored,
        "canvas_group": _roster_group_for_user(course_id, user_id),
    }


def _roster_safe_projection(course_id: str, user_id: str, vault) -> dict:
    record = _roster_full_record(course_id, user_id, vault)
    local = record["local"]
    extra = record["extra_time"] or {}
    monitored = record["monitored"] or {}
    group = record["canvas_group"]
    profile = local.get("classroom_profile", config.empty_classroom_profile())
    try:
        profile = config.validate_classroom_profile(profile)
    except ValueError:
        profile = config.empty_classroom_profile()
    return {
        "pseudonym": record["vault"].get("pseudonym", ""),
        "extra_time": {"enabled": bool(extra), "days": extra.get("days", 0) if extra else 0},
        "monitored": {"enabled": bool(monitored)},
        "canvas_group": group,
        "classroom_profile": profile,
    }


def _open_roster_student(course_id: str, requested: str):
    vault, vault_err = _open_vault()
    if vault_err:
        return None, vault_err
    mirror_doc = _mirror_roster_doc(course_id)
    if mirror_doc is None:
        return None, _MIRROR_UNAVAILABLE_ROSTER_ERROR
    with _vault_transaction(vault):
        roster_service.upsert_roster(vault, mirror_doc["students"])
        user_id = pseudonym.resolve_pseudonym(vault, mirror_doc["students"], requested)
        if not user_id:
            return None, "No current local roster student matches that pseudonym."
        return {"vault": vault, "students": mirror_doc["students"], "user_id": user_id}, None


def _gate_roster_result(payload: dict, vault) -> dict:
    return pseudonym.gate(payload, vault)


def _validate_mcp_roster_patch(patch: object) -> tuple[dict | None, str | None]:
    if not isinstance(patch, dict):
        return None, "patch must be an object."
    if "nicknames" in patch:
        return None, "nicknames is not available through MCP; use add_nicknames."
    unknown = set(patch) - _MCP_ROSTER_PATCH_KEYS
    if unknown:
        return None, f"Unknown patch keys: {sorted(unknown)}"
    if "add_nicknames" in patch and (
        not isinstance(patch["add_nicknames"], list)
        or any(not isinstance(value, str) for value in patch["add_nicknames"])
    ):
        return None, "add_nicknames must be a list of strings."
    return patch, None


def get_roster_student_settings(course_id: str, pseudonym: str) -> dict:
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}
    target, error = _open_roster_student(course_id, pseudonym)
    if error:
        return {"ok": False, "error": error}
    vault, user_id = target["vault"], target["user_id"]
    result = _gate_roster_result({
        "pseudonym": vault.get_or_assign(user_id),
        "settings": _roster_safe_projection(course_id, user_id, vault),
        "settings_digest": _canonical_digest(_roster_full_record(course_id, user_id, vault)),
    }, vault)
    return result


def preview_roster_student_change(course_id: str, pseudonym: str, patch: dict) -> dict:
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}
    patch, error = _validate_mcp_roster_patch(patch)
    if error:
        return {"ok": False, "error": error}
    target, error = _open_roster_student(course_id, pseudonym)
    if error:
        return {"ok": False, "error": error}
    vault, user_id = target["vault"], target["user_id"]
    before = _roster_safe_projection(course_id, user_id, vault)
    after = dict(before)
    for key, value in patch.items():
        if key == "add_nicknames":
            after[key] = list(value)
        elif key == "regenerate_pseudonym":
            after[key] = bool(value)
        else:
            after[key] = value
    preview = {"pseudonym": before["pseudonym"], "patch": patch,
               "before": {key: before.get(key) for key in patch},
               "after": {key: after.get(key) for key in patch},
               "settings_digest": _canonical_digest(_roster_full_record(course_id, user_id, vault))}
    return _gate_roster_result(_with_next("preview_roster_student_change", {
        "ok": True,
        "pseudonym": before["pseudonym"],
        "settings_digest": preview["settings_digest"],
        "preview": preview,
        "preview_digest": _canonical_digest(preview),
    }), vault)


def _apply_roster_update(course_id: str, vault, user_id: str, patch: dict) -> dict:
    from api.webui import roster_mcp
    return roster_mcp.update_student(
        course_id, user_id, patch, vault)


def apply_roster_student_change(course_id: str, preview: dict,
                                preview_digest: str, expected_settings_digest: str) -> dict:
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}
    if not isinstance(preview, dict) or _canonical_digest(preview) != preview_digest:
        return {"ok": False, "error": "Preview digest mismatch; nothing was written."}
    if preview.get("settings_digest") != expected_settings_digest:
        return {"ok": False, "error": "Expected settings digest does not match the preview; nothing was written."}
    requested = preview.get("pseudonym")
    patch, error = _validate_mcp_roster_patch(preview.get("patch"))
    if error:
        return {"ok": False, "error": error}
    target, error = _open_roster_student(course_id, requested)
    if error:
        return {"ok": False, "error": error}
    vault, user_id = target["vault"], target["user_id"]
    current_digest = _canonical_digest(_roster_full_record(course_id, user_id, vault))
    if current_digest != expected_settings_digest:
        return {"ok": False, "error": "Settings changed since preview; nothing was written."}
    result = _apply_roster_update(course_id, vault, user_id, patch)
    if not result.get("ok"):
        return result
    fresh = _roster_full_record(course_id, user_id, vault)
    return _gate_roster_result({"pseudonym": vault.get_or_assign(user_id),
                                "settings_digest": _canonical_digest(fresh)}, vault)


def clear_roster_student_field(course_id: str, pseudonym: str, field: str,
                               expected_settings_digest: str) -> dict:
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}
    if field in {"nicknames", "add_nicknames"}:
        return {"ok": False, "error": f"{field} cannot be cleared through MCP."}
    if field not in _MCP_ROSTER_CLEAR_KEYS:
        return {"ok": False, "error": "That roster field has no supported direct clear operation."}
    target, error = _open_roster_student(course_id, pseudonym)
    if error:
        return {"ok": False, "error": error}
    vault, user_id = target["vault"], target["user_id"]
    if _canonical_digest(_roster_full_record(course_id, user_id, vault)) != expected_settings_digest:
        return {"ok": False, "error": "Settings changed since read; nothing was written."}
    patch = {
        "extra_time": {"enabled": False} if field == "extra_time" else None,
        "monitored": {"enabled": False} if field == "monitored" else None,
        "classroom_profile": config.empty_classroom_profile() if field == "classroom_profile" else None,
    }
    result = _apply_roster_update(course_id, vault, user_id, {field: patch[field]})
    if not result.get("ok"):
        return result
    return _gate_roster_result({"pseudonym": vault.get_or_assign(user_id),
                                "settings_digest": _canonical_digest(_roster_full_record(course_id, user_id, vault))}, vault)


_VAULT_CONFLICT_ERROR = (
    "identity vault conflict detected — open the Students page in Canvas Expert "
    "to review it before pseudonymized reads continue"
)


def _vault_conflict_check(vault) -> str | None:
    """Fail-closed guard: a forked vault.json (OneDrive conflict copy) can
    assign a second pseudonym to the same student and silently break scrub
    coverage, so refuse pseudonymized reads until a teacher resolves it in
    the web UI. Test doubles without a ``conflicts()`` method (existing
    ``_vault_factory`` monkeypatches) are treated as conflict-free."""
    conflicts = getattr(vault, "conflicts", None)
    if conflicts is None:
        return None
    if conflicts():
        return _VAULT_CONFLICT_ERROR
    return None


def _open_vault():
    """Open the identity vault, failing closed if it is unavailable or forked.

    Returns ``(vault, None)`` on success or ``(None, error)`` for the tool to
    return verbatim."""
    try:
        vault = _vault_factory()
    except _VaultUnavailable as error:
        return None, str(error)
    return vault, _vault_conflict_check(vault)


def list_courses() -> dict:
    """All saved courses (Current + Previous). No Canvas call, no student
    data — no course gate, no safety gate."""
    courses = config.saved_courses()
    return {
        "ok": True,
        "courses": [
            {
                "course_id": str(c.get("id", "")),
                "course_name": str(c.get("nickname") or c.get("name") or ""),
                "active": bool(c.get("active", True)),
                "lifecycle": mirror_store.read_course_context(
                    str(c.get("id", ""))).get("lifecycle", "unknown"),
            }
            for c in courses
        ],
    }


_SECTION_COLUMNS = ("section_id", "section_name")


def list_sections(course_id: str) -> dict:
    """Section names from the local CanvasMirror roster (disk-only, no live
    Canvas fallback) for any saved course (Current or Previous). No student
    data — no vault, no safety gate. Returns
    a {columns, rows} table of (section_id, section_name)."""
    identity_error = _saved_course_gate_check(course_id)
    if identity_error:
        return {"ok": False, "error": identity_error}
    document = mirror_store.read_roster(course_id)
    if document is None:
        return {
            "ok": False,
            "error": _MIRROR_UNAVAILABLE_ROSTER_ERROR,
        }

    sections = document.get("sections", {})
    rows = [
        {"section_id": str(sid), "section_name": str(name)}
        for sid, name in sections.items()
    ]
    return {
        "ok": True,
        "course_id": course_id,
        "sections": _tabulate(rows, _SECTION_COLUMNS),
    }


def list_groups(course_id: str) -> dict:
    """List current-course group-set and group names from the local mirror only.

    Memberships and Canvas identifiers are deliberately consumed here and never
    enter the assistant-facing projection.
    """
    error = _course_gate_check(course_id)
    if error:
        return {"ok": False, "error": error}
    try:
        scope = read_service.private_groups(
            course_id, max_age_hours=read_service.GROUPS_MAX_AGE_HOURS,
        )
    except Exception:
        scope = {"state": "malformed", "records": None}
    if scope.get("state") != "current":
        state = scope.get("state") or "unavailable"
        return {
            "ok": False,
            "error": "A fresh local Canvas group mirror is required for group discovery.",
            "state": state,
            "attention": {
                "action": "refresh_mirror",
                "reason": "Refresh the current course mirror, then retry list_groups.",
            },
        }
    records = scope.get("records")
    if not isinstance(records, list):
        return {
            "ok": False,
            "error": "The local Canvas group mirror is malformed.",
            "state": "malformed",
            "attention": {
                "action": "refresh_mirror",
                "reason": "Refresh the current course mirror, then retry list_groups.",
            },
        }
    group_sets = []
    selected_id = str(
        (config.get_roster_group_scheme(course_id) or {}).get(
            "selected_group_category_id"
        ) or ""
    )
    selected_name = None
    for category in records:
        if not isinstance(category, dict):
            return {
                "ok": False,
                "error": "The local Canvas group mirror is malformed.",
                "state": "malformed",
                "attention": {"action": "refresh_mirror", "reason": "Refresh the current course mirror, then retry list_groups."},
            }
        category_name = str(category.get("category_name") or "").strip()
        category_id = str(category.get("category_id") or "")
        groups = category.get("groups")
        if not category_name or not isinstance(groups, list):
            return {
                "ok": False,
                "error": "The local Canvas group mirror is malformed.",
                "state": "malformed",
                "attention": {"action": "refresh_mirror", "reason": "Refresh the current course mirror, then retry list_groups."},
            }
        if selected_id and category_id == selected_id:
            selected_name = category_name
        safe_groups = []
        for group in groups:
            if not isinstance(group, dict) or not str(group.get("name") or "").strip():
                return {
                    "ok": False,
                    "error": "The local Canvas group mirror is malformed.",
                    "state": "malformed",
                    "attention": {"action": "refresh_mirror", "reason": "Refresh the current course mirror, then retry list_groups."},
                }
            safe_groups.append({"name": str(group["name"]).strip()})
        group_sets.append({"name": category_name, "groups": safe_groups})
    result = {"ok": True, "course_id": str(course_id), "group_sets": group_sets,
              "selected_group_set": selected_name}
    if selected_name is None:
        result["attention"] = {
            "action": "select_group_set",
            "reason": "Select the group set to use for Roster in the Roster page before differentiated delivery.",
        }
    return result


def get_course_assignments(course_id: str, full_descriptions: bool = False) -> dict:
    """Assignment metadata from the local course catalog (disk-only, no live
    Canvas fallback — refresh the catalog from the web UI first) for any
    saved course (Current or Previous). No student data — no safety gate.
    Descriptions are trimmed to a preview unless ``full_descriptions`` is set;
    assignments go out as a {columns, rows} table."""

    identity_error = _saved_course_gate_check(course_id)
    if identity_error:
        return {"ok": False, "error": identity_error}
    read_result = read_catalog(course_id)
    scope = read_service.catalog_assignments(
        course_id, catalog_reader=lambda _course_id: read_result)
    if scope["source"] == "none":
        return {
            "ok": False,
            "error": ("No local course catalog found for this course. Refresh "
                      "the catalog from the CanvasExpert web UI, then try again."),
        }

    description_chars = 0 if full_descriptions else _DESCRIPTION_PREVIEW_CHARS
    assignments = [
        {
            "id": a.get("id"),
            "title": a.get("name", ""),
            "description_text": _truncate_text(a.get("description_text", ""),
                                               description_chars),
            "due_at": a.get("due_at", ""),
            "points_possible": a.get("points_possible"),
            "published": a.get("published", True),
        }
        for a in scope["records"]
    ]
    return {
        "ok": True,
        "course_id": str((read_result.get("catalog") or {}).get("course_id") or course_id),
        "course_name": str((read_result.get("catalog") or {}).get("course_name") or ""),
        "assignments": _tabulate(assignments, _ASSIGNMENT_COLUMNS),
    }


def get_modules(course_id: str, include_items: bool = False) -> dict:
    """Module structure from the local course catalog (disk-only, no live
    Canvas fallback — refresh the catalog from the web UI first) for any
    saved course (Current or Previous). No student data — no vault, no safety
    gate. Staleness is labeled (source, synced_at, state), never refused,
    since modules are structural, not student data. Modules go out as a
    {columns, rows} table; include_items nests each module's items as their
    own {columns, rows} table. The v3 catalog module contract has no published
    column; module item identity is carried as ``content_id``."""

    identity_error = _saved_course_gate_check(course_id)
    if identity_error:
        return {"ok": False, "error": identity_error}
    read_result = read_catalog(course_id)
    # Age-gate the stored catalog against the same mirror serve-age window
    # used elsewhere, so "current" means fresh, not just last-refreshed-ever;
    # a stale catalog is still returned in full, only relabeled "stale".
    scope = read_service.catalog_modules(
        course_id, catalog_reader=lambda _course_id: read_result,
        max_age_hours=mirror_queries._serve_max_age_hours())
    if scope["source"] == "none":
        return {
            "ok": False,
            "error": ("No local course catalog found for this course. Refresh "
                      "the catalog from the CanvasExpert web UI, then try again."),
        }

    records = scope["records"]
    # Distinguish "never successfully cataloged" (state=unavailable, no
    # last_success_at) from "cataloged but has no modules" (state=current/stale
    # with empty records), since both produce an empty modules list.
    never_cataloged = (
        scope["state"] == "unavailable"
        and not scope.get("last_success_at")
    )
    modules_state_detail = "never_cataloged" if never_cataloged else "cataloged"
    columns = _MODULE_COLUMNS
    if include_items:
        columns = columns + ("items",)

    modules = []
    for module in records:
        row = {
            "id": module.get("id"),
            "name": module.get("name", ""),
            "position": module.get("position"),
            "item_count": len(module.get("items") or []),
        }
        if include_items:
            items = [
                {
                    "id": item.get("id"),
                    "type": item.get("type", ""),
                    "title": item.get("title", ""),
                    "position": item.get("position"),
                    "content_id": item.get("content_id", ""),
                }
                for item in (module.get("items") or [])
            ]
            row["items"] = _tabulate(items, _MODULE_ITEM_COLUMNS)
        modules.append(row)

    result = {
        "ok": True,
        "course_id": str((read_result.get("catalog") or {}).get("course_id") or course_id),
        "course_name": str((read_result.get("catalog") or {}).get("course_name") or ""),
        "modules": _tabulate(modules, columns),
        "source": scope["source"],
        "synced_at": scope["last_success_at"],
        "state": scope["state"],
        "modules_state_detail": modules_state_detail,
    }
    if scope["state"] == "stale":
        result["stale_note"] = (
            "Refresh this course's Course Catalog from the CanvasExpert web UI. "
            "refresh_mirror only refreshes CanvasMirror roster, assignments, and submissions."
        )
    return result


def get_course_pages(course_id: str, full_text: bool = False) -> dict:
    """Published page projection from the local v3 Catalog for the Current course.

    The refresh route is the only Canvas acquisition path. This read never
    refreshes, falls back to Canvas, or exposes the workspace path.
    """
    identity_error = _saved_course_gate_check(course_id)
    if identity_error:
        return {"ok": False, "error": identity_error}
    gate_error = _course_gate_check(course_id)
    if gate_error:
        return {"ok": False, "error": gate_error}
    read_result = read_catalog(course_id)
    scope = read_service.catalog_pages(
        course_id, catalog_reader=lambda _course_id: read_result,
    )
    if scope["source"] == "none":
        return {
            "ok": False,
            "error": ("No local course catalog found for this course. Refresh "
                      "the catalog from the CanvasExpert web UI, then try again."),
        }
    body_chars = 0 if full_text else _DESCRIPTION_PREVIEW_CHARS
    pages = [{
        "id": page.get("id"),
        "title": page.get("title", ""),
        "body_text": _truncate_text(page.get("body_text", ""), body_chars),
        "published": page.get("published") is True,
        "front_page": page.get("front_page") is True,
        "updated_at": page.get("updated_at", ""),
    } for page in scope["records"] if page.get("published") is True]
    result = {
        "ok": True,
        "course_id": str((read_result.get("catalog") or {}).get("course_id") or course_id),
        "course_name": str((read_result.get("catalog") or {}).get("course_name") or ""),
        "pages": _tabulate(pages, _PAGE_COLUMNS),
        "source": scope["source"],
        "synced_at": scope["last_success_at"],
        "state": scope["state"],
    }
    if scope["state"] == "stale":
        result["stale_note"] = (
            "Refresh this course's Course Catalog from the CanvasExpert web UI. "
            "refresh_mirror only refreshes CanvasMirror roster, assignments, and submissions."
        )
    return result


def preview_learning_objective(course_id: str, objective: str,
                               effective_start: str, effective_end: str,
                               source_refs: list, replaces: str = None) -> dict:
    """Build the exact reviewed objective preview; never writes or calls Canvas."""
    gate_error = _course_gate_check(course_id)
    if gate_error:
        return {"ok": False, "error": gate_error}
    try:
        read_result = read_catalog(course_id)
        catalog = read_result.get("catalog") if isinstance(read_result, dict) else None
        if not isinstance(catalog, dict):
            raise ValueError("No local course catalog found for this course. Refresh the catalog first.")
        document = learning_objectives.read_document()
        result = learning_objectives.build_preview(
            course_id=str(course_id), catalog=catalog, document=document,
            objective=objective, effective_start=effective_start,
            effective_end=effective_end, source_refs=source_refs, replaces=replaces,
        )
        return _with_next("preview_learning_objective", {"ok": True, **result})
    except (OSError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error)}


def apply_learning_objective(course_id: str, preview: dict,
                             preview_digest: str, expected_revision: int) -> dict:
    """Apply only the exact current preview after all concurrency checks."""
    gate_error = _course_gate_check(course_id)
    if gate_error:
        return {"ok": False, "error": gate_error}
    try:
        read_result = read_catalog(course_id)
        catalog = read_result.get("catalog") if isinstance(read_result, dict) else None
        if not isinstance(catalog, dict):
            raise ValueError("No local course catalog found for this course. Refresh the catalog first.")
        document = learning_objectives.read_document()
        written = learning_objectives.apply_preview(
            course_id=str(course_id), preview=preview,
            preview_digest_value=preview_digest,
            expected_revision=expected_revision, catalog=catalog,
            document=document,
        )
        return {"ok": True, "revision": written["revision"],
                "course_id": str(course_id)}
    except (OSError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error)}


def list_learning_objectives(course_id: str) -> dict:
    """List the reviewed objectives for a Current course without student data."""
    gate_error = _course_gate_check(course_id)
    if gate_error:
        return {"ok": False, "error": gate_error}
    try:
        document = learning_objectives.read_document()
        rows = []
        for entry in document["objectives"].get(str(course_id), []):
            rows.append([
                entry["id"], entry["objective"], entry["effective_start"],
                entry["effective_end"], [ref["title"] for ref in entry["source_refs"]],
                entry["authored_at"],
            ])
        return {"ok": True, "course_id": str(course_id),
                "revision": document["revision"],
                "objectives": {"columns": [
                    "id", "objective", "effective_start", "effective_end",
                    "source_titles", "authored_at",
                ], "rows": rows}}
    except (OSError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error)}


def delete_learning_objective(course_id: str, entry_id: str, expected_revision: int) -> dict:
    """Delete one reviewed objective with an explicit revision guard."""
    gate_error = _course_gate_check(course_id)
    if gate_error:
        return {"ok": False, "error": gate_error}
    try:
        written = learning_objectives.delete_entry(
            course_id=str(course_id), entry_id=entry_id,
            expected_revision=expected_revision,
        )
        return {"ok": True, "course_id": str(course_id), "revision": written["revision"]}
    except (OSError, TypeError, ValueError) as error:
        return {"ok": False, "error": str(error)}


_CONTRACT_FILES = {
    "quiz": "Author a Quiz (QuizForge).txt",
    "assignment": "Author an Assignment (AssignmentForge).txt",
    "page": "Author a Page (PageForge).txt",
    "rubric": "Author a Rubric (RubricForge).txt",
    "schedule": "Author a Class Schedule.txt",
    "academic_calendar": "Author an Academic Calendar.txt",
    "learning_objective": "Author a Learning Objective.txt",
}
_DIRECT_WRITE_CONTRACT_KINDS = frozenset(
    {"schedule", "academic_calendar", "learning_objective"})
_STAGED_CONTRACT_KINDS = ("quiz", "assignment", "page", "rubric")

# Product knowledge the tool surface does not imply. An assistant that only
# sees the tool list cannot tell that Writing Timeline exists, or that every
# writing assignment is tracked or not tracked — so it guesses, or worse,
# tells the teacher a feature they use every week isn't real. These are the
# same canonical files the web UI hands out for pasting into a chat-only
# assistant, so connected and pasted assistants read one text, not two.
_TOOL_GROUPS = {
    # Appendix B has no named surface for discovery and catalog reads. This is
    # the one deliberately plain exception to its teacher-facing vocabulary.
    # refresh_mirror advances the same saved-course read layer.
    "Course discovery and catalog": (
        "list_courses",
        "get_course_assignments",
        "get_modules",
        "get_course_pages",
        "list_sections",
        "list_groups",
        "refresh_mirror",
    ),
    "Create and Forge": (
        # The product guide selects the workflow; the contract and staged list
        # are the two authoring-specific artifacts that workflow reaches, and
        # the push pair is where a staged draft becomes real Canvas content.
        "get_product_guide",
        "get_authoring_contract",
        "stage_content",
        "list_staged_content",
        "preview_content_push",
        "preview_differentiated_quiz_push",
        "apply_content_push",
        "push_content_live",
        # Publish/re-date an assignment that already exists, addressed by
        # Canvas assignment_id -- a sibling write path, not staged content.
        "preview_assignment_update",
        "apply_assignment_update",
    ),
    "Scoring Sessions": (
        "start_scoring_session",
        "continue_scoring_session",
        "list_scoring_sessions",
        "get_scoring_packet",
        "submit_scoring_results",
    ),
    "Gradebook": ("get_gradebook_snapshot",),
    "SIS Grade Bridges": (
        "list_sis_grade_bridges",
        "preview_sis_grade_bridge",
        "apply_sis_grade_bridge",
    ),
    "Learning Objectives": (
        "list_learning_objectives",
        "preview_learning_objective",
        "apply_learning_objective",
        "delete_learning_objective",
    ),
    "School Calendar": (
        # Reads only. The calendar and bell write pairs were retired with the
        # classroom display they were built to feed; those edits live in the
        # web UI, where a teacher can see a calendar while changing it.
        "get_school_calendar",
        "get_bell_schedule",
        "get_day_schedule",
        "get_teacher_schedule",
    ),
    # Mirror submissions are the evidence exposed by the Writing Timeline job.
    "Writing Timeline": ("get_submissions",),
    "Writing Record": ("get_writing_history",),
    "Students": (
        "get_roster",
        "get_roster_student_settings",
        "preview_roster_student_change",
        "apply_roster_student_change",
        "clear_roster_student_field",
    ),
    "Assessments and DataForge": (
        "get_standards_profile",
        "get_assessment_context",
        "get_assessment_grouping_proposal",
    ),
}


def _validated_tool_groups(tool_names=None) -> dict[str, tuple[str, ...]]:
    expected = set(tool_names) if tool_names is not None else {
        item["name"] for item in contract.load_contract()["tools"]
    }
    grouped = [name for names in _TOOL_GROUPS.values() for name in names]
    duplicates = sorted({name for name in grouped if grouped.count(name) > 1})
    missing = sorted(expected - set(grouped))
    unknown = sorted(set(grouped) - expected)
    empty = sorted(group for group, names in _TOOL_GROUPS.items() if not names)
    if duplicates or missing or unknown or empty:
        raise ValueError(
            "tool inventory grouping mismatch: "
            f"duplicates={duplicates}, missing={missing}, unknown={unknown}, empty={empty}"
        )
    return _TOOL_GROUPS


def _build_tool_inventory() -> str:
    schema = contract.load_contract()
    groups = _validated_tool_groups(item["name"] for item in schema["tools"])
    lines = [
        "CanvasExpert MCP tools by job",
        "",
        f"{len(schema['tools'])} tools in schema version {schema['schema_version']}.",
    ]
    for group, names in groups.items():
        lines.extend(("", f"## {group}", ", ".join(f"`{name}`" for name in names)))
    return "\n".join(lines)


# Each topic declares exactly one source: a canonical file or a generated
# result. Summaries form the compact annotated table of contents returned with
# every guide response; declaration order is the public topic order.
_GUIDE_FILES = {
    "overview": {"file": "START HERE - CanvasAgent.txt",
                 "summary": "Product surfaces and capabilities (Appendix B)."},
    "setup": {"file": "START HERE - CanvasAgent.txt",
              "summary": "Local setup and first-run expectations (Appendix A)."},
    "chat_authoring": {"file": "START HERE - CanvasAgent.txt",
                       "summary": "Chat-only authoring and Forge contracts (Appendix C)."},
    "connected": {"file": "START HERE - CanvasAgent.txt",
                  "summary": "Connected MCP workflows and write boundaries (Appendix D)."},
    "privacy": {"file": "START HERE - CanvasAgent.txt",
                "summary": "Pseudonymization and external-AI boundaries (Appendix E)."},
    "troubleshooting": {"file": "START HERE - CanvasAgent.txt",
                        "summary": "Connection and workflow troubleshooting (Appendix F)."},
    "assessments": {"file": "START HERE - CanvasAgent.txt",
                    "summary": "Assessment and grouping guidance (Appendix G)."},
    "full": {"file": "START HERE - CanvasAgent.txt",
             "summary": "Complete CanvasAgent guide, Appendices A through G."},
    "writing_timeline": {"file": "Writing Timeline (tracked assignments).txt",
                         "summary": "Tracked-assignment timeline behavior and coverage."},
    "writing_record": {"file": "Writing Record (longitudinal writing history).txt",
                       "summary": "Longitudinal writing evidence and current limits."},
    "tools": {"generated": _build_tool_inventory,
              "summary": "All MCP tools grouped by teacher-facing job."},
}
_DEFAULT_GUIDE_TOPIC = "overview"
_CANVAS_AGENT_APPENDIXES = {
    "setup": "A",
    "overview": "B",
    "chat_authoring": "C",
    "connected": "D",
    "privacy": "E",
    "troubleshooting": "F",
    "assessments": "G",
}
_CANVAS_AGENT_APPENDIX_ERROR = (
    "CanvasAgent guide unavailable: expected Appendix A through G exactly once "
    "and in order."
)


def _read_authoring_doc(filename: str, label: str) -> tuple[str | None, str | None]:
    """Read one file from ``api/default_docs/AI Authoring/`` verbatim — the same
    on-disk source ``/api/download-contract`` serves, so each document lives in
    exactly one place. Returns ``(text, None)`` or ``(None, error)``."""
    path = os.path.join(REPO_ROOT, "api", "default_docs", "AI Authoring", filename)
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(), None
    except OSError as error:
        return None, f"Could not read the {label}: {error}"


def _read_canvasagent_topic(topic: str, guide_text: str) -> tuple[str | None, str | None]:
    """Return one exact Appendix slice from the canonical CanvasAgent file."""
    matches = list(re.finditer(r"(?m)^Appendix ([A-Z])\..*$", guide_text))
    letters = [match.group(1) for match in matches]
    if letters != list("ABCDEFG"):
        return None, _CANVAS_AGENT_APPENDIX_ERROR
    if topic == "full":
        return guide_text, None

    appendix = _CANVAS_AGENT_APPENDIXES.get(topic)
    if appendix is None:
        return None, f"unknown CanvasAgent topic '{topic}'"
    index = ord(appendix) - ord("A")
    start = matches[index].start()
    end = matches[index + 1].start() if index + 1 < len(matches) else len(guide_text)
    return guide_text[start:end].strip(), None


def _staging_appendix(kind: str) -> str:
    """A short "how to stage this for the teacher" section appended to the
    contract an assistant pulls. Kept here rather than in the shared
    ``api/default_docs/AI Authoring/`` files so the web UI's own
    download-contract stays the pure envelope format, while an MCP assistant
    that authors a draft learns where to drop it and how to mark it complete.
    The detailed steps ride this response, so they cost context only when
    authoring."""
    folder = runtime_paths.inbox_folder(kind)
    where = str(folder) if folder else (
        f"the {kind.capitalize()} Inbox folder in the Canvas Expert workspace")
    return (
        "\n\n---\n\n"
        "## Getting this to the teacher\n\n"
        "Every route stages the draft, so there is always a file the teacher "
        "can read afterwards. What differs is where it stops.\n\n"
        "**If the teacher asked for this in Canvas, put it there.** Call "
        "push_content_live with this kind, a short label, the completed "
        "envelope, and their course_id. It stages the draft and creates the "
        "Canvas object in one call. Their request is the authorization: do "
        "not stage it instead and ask, and do not ask them to confirm a "
        "preview they did not ask for. Drafts land unpublished unless they "
        "asked for published=true, so what you create is visible to them and "
        "not yet to students -- they can edit or delete it by hand in Canvas "
        "and tell you what to change.\n\n"
        "**If they asked you to prepare it for their review**, call "
        "stage_content with the same kind, label, and envelope, and tell them "
        "it is staged. It appears in the matching Canvas Expert push tab under "
        "\"Staged by your assistant (pending review)\" for them to validate "
        "and push themselves.\n\n"
        "**For due, unlock or lock dates**, stage it, then walk the pair: "
        "preview_content_push with this kind and the draft's label carries "
        "the dates, and apply_content_push with the three coordinates "
        "unchanged lands it.\n\n"
        f"The Inbox for this kind is `{where}`. You do not need to write there "
        "yourself; stage_content handles the envelope and the byte-count marker "
        "that keeps a half-synced draft from being picked up.\n"
    )


def get_authoring_contract(kind: str) -> dict:
    """Return one canonical Forge authoring contract.

    Contracts come from ``api/default_docs/AI Authoring/``. The Forge kinds
    receive the staging appendix; the kinds in
    ``_DIRECT_WRITE_CONTRACT_KINDS`` have no review queue and are returned
    verbatim. No course_id, student data, vault, or safety gate applies.
    """
    filename = _CONTRACT_FILES.get(kind)
    if filename is None:
        return {
            "ok": False,
            "error": (f"unknown kind '{kind}'; expected one of: "
                      f"{', '.join(_CONTRACT_FILES)}"),
        }

    contract_text, error = _read_authoring_doc(filename, f"{kind} authoring contract")
    if error:
        return {"ok": False, "error": error}

    # These kinds have no staging/review queue. They write directly to the
    # teacher's local workspace, or through the Calendar preview/apply pair.
    if kind in _DIRECT_WRITE_CONTRACT_KINDS:
        return {"ok": True, "kind": kind, "contract": contract_text}

    return {"ok": True, "kind": kind,
            "contract": contract_text + _staging_appendix(kind)}


def get_product_guide(topic: str = "") -> dict:
    """Return a canonical CanvasAgent appendix or one standalone guide file.

    The connected tool and the web UI download route share one source file for
    every topic. Appendix topics are extracted from that file so a correction
    cannot make the connected and pasted guidance disagree."""
    requested = str(topic or "").strip().lower() or _DEFAULT_GUIDE_TOPIC
    source = _GUIDE_FILES.get(requested)
    if source is None:
        return {
            "ok": False,
            "error": (f"unknown topic '{requested}'; expected one of: "
                      f"{', '.join(_GUIDE_FILES)} (or omit for "
                      f"{_DEFAULT_GUIDE_TOPIC})"),
        }

    if "generated" in source:
        try:
            guide_text = source["generated"]()
        except ValueError as error:
            return {"ok": False, "error": str(error)}
    else:
        filename = source["file"]
        guide_text, error = _read_authoring_doc(filename, f"{requested} guide")
        if error:
            return {"ok": False, "error": error}
        if filename == _GUIDE_FILES["full"]["file"]:
            guide_text, error = _read_canvasagent_topic(requested, guide_text)
            if error:
                return {"ok": False, "error": error}

    return {"ok": True, "topic": requested,
            "topics": {name: item["summary"] for name, item in _GUIDE_FILES.items()},
            "guide": guide_text}


def _read_published_profile() -> tuple[dict | None, str, str]:
    """Read the published profile without returning any private path."""
    root = workspace.for_ai_root()
    if not root:
        return None, "missing", (
            "DataForge standards profile unavailable: workspace is not configured."
        )
    path = os.path.join(root, "DataForge", profile_export.PROFILE_FILENAME)
    try:
        with open(path, encoding="utf-8") as handle:
            profile = json.load(handle)
    except FileNotFoundError:
        return None, "missing", (
            "DataForge standards profile unavailable: generate it locally first."
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None, "malformed", (
            "DataForge standards profile unavailable: the published artifact is malformed."
        )
    if not isinstance(profile, dict) or profile.get("format") != profile_export.FORMAT:
        return None, "unsupported", (
            "DataForge standards profile unavailable: unsupported artifact format."
        )
    return profile, "valid", ""


def get_standards_profile() -> dict:
    """Return the published local DataForge profile after the safety gate.

    This is deliberately not course-scoped: the profile is an offline artifact
    assembled from the teacher's local history. It remains student data, so it
    still needs the identity vault and outbound safety scan before it leaves the
    process. Errors are intentionally generic so a private path or leak value
    cannot be reflected to an MCP client.
    """
    profile, _, error = _read_published_profile()
    if error:
        return {"ok": False, "error": error}

    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}
    verdict = feedback_safety.scan_payload(profile, vault)
    if not verdict.get("green"):
        return {"ok": False, "error":
                "DataForge standards profile withheld: the outbound safety gate is not green."}
    return {"ok": True, "profile": profile}


def _assessment_context_empty_students() -> dict:
    return _tabulate([], _ASSESSMENT_CONTEXT_COLUMNS)


def _assessment_context_attention(course_id: str, state: str, reason: str) -> dict:
    return {
        "ok": False,
        "course_id": str(course_id),
        "error": "Current roster data is unavailable or inconsistent; student data is withheld.",
        "attention": {"action": "refresh_mirror", "reason": reason},
        "source": {"current_roster": {
            "source": "local_mirror", "scope": "current_course", "state": state,
        }},
        "students": _assessment_context_empty_students(),
    }


def _assessment_context_profile_error(state: str, error: str) -> dict:
    return {
        "ok": False,
        "error": error,
        "profile_state": state,
        "students": _assessment_context_empty_students(),
    }


def _assessment_context_roster(course_id: str, vault):
    """Return a current, structurally consistent pseudonymized roster."""
    if not _cache_safe():
        return None, _assessment_context_attention(
            course_id, "unavailable", "the local mirror read seam is unavailable"
        )
    try:
        scope = read_service.private_roster(
            course_id, max_age_hours=mirror_queries._serve_max_age_hours()
        )
        document = mirror_store.read_roster(course_id)
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        return None, _assessment_context_attention(
            course_id, "malformed", "the local mirror roster is malformed"
        )

    if not isinstance(scope, dict) or scope.get("state") != "current":
        state = scope.get("state", "unavailable") if isinstance(scope, dict) else "malformed"
        return None, _assessment_context_attention(
            course_id, str(state), "refresh the current course mirror before retrying"
        )
    if not isinstance(document, dict) or document.get("state") != "current":
        return None, _assessment_context_attention(
            course_id, "malformed", "the local mirror roster is internally inconsistent"
        )

    records = scope.get("records")
    raw_students = document.get("students")
    sections = document.get("sections")
    record_ids = {
        str(record.get("id")) for record in records or [] if isinstance(record, dict)
    }
    if (not isinstance(records, list) or not isinstance(raw_students, dict)
            or any(not isinstance(value, dict) for value in raw_students.values())
            or not isinstance(sections, dict)
            or any(not isinstance(section_id, str) or not isinstance(name, str)
                   for section_id, name in sections.items())
            or str(scope.get("course_id")) != str(course_id)
            or str(document.get("course_id")) != str(course_id)
            or scope.get("last_success_at") != document.get("last_success_at")
            or record_ids != {str(key) for key in raw_students}):
        return None, _assessment_context_attention(
            course_id, "malformed", "the local mirror roster is internally inconsistent"
        )
    if any(not isinstance(record, dict) or record.get("id") in (None, "") for record in records):
        return None, _assessment_context_attention(
            course_id, "malformed", "the local mirror roster is malformed"
        )
    ids = [str(record["id"]) for record in records]
    if len(ids) != len(set(ids)):
        return None, _assessment_context_attention(
            course_id, "malformed", "the local mirror roster contains duplicate students"
        )

    try:
        with _vault_transaction(vault):
            roster_service.upsert_roster(vault, records)
            projected = pseudonym.pseudonymize_roster(
                vault, records, sections
            )
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        return None, _assessment_context_attention(
            course_id, "malformed", "the current roster could not be projected safely"
        )

    if (not isinstance(projected, list)
            or any(not isinstance(row, dict) for row in projected)):
        return None, _assessment_context_attention(
            course_id, "malformed", "the current roster projection is malformed"
        )

    folded = {}
    for row in projected:
        key = str(row.get("pseudonym") or "").strip()
        if not key or key.casefold() in folded:
            return None, _assessment_context_attention(
                course_id, "malformed", "the current roster has a pseudonym collision"
            )
        folded[key.casefold()] = key
    try:
        pseudonym_by_id = {
            str(entry.get("canvas_id")): str(entry.get("pseudonym"))
            for entry in vault.entries()
            if isinstance(entry, dict)
            and str(entry.get("canvas_id") or "").strip()
            and str(entry.get("pseudonym") or "").strip()
        }
    except (AttributeError, TypeError, ValueError):
        return None, _assessment_context_attention(
            course_id, "malformed", "the current roster identity projection is malformed"
        )
    if any(str(record["id"]) not in pseudonym_by_id for record in records):
        return None, _assessment_context_attention(
            course_id, "malformed", "the current roster identity projection is incomplete"
        )
    return {
        "rows": sorted(projected, key=lambda row: row["pseudonym"]),
        "by_folded": folded,
        "synced_at": str(scope.get("last_success_at") or ""),
        # Internal-only values reused by the grouping projection. They never
        # cross the MCP boundary and keep the mirror/pseudonym join in one
        # source rather than reconstructing it in the wrapper.
        "records": records,
        "pseudonym_by_id": pseudonym_by_id,
    }, None


def _assessment_profile_shape(profile: dict):
    """Validate the bounded shape consumed by assessment context."""
    def _finite_percentage(value) -> bool:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and 0 <= value <= 100)

    if profile.get("grain") != "learning_standard":
        return None, "unsupported", "DataForge standards profile has an unsupported grain."
    if (not isinstance(profile.get("generated"), str)
            or not profile.get("generated")
            or not isinstance(profile.get("snapshots_used"), int)
            or isinstance(profile.get("snapshots_used"), bool)
            or profile["snapshots_used"] < 0
            or not isinstance(profile.get("snapshots_without_standard_list"), int)
            or isinstance(profile.get("snapshots_without_standard_list"), bool)
            or not isinstance(profile.get("student_count"), int)
            or isinstance(profile.get("student_count"), bool)
            or profile["student_count"] < 0
            or not isinstance(profile.get("students"), dict)
            or profile["student_count"] != len(profile["students"])
    ):
        return None, "malformed", "DataForge standards profile is malformed."

    students = {}
    for pseudonym_value, summary in profile["students"].items():
        if (not isinstance(pseudonym_value, str) or not pseudonym_value.strip()
                or pseudonym_value != pseudonym_value.strip()
                or pseudonym_value.casefold() in students):
            return None, "malformed", "DataForge standards profile has a pseudonym collision."
        if not isinstance(summary, dict):
            return None, "malformed", "DataForge standards profile is malformed."
        assessments = summary.get("assessments")
        latest_pct = summary.get("latest_pct")
        if (not isinstance(assessments, int) or isinstance(assessments, bool)
                or assessments < 0
                or (latest_pct is not None and not _finite_percentage(latest_pct))
                or not isinstance(summary.get("latest_date"), str)
                or not isinstance(summary.get("weak_standards"), list)
                or any(not isinstance(code, str) or not code for code in summary["weak_standards"])
                or len(summary["weak_standards"]) > MAX_ASSESSMENT_CONTEXT_STANDARDS
                or len({code.casefold() for code in summary["weak_standards"]})
                   != len(summary["weak_standards"])
                or not isinstance(summary.get("standards"), dict)):
            return None, "malformed", "DataForge standards profile is malformed."
        standards = {}
        for code, entry in summary["standards"].items():
            if not isinstance(code, str) or not code or not isinstance(entry, dict):
                return None, "malformed", "DataForge standards profile is malformed."
            attempts = entry.get("attempts")
            assessed_in = entry.get("assessed_in")
            if (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0
                    or not _finite_percentage(entry.get("mean"))
                    or (entry.get("latest") is not None
                        and not _finite_percentage(entry.get("latest")))
                    or not isinstance(entry.get("latest_date"), str)
                    or not isinstance(assessed_in, list)
                    or any(not isinstance(label, str) for label in assessed_in)
                    or not isinstance(entry.get("weak"), bool)):
                return None, "malformed", "DataForge standards profile is malformed."
            standards[code] = entry
        if any(code not in standards for code in summary["weak_standards"]):
            return None, "malformed", "DataForge standards profile is malformed."
        students[pseudonym_value.casefold()] = {
            "pseudonym": pseudonym_value, "summary": summary, "standards": standards,
        }
    return students, None, None


def _assessment_context_profile(vault):
    profile, state, error = _read_published_profile()
    if error:
        return None, _assessment_context_profile_error(state, error)
    verdict = feedback_safety.scan_payload(profile, vault)
    if not verdict.get("green"):
        return None, _assessment_context_profile_error(
            "unsafe", "DataForge standards profile withheld: the outbound safety gate is not green."
        )
    students, shape_state, shape_error = _assessment_profile_shape(profile)
    if shape_error:
        return None, _assessment_context_profile_error(shape_state, shape_error)
    return {"profile": profile, "students": students}, None


def _assessment_context_limit(kind: str, maximum: int, actual: int) -> dict:
    return {
        "ok": False,
        "error": "Assessment context exceeds a deterministic evidence limit; no rows were returned.",
        "limit": {"kind": kind, "maximum": maximum, "actual": actual},
        "students": _assessment_context_empty_students(),
    }


def get_assessment_context(course_id: str, pseudonyms: str = "") -> dict:
    """Bounded, read-only current-roster join with local longitudinal history."""
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}
    if not isinstance(pseudonyms, str):
        return {"ok": False, "error": "pseudonyms must be a comma-separated string."}

    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}
    roster, roster_error = _assessment_context_roster(course_id, vault)
    if roster_error:
        return roster_error
    profile_data, profile_error = _assessment_context_profile(vault)
    if profile_error:
        return profile_error

    requested = {token.strip().casefold() for token in pseudonyms.split(",") if token.strip()}
    current_rows = roster["rows"]
    current_by_folded = roster["by_folded"]
    unknown_requested = requested - set(current_by_folded)
    selected = [
        row for row in current_rows
        if not requested or row["pseudonym"].casefold() in requested
    ]
    if len(selected) > MAX_ASSESSMENT_CONTEXT_STUDENTS:
        return _assessment_context_limit(
            "students", MAX_ASSESSMENT_CONTEXT_STUDENTS, len(selected)
        )
    profile_students = profile_data["students"]
    profile_only_count = sum(
        1 for key in profile_students if key not in current_by_folded
    )

    rows = []
    with_history = 0
    for roster_row in selected:
        matched = profile_students.get(roster_row["pseudonym"].casefold())
        summary = matched["summary"] if matched else None
        if summary and summary["assessments"] > 0:
            with_history += 1
        standards = []
        if matched:
            if len(matched["standards"]) > MAX_ASSESSMENT_CONTEXT_STANDARDS:
                return _assessment_context_limit(
                    "standards", MAX_ASSESSMENT_CONTEXT_STANDARDS,
                    len(matched["standards"]),
                )
            for code in sorted(matched["standards"]):
                entry = matched["standards"][code]
                if len(entry["assessed_in"]) > MAX_ASSESSMENT_CONTEXT_ASSESSED_IN:
                    return _assessment_context_limit(
                        "assessed_in", MAX_ASSESSMENT_CONTEXT_ASSESSED_IN,
                        len(entry["assessed_in"]),
                    )
                standards.append({
                    "code": code,
                    "attempts": entry["attempts"],
                    "mean": entry["mean"],
                    "latest": entry["latest"],
                    "latest_date": entry["latest_date"],
                    "assessed_in": list(entry["assessed_in"]),
                    "weak": entry["weak"],
                })
        rows.append({
            "pseudonym": roster_row["pseudonym"],
            "assessment_count": summary["assessments"] if summary else 0,
            "latest_assessment_date": summary["latest_date"] if summary else "",
            "latest_percentage": summary["latest_pct"] if summary else None,
            "weak_standard_codes": list(summary["weak_standards"]) if summary else [],
            "standards": standards,
        })

    payload = {
        "course_id": str(course_id),
        "source": {
            "current_roster": {
                "source": "local_mirror", "scope": "current_course", "state": "current",
                "synced_at": roster["synced_at"],
            },
            "assessment_history": {
                "source": "local_longitudinal_history",
                "scope": "local_longitudinal_history",
                "generated": profile_data["profile"]["generated"],
                "grain": profile_data["profile"]["grain"],
                "snapshots_used": profile_data["profile"]["snapshots_used"],
            },
        },
        "coverage": {
            "current_roster_students_with_history": with_history,
            "current_roster_students_without_history": len(selected) - with_history,
            "requested_pseudonyms_not_in_current_roster": len(unknown_requested),
            "published_profile_students_not_in_current_roster": profile_only_count,
        },
        "students": rows,
    }
    result = pseudonym.gate(payload, vault)
    if result.get("ok"):
        result["students"] = _tabulate(result["students"], _ASSESSMENT_CONTEXT_COLUMNS)
    return result


def _assessment_grouping_empty_proposal() -> dict:
    return {
        "groups": [],
        "placements": _tabulate([], _ASSESSMENT_GROUPING_PLACEMENT_COLUMNS),
    }


def _assessment_grouping_error(message: str, *, attention: dict | None = None) -> dict:
    result = {
        "ok": False,
        "error": message,
        "proposal": _assessment_grouping_empty_proposal(),
    }
    if attention is not None:
        result["attention"] = attention
    return result


def _assessment_grouping_group_set(document: dict, label: str):
    """Resolve the assistant-facing group-set label without exposing its ID."""
    requested = label.strip().casefold()
    matches = [
        category for category in document.get("categories", [])
        if isinstance(category, dict)
        and isinstance(category.get("category_name"), str)
        and category["category_name"].strip().casefold() == requested
    ]
    if not matches:
        return None, _assessment_grouping_error(
            "No current local Canvas group set matches group_set_label."
        )
    if len(matches) > 1:
        return None, _assessment_grouping_error(
            "group_set_label matches more than one current local Canvas group set."
        )
    return matches[0], None


def _assessment_grouping_snapshot(paths, snapshot_id: str):
    try:
        snapshots = history_store.list_snapshots(paths)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None, _assessment_grouping_error(
            "The local assessment snapshot is unavailable."
        )
    snapshot = next(
        (
            candidate for candidate in snapshots
            if isinstance(candidate, dict)
            and str(candidate.get("id") or "").strip() == snapshot_id
        ),
        None,
    )
    if snapshot is None:
        return None, _assessment_grouping_error(
            "No local assessment snapshot matches snapshot_id."
        )
    students = snapshot.get("students")
    if not isinstance(students, list):
        return None, _assessment_grouping_error(
            "The selected local assessment snapshot is malformed."
        )
    seen = set()
    for student in students:
        if not isinstance(student, dict):
            return None, _assessment_grouping_error(
                "The selected local assessment snapshot is malformed."
            )
        pseudonym_value = str(student.get("n") or "").strip()
        if not pseudonym_value or pseudonym_value.casefold() in seen:
            return None, _assessment_grouping_error(
                "The selected local assessment snapshot has a pseudonym collision."
            )
        seen.add(pseudonym_value.casefold())
    return snapshot, None


def _assessment_grouping_source_error(message: str, state: str = "unavailable") -> dict:
    return _assessment_grouping_error(
        message,
        attention={"action": "refresh_mirror", "reason": message},
    )


def get_assessment_grouping_proposal(
    course_id: str,
    snapshot_id: str,
    method: str = "overall_pct",
    cutoffs: str = "",
    no_data_group: str = "",
    group_set_label: str = "",
) -> dict:
    """Return the Students-page grouping proposal as a safe read projection."""
    err = _course_gate_check(course_id)
    if err:
        return _assessment_grouping_error(err)
    if not all(isinstance(value, str) for value in (
        course_id, snapshot_id, method, cutoffs, no_data_group, group_set_label
    )):
        return _assessment_grouping_error(
            "Assessment grouping arguments must be strings."
        )
    snapshot_id = snapshot_id.strip()
    group_set_label = group_set_label.strip()
    if not snapshot_id:
        return _assessment_grouping_error("snapshot_id is required.")
    if not group_set_label:
        return _assessment_grouping_error("group_set_label is required.")

    vault, vault_err = _open_vault()
    if vault_err:
        return _assessment_grouping_error(vault_err)
    roster, roster_error = _assessment_context_roster(course_id, vault)
    if roster_error:
        return _assessment_grouping_error(
            roster_error.get(
                "error",
                "Current roster data is unavailable or inconsistent; student data is withheld.",
            ),
            attention=roster_error.get("attention"),
        )
    if len(roster["records"]) > MAX_ASSESSMENT_GROUPING_STUDENTS:
        return _assessment_grouping_error(
            "Assessment grouping exceeds the deterministic student limit; no proposal rows were returned.",
        ) | {"limit": {
            "kind": "students",
            "maximum": MAX_ASSESSMENT_GROUPING_STUDENTS,
            "actual": len(roster["records"]),
        }}

    try:
        groups_document = mirror_store.read_groups(course_id)
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        groups_document = None
    if not isinstance(groups_document, dict) or groups_document.get("state") != "current":
        state = groups_document.get("state", "unavailable") if isinstance(groups_document, dict) else "malformed"
        return _assessment_grouping_source_error(
            "A current local group mirror is required for assessment grouping.", state
        )
    category, category_error = _assessment_grouping_group_set(
        groups_document, group_set_label
    )
    if category_error:
        return category_error

    try:
        paths = dataforge_paths.get_paths()
        snapshot, snapshot_error = _assessment_grouping_snapshot(paths, snapshot_id)
        if snapshot_error:
            return snapshot_error
        identity = VaultIdentity(vault)
        linked_students = identity.linked_students()
        if not isinstance(linked_students, dict):
            return _assessment_grouping_error(
                "The local assessment identity map is unavailable."
            )
        profile_students = {
            str(student["n"]).strip(): {"latest_pct": student.get("pct")}
            for student in snapshot["students"]
        }
        coverage_report = canvas_join.build_coverage_report(
            profile_students,
            linked_students,
            roster["records"],
        )
        if coverage_report.get("ambiguous_roster_count", 0):
            return _assessment_grouping_error(
                "The local roster has an ambiguous identity join; grouping is withheld."
            )
        private_proposal = grouping.build_grouping_proposal(
            snapshot,
            coverage_report,
            roster["records"],
            category,
            method=method,
            cutoffs=cutoffs or None,
            no_data_group=no_data_group,
        )
    except IdentityMigrationError:
        return _assessment_grouping_error(
            "The local assessment identity map is unavailable."
        )
    except grouping.GroupingValidationError as exc:
        return _assessment_grouping_error(str(exc))
    except (OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError):
        return _assessment_grouping_error(
            "The local assessment grouping sources are malformed or unavailable."
        )

    pseudonym_by_id = roster["pseudonym_by_id"]
    safe_placements = []
    for placement in private_proposal["placements"]:
        pseudonym_value = pseudonym_by_id.get(str(placement.get("canvas_id") or ""))
        if not pseudonym_value:
            return _assessment_grouping_error(
                "The current roster identity projection is incomplete."
            )
        safe_placements.append({
            "pseudonym": pseudonym_value,
            "score": placement.get("score"),
            "status": placement.get("status"),
            "group": placement.get("group"),
        })

    safe_groups = []
    for tier in private_proposal["tiers"]:
        pseudonyms = [
            pseudonym_by_id.get(str(student_id))
            for student_id in tier.get("student_ids", [])
        ]
        if any(not value for value in pseudonyms):
            return _assessment_grouping_error(
                "The current roster identity projection is incomplete."
            )
        safe_groups.append({
            "group_name": tier["name"],
            "count": tier["student_count"],
            "pseudonyms": pseudonyms,
        })

    safe_payload = {
        "proposal": {
            "source": {
                "assessment_snapshot": {
                    "source": "local_longitudinal_history",
                    "scope": "local_longitudinal_history",
                    "snapshot_id": str(private_proposal["snapshot_id"]),
                    "label": str(private_proposal["snapshot_label"]),
                    "date": str(snapshot.get("date") or ""),
                    "grain": str(snapshot.get("breakdown_type") or ""),
                },
                "current_roster": {
                    "source": "local_mirror",
                    "scope": "current_course",
                    "state": "current",
                    "synced_at": roster["synced_at"],
                },
                "group_mirror": {
                    "source": "local_mirror",
                    "scope": "current_course_group_sets",
                    "state": "current",
                    "synced_at": str(groups_document.get("last_success_at") or ""),
                },
            },
            "method": private_proposal["method"],
            "cutoffs": private_proposal["cutoffs"],
            "group_set_label": str(category.get("category_name") or "").strip(),
            "no_data_group": private_proposal["no_data_group"],
            "coverage": {
                "status": "ready",
                "current_roster_count": private_proposal["roster_count"],
                "matched_count": private_proposal["matched_count"],
                "no_data_count": private_proposal["no_data_count"],
                "profile_student_count": coverage_report["profile_student_count"],
                "linked_student_count": coverage_report["linked_student_count"],
                "missing_local_id_count": coverage_report["missing_local_id_count"],
                "not_in_roster_count": coverage_report["not_in_roster_count"],
                "ambiguous_roster_count": coverage_report["ambiguous_roster_count"],
                "roster_student_count": coverage_report["roster_student_count"],
                "roster_only_count": coverage_report["roster_only_count"],
                "coverage_percent": coverage_report["coverage_percent"],
            },
            "proposal_digest": private_proposal["proposal_digest"],
            "groups": safe_groups,
            "placements": safe_placements,
        },
    }
    encoded = json.dumps(safe_payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded) > MAX_ASSESSMENT_GROUPING_RESULT_CHARS:
        return _assessment_grouping_error(
            "Assessment grouping result exceeds the deterministic result limit; no proposal rows were returned."
        ) | {"limit": {
            "kind": "serialized_chars",
            "maximum": MAX_ASSESSMENT_GROUPING_RESULT_CHARS,
            "actual": len(encoded),
        }}
    result = pseudonym.gate(safe_payload, vault)
    if not result.get("ok"):
        blocked = _assessment_grouping_error(
            result.get("error", "Safety scan blocked this result before it left the machine.")
        )
        if result.get("violations"):
            blocked["violations"] = result["violations"]
        return blocked
    result["proposal"]["placements"] = _tabulate(
        result["proposal"]["placements"], _ASSESSMENT_GROUPING_PLACEMENT_COLUMNS
    )
    return result


def list_staged_content(kind: str = "") -> dict:
    """Drafts an assistant has already staged in the per-kind Inbox, so it can
    confirm a drop landed and avoid losing track or duplicating it. Reuses
    ``webui.deps.list_inbox_files`` (the Slice C marker gate) as-is. Pass one
    of quiz/assignment/page/rubric to narrow to that kind, or omit for all
    four. No course_id, no student data — no course gate, no vault, no safety
    gate. Only each draft's label (name) is returned, never its absolute
    path."""
    if kind:
        if kind not in _STAGED_CONTRACT_KINDS:
            return {
                "ok": False,
                "error": (f"unknown kind '{kind}'; expected one of: "
                      f"{', '.join(_STAGED_CONTRACT_KINDS)} (or omit for all)"),
            }
        kinds = [kind]
    else:
        kinds = list(_STAGED_CONTRACT_KINDS)

    rows = [
        {"kind": k, "label": entry["label"]}
        for k in kinds
        for entry in deps.list_inbox_files(k)
    ]
    return {"ok": True, "staged": _tabulate(rows, _STAGED_CONTENT_COLUMNS)}


def preview_content_push(
    course_id: str,
    kind: str,
    label: str,
    published: bool = False,
    module_name: str = "",
    assignment_group_name: str = "",
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
    post_to_sis: bool = False,
) -> dict:
    """Freeze one staged draft (quiz/assignment/page/rubric, by the label
    list_staged_content returns) into a persisted, digest-protected review for
    one Current course, and return what it will create. No Canvas write here:
    Canvas is read only to capture the baseline apply drift-checks against.

    Delivery options are per kind -- a page takes published and module_name, a
    rubric only published, and a quiz or assignment also takes
    assignment_group_name, post_to_sis, and ISO 8601 due_at/unlock_at/lock_at.
    Naming one a kind cannot carry is refused, not dropped. Drafts stay
    unpublished unless published=true.

    Returns operation_id, batch_id, and review_digest (pass all three,
    unchanged, to apply_content_push) plus the frozen preview: the course, the
    title, and whether an object of that name already exists in the course.
    No course_id, student data, vault, or safety gate applies -- authored
    content carries none.
    """
    return _with_next("preview_content_push", content_push.preview_content_push(
        course_id, kind, label,
        published=published, module_name=module_name,
        assignment_group_name=assignment_group_name,
        due_at=due_at, unlock_at=unlock_at, lock_at=lock_at,
        post_to_sis=post_to_sis,
    ))


def preview_differentiated_quiz_push(
    course_id: str,
    variants: list,
    published: bool = False,
    module_name: str = "",
    assignment_group_name: str = "",
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
    post_to_sis: bool = False,
) -> dict:
    """Freeze several staged QuizForge labels for distinct selected groups."""
    return _with_next("preview_differentiated_quiz_push", content_push.preview_differentiated_quiz_push(
        course_id, variants, published=published, module_name=module_name,
        assignment_group_name=assignment_group_name, due_at=due_at,
        unlock_at=unlock_at, lock_at=lock_at, post_to_sis=post_to_sis,
    ))


def apply_content_push(operation_id: str, batch_id: str, review_digest: str) -> dict:
    """Create exactly what preview_content_push or
    preview_differentiated_quiz_push froze in the Canvas course it froze it against.

    Takes only the three opaque coordinates that preview returned, so nothing
    here can reach another draft, course, or kind. Runs the same Operation
    Ledger apply the teacher's own push tab runs -- one claim, a drift check
    against the frozen baseline, per-step checkpoints, and a durable receipt --
    so a draft landed from chat and one landed from the web UI are the same
    write. A draft whose course changed under the frozen review is refused as
    drift rather than overwritten.

    Returns the operation status and, per target, the state and the Canvas URL
    of what was created. Refuses cleanly, with no Canvas call, when the
    coordinates do not match a frozen content review.
    """
    return content_push.apply_content_push(operation_id, batch_id, review_digest)


def preview_assignment_update(
    course_id: str,
    assignment_id: str,
    published: bool = None,
    due_at: str = "",
    unlock_at: str = "",
    lock_at: str = "",
) -> dict:
    """Freeze a publish/date patch against one existing Canvas assignment.

    No draft, no label: assignment_id names the exact Canvas assignment, and
    only its published state and three schedule dates are ever read or
    changed -- description, points, and assignment group stay untouched.
    Canvas is read live to freeze the field diff and the drift anchor;
    never the mirror or Course Catalog. Supplying no field at all is refused
    before anything reaches Canvas.
    """
    gate_error = _course_gate_check(course_id)
    if gate_error:
        return {"ok": False, "error": gate_error}
    return _with_next("preview_assignment_update", content_push.preview_assignment_update(
        course_id, assignment_id, published=published,
        due_at=due_at, unlock_at=unlock_at, lock_at=lock_at,
    ))


def apply_assignment_update(operation_id: str, batch_id: str, review_digest: str) -> dict:
    """Write exactly what preview_assignment_update froze to Canvas.

    Same coordinates contract as apply_content_push: the three opaque values
    preview returned, nothing else. Runs the same Operation Ledger apply --
    one claim, a drift check against the frozen updated_at, and a durable
    receipt. Blocked as drift_detected, not overwritten, if the assignment
    changed in Canvas since the preview.
    """
    return content_push.apply_assignment_update(operation_id, batch_id, review_digest)


def stage_content(kind: str, label: str, content: str) -> dict:
    """Write one authored draft into the teacher's per-kind To Review Inbox.

    The staging step an assistant used to have to perform with file access:
    the envelope and its byte-count marker are written together, so the draft
    appears in the matching push tab for the teacher to review and land. No
    Canvas write, and an existing label is refused rather than overwritten.
    """
    return content_push.stage_content(kind, label, content)


def push_content_live(
    course_id: str,
    kind: str,
    label: str,
    content: str,
    published: bool = False,
    module_name: str = "",
    assignment_group_name: str = "",
    post_to_sis: bool = False,
) -> dict:
    """Stage one authored draft and create it in Canvas in a single call.

    The route for a teacher who asked for the content in Canvas. Their ask is
    the authorization: this is not a gate to route around, it is the gate --
    staging still happens, so the draft remains on disk as the artifact of
    record, and the same baseline capture, frozen review, and drift check run
    internally between staging and applying. What it drops is the round trip
    that asked the teacher to approve a review they never asked to see.

    Dates stay on the preview pair.
    """
    return content_push.push_content_live(
        course_id, kind, label, content,
        published=published, module_name=module_name,
        assignment_group_name=assignment_group_name,
        post_to_sis=post_to_sis,
    )


_MIRROR_UNAVAILABLE_ROSTER_ERROR = (
    "The local CanvasMirror roster for this course is stale or missing. "
    "Canvas Expert withholds it rather than fetching live from Canvas — call "
    "refresh_mirror for this course, then try again."
)
_MIRROR_UNAVAILABLE_SUBMISSIONS_ERROR = (
    "The local CanvasMirror for this course (roster, assignments, or "
    "submissions) is stale or missing. Canvas Expert withholds submissions "
    "rather than fetching live from Canvas — call refresh_mirror for this "
    "course, then try again."
)
_MIRROR_UNAVAILABLE_SNAPSHOT_ERROR = (
    "The local CanvasMirror for this course isn't fresh enough to serve a "
    "whole-course snapshot. Canvas Expert withholds it rather than fetching "
    "live from Canvas — call refresh_mirror for this course, then try again."
)


def get_roster(course_id: str) -> dict:
    """Current roster as a {columns, rows} table of (pseudonym,
    section_names), sorted by pseudonym. Served ONLY from the local
    CanvasMirror — never live Canvas; a stale or missing mirror is refused
    (call refresh_mirror first). Pseudonymized through the identity vault;
    gated by the outbound safety scan before tabulation."""
    identity_error = _saved_course_gate_check(course_id)
    if identity_error:
        return {"ok": False, "error": identity_error}
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}

    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}

    mirror_doc = _mirror_roster_doc(course_id)
    if mirror_doc is None:
        return {"ok": False, "error": _MIRROR_UNAVAILABLE_ROSTER_ERROR}

    with _vault_transaction(vault):
        users = mirror_doc["students"]
        roster_service.upsert_roster(vault, users)
        roster = pseudonym.pseudonymize_roster(vault, users, mirror_doc["sections"])
        result = pseudonym.gate(
            {"roster": roster, "source": "mirror",
             "synced_at": mirror_doc["last_success_at"]}, vault)
    if result.get("ok"):
        result["roster"] = _tabulate(result["roster"], _ROSTER_COLUMNS)
    return result


def get_submissions(course_id: str, assignment_id: str,
                    include_text: bool = True, pseudonyms: str = "",
                    max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS) -> dict:
    """One assignment's submissions, pseudonymized and scrubbed, as
    ``{assignment: {...}, submissions: {columns, rows}}``. Served ONLY from
    the local CanvasMirror — never live Canvas; a stale or missing mirror is
    refused (call refresh_mirror first). ``pseudonyms`` (comma-separated)
    narrows to specific students; ``include_text=False`` drops the text
    column; text is trimmed to ``max_text_chars`` (0 = full). Attachments are
    never included. Historical rows remain; ``current_enrollment`` marks
    membership in this bundle's mirror roster. Gated by the outbound safety scan."""
    identity_error = _saved_course_gate_check(course_id)
    if identity_error:
        return {"ok": False, "error": identity_error}
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}

    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}

    bundle, bundle_err = _mirror_submission_bundle(course_id, assignment_id)
    if bundle_err:
        return {"ok": False, "error": bundle_err}
    if bundle is None:
        return {"ok": False, "error": _MIRROR_UNAVAILABLE_SUBMISSIONS_ERROR}

    # Sync the full roster first so the scrub map covers every enrolled
    # student, not just the ones who submitted this assignment.
    with _vault_transaction(vault):
        roster_service.upsert_roster(vault, bundle["roster"])
        assignment, subs = bundle["assignment"], bundle["rows"]

        rows = pseudonym.pseudonymize_submission_rows(vault, subs)
        current_pseudonyms = {
            vault.get_or_assign(student["id"])
            for student in bundle["roster"] if student.get("id") is not None
        }
        for row in rows:
            row["current_enrollment"] = row["pseudonym"] in current_pseudonyms
        wanted = {p.strip().casefold() for p in pseudonyms.split(",") if p.strip()}
        if wanted:
            rows = [r for r in rows if r["pseudonym"].casefold() in wanted]
        # Trim/drop text BEFORE the gate so the scan covers exactly the bytes
        # that leave the machine.
        for row in rows:
            if include_text:
                row["text"] = _truncate_text(row["text"], max_text_chars)
            else:
                row.pop("text", None)

        payload = {
            "assignment": {
                "id": assignment.get("id"),
                "title": assignment.get("name", ""),
                "points_possible": assignment.get("points_possible"),
                "due_at": assignment.get("due_at", ""),
            },
            "submissions": rows,
            "source": "mirror",
            "synced_at": bundle["synced_at"],
        }
        result = pseudonym.gate(payload, vault)
    if result.get("ok"):
        columns = (_SUBMISSION_COLUMNS if include_text
                   else tuple(c for c in _SUBMISSION_COLUMNS if c != "text"))
        result["submissions"] = _tabulate(result["submissions"], columns)
    return result


# A student's writing history has no session lookback of its own to borrow, and
# the substrate landed 2026-07-27 -- so any student's real history today is far
# shorter than this. Two years keeps the default call cheap and bounded rather
# than scanning from date.min, while being generous enough that "since"/"until"
# only need to be passed when someone actually wants to narrow the window.
_DEFAULT_HISTORY_LOOKBACK_DAYS = 730


def get_writing_history(pseudonym: str, since: str = "", until: str = "",
                        include_text: bool = False,
                        max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS) -> dict:
    """One student's Writing Record evidence across time, pseudonym-first:
    dated submissions, assignment context, word counts, segment attribution,
    and structural flags. Writing Record does not score, coach, or judge work.
    Read from the private per-student store
    (``api/dailywriting``), never from a course or the CanvasMirror -- there
    is no ``course_id`` here because the store has no course concept and
    nothing to refresh, but the identity vault and the outbound safety gate
    still apply: this is the first tool to carry student data with no
    ``course_id`` gate.

    No judgment is computed here; the assistant and teacher may evaluate the
    evidence later if they choose.
    ``since``/``until`` are ``YYYY-MM-DD`` dates (both default to a two-year
    lookback from today). ``include_text=False`` (the default) omits every
    span quoted from student writing; ``include_text=True`` includes them
    trimmed to ``max_text_chars`` (0 = full), trimmed BEFORE the gate scans
    them. The assignment prompt is teacher-authored, not student data, and is
    always included."""
    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}

    try:
        until_date = date.fromisoformat(until) if until else date.today()
        since_date = (date.fromisoformat(since) if since else
                      until_date - timedelta(days=_DEFAULT_HISTORY_LOOKBACK_DAYS))
    except ValueError as error:
        return {"ok": False,
                "error": f"since/until must be YYYY-MM-DD dates: {error}"}
    if since_date > until_date:
        return {"ok": False, "error": "since is after until"}

    try:
        repository = _dailywriting_repository_factory()
    except DailyWritingStoreError as error:
        return {"ok": False, "error": str(error)}

    try:
        submissions = repository.submissions_in_window(
            pseudonym, since_date, until_date)
        reps = {}
        for submission in submissions:
            if submission.rep_id not in reps:
                reps[submission.rep_id] = repository.read_rep(submission.rep_id)
    except IdentityError:
        return {
            "ok": False,
            "error": (f"'{pseudonym}' is not a known pseudonym in the "
                      "identity vault; sync the roster for this student's "
                      "section in the CanvasExpert web UI, then retry."),
        }

    payload = dailywriting_projection.build_history_payload(
        pseudonym_id=pseudonym,
        since=since_date,
        until=until_date,
        submissions=submissions,
        reps=reps,
        include_text=include_text,
        max_text_chars=max_text_chars,
    )
    return _pseudonym_gate(payload, vault)


def get_gradebook_snapshot(course_id: str) -> dict:
    """Whole-course grading snapshot, pseudonymized: per-assignment stats
    (``title`` instead of ``name``, no ``html_url``) and per-student stats
    (``pseudonym`` instead of a name), each as a {columns, rows} table.
    ``has_submission`` counts roster rows with a submitted_at timestamp;
    ``has_grade`` counts graded roster rows with scores, including manual
    grades without a submission. ``ungraded`` follows Canvas workflow state;
    ``partially_scored`` is the subset of those rows with a numeric score.
    Their difference is not ungraded work.
    Served ONLY from the local CanvasMirror — never live Canvas; a stale or
    missing mirror is refused (call refresh_mirror first). Gated by the
    outbound safety scan before tabulation."""
    identity_error = _saved_course_gate_check(course_id)
    if identity_error:
        return {"ok": False, "error": identity_error}
    err = _course_gate_check(course_id)
    if err:
        return {"ok": False, "error": err}

    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}

    snapshot, snapshot_error = _load_snapshot(course_id)
    if snapshot_error:
        return {"ok": False, "error": snapshot_error}
    students = snapshot.get("students") or []

    assignment_rows = []
    for a in snapshot["assignments"]:
        row = {k: v for k, v in a.items() if k not in ("name", "html_url")}
        row["title"] = a.get("name", "")
        row["has_submission"] = row.pop("submitted")
        row["has_grade"] = row.pop("graded")
        assignment_rows.append(row)

    payload = {
        "class_avg": snapshot["class_avg"],
        "student_count": snapshot["student_count"],
        "total_missing": snapshot["total_missing"],
        "total_ungraded": snapshot["total_ungraded"],
        "source": snapshot.get("source", "canvas"),
        "synced_at": snapshot.get("synced_at", ""),
        "assignments": assignment_rows,
        "students": [],
    }
    with _vault_transaction(vault):
        roster_service.upsert_roster(vault, [
            {"id": row.get("user_id"), "name": row.get("name", "")}
            for row in students
        ])
        payload["students"] = pseudonym.pseudonymize_gradebook_rows(vault, snapshot["students"])
        result = pseudonym.gate(payload, vault)
    if result.get("ok"):
        result["assignments"] = _tabulate(result["assignments"], _GRADEBOOK_ASSIGNMENT_COLUMNS)
        result["students"] = _tabulate(result["students"], _GRADEBOOK_STUDENT_COLUMNS)
    return result


_REFRESH_TIMEOUT_SECONDS = 25.0

# refresh_mirror drives a submissions delta (course.refresh) AND a roster
# pass, so a roster that has aged past the serve window is recoverable on
# demand. Without the explicit roster scope the manual refresh runs a delta
# only, which never rewrites the roster file — roster reads
# would then refuse indefinitely (the assistant loops: refresh says "synced",
# the roster stays stale) while the gradebook served fine off deltas. The
# background heartbeat closes the same gap from the other side; see
# mirror_service.due_passes.
_REFRESH_SCOPES = ["course.refresh", "roster", "groups"]


def refresh_mirror(course_id: str) -> dict:
    """Ask Canvas Expert to sync this course's local CanvasMirror from Canvas
    (a submissions delta plus a roster refresh), then report freshness — the
    response is a sync STATUS, never Canvas data. Call this after
    get_roster/get_submissions/get_gradebook_snapshot refuses as stale or
    unavailable, then re-call that same tool; this tool never returns course,
    roster, or submission data itself, so it needs no identity vault and no
    outbound safety scan. It accepts any saved course (Current or Previous)."""

    try:
        plan_id = _enqueue_sync(course_id, _REFRESH_SCOPES)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    except Exception as error:
        return {"ok": False, "error": f"Could not start a sync: {error}"}

    plan = _wait_for_plan(plan_id, timeout_seconds=_REFRESH_TIMEOUT_SECONDS)
    state = plan.get("state", "failed")
    if state == "succeeded":
        return {"ok": True, "status": "synced",
                "message": "Mirror refreshed (roster, groups, assignments, and submissions status only). Re-read the refused tool now."}
    if state in ("queued", "running"):
        return {"ok": True, "status": "syncing",
                "message": "Still syncing — wait a few seconds, then try again."}
    return {"ok": False, "status": "failed",
            "error": "Sync failed. Try again, or use Sync now in the CanvasExpert web UI."}


def get_bell_schedule(schedule_id: str = "") -> dict:
    """Read bell schedule(s) from workspace Calendars folder.

    No course gate, no student data — no safety gate.
    schedule_id: empty string ("") returns all variants as {schedule_id: meetings},
                 non-empty returns just that one or error if not found.
    """
    bell_schedules, problems = deps.load_bell_schedules()

    if schedule_id == "":
        return {
            "ok": True,
            "schedules": bell_schedules,
            "problems": problems,
        }

    if schedule_id not in bell_schedules:
        return {
            "ok": False,
            "error": f"schedule '{schedule_id}' not found",
            "problems": problems,
        }

    return {
        "ok": True,
        "schedule_id": schedule_id,
        "meetings": bell_schedules[schedule_id],
        "problems": problems,
    }


def get_day_schedule(date: str) -> dict:
    """Resolve teacher blocks for a specific date.

    No course gate, no student data — no safety gate.
    date: "YYYY-MM-DD" string
    Returns the canonical calendar's own resolution ``state`` (unconfigured,
    invalid_calendar, outside_coverage, no_school, no_regular_classes,
    unknown_schedule, or ready) alongside blocks: [{name, label, start, end,
    raw_periods, schedule_id, period_ids, segments, seq}, ...] sorted by
    start time. A repeated block produces one entry per consecutive meeting
    run; slides bind to its first entry.
    """
    result = deps.resolve_schedule_for(date)
    return {
        "ok": True,
        "date": date,
        "state": result["state"],
        "blocks": result["blocks"],
        "problems": result["problems"],
    }


def get_teacher_schedule() -> dict:
    """Read teacher schedule from workspace Calendars folder.

    No course gate, no student data — no safety gate.
    """
    teacher_schedule, problems = deps.load_teacher_schedule()
    return {
        "ok": True,
        "schedule": teacher_schedule,
        "problems": problems,
    }


def get_school_calendar(date_from: str = "", date_to: str = "") -> dict:
    """Read the canonical School Calendar: readiness, plus a bounded range.

    No course_id, no student data -- no course gate, no safety gate.
    date_from/date_to: pass both for day/grading-period/event rows in that
    inclusive range; omit both for readiness only (revision, coverage,
    today's resolution, low-coverage warning). Never raises.
    """
    bell_schedules, _bell_problems = deps.load_bell_schedules()
    readiness = school_calendar.readiness(bell_schedule_ids=bell_schedules)
    result = {"ok": True, "readiness": readiness}
    if date_from and date_to:
        projection, problems = school_calendar.range_projection(date_from, date_to)
        if projection is None:
            return {"ok": False, "problems": problems}
        result["days"] = projection["days"]
        result["grading_periods"] = projection["grading_periods"]
        result["events"] = projection["events"]
    return result


# --- Scoring Packet MCP Tools (v22) ----------------------------------------

def _safe_bundle_path(session: dict) -> str:
    """Resolved on-disk path of a session's SAFE bundle, or "" when absent."""
    raw = (session.get("privacy_artifacts") or {}).get("safe_bundle") or ""
    if not raw:
        return ""
    resolved = workspace.extended_path(raw)
    return resolved if os.path.isfile(resolved) else ""


def _visible_scoring_sessions() -> list[tuple[dict, dict]]:
    """One identity-free row per root whose frozen scope includes a Current course."""
    from api.powergrader import scoring_queue

    active_course_ids = {str(c.get("id", "")) for c in config.active_courses()}
    visible = []
    for summary in scoring_queue.root_summaries():
        root_id = str(summary.get("session_id") or "")
        root = scoring_queue.load_root_session(root_id)
        if not root:
            continue
        queued_course_ids = {str(item.get("course_id") or "") for item in root.get("queue") or []}
        if not (queued_course_ids & active_course_ids):
            continue
        visible.append((summary, root))
    visible.sort(key=lambda pair: str(pair[0].get("created") or ""), reverse=True)
    return visible


def _staged_marker(session: dict) -> dict:
    marker = session.get("assistant_staged")
    return marker if isinstance(marker, dict) else {}


def _scored_count(session: dict) -> int:
    return sum(1 for student in (session.get("students") or [])
               if student.get("ai_score") is not None)


def start_scoring_session(course_id: str = "", assignment_id: str = "") -> dict:
    """Freeze a mirror-backed queue of Current-course assignments needing scoring."""
    course_key = str(course_id or "").strip()
    assignment_key = str(assignment_id or "").strip()
    if assignment_key and not course_key:
        return {"ok": False, "code": "invalid_scope",
                "error": "assignment_id requires a Current course_id."}
    if course_key:
        gate_error = _course_gate_check(course_key)
        if gate_error:
            return {"ok": False, "code": "invalid_scope", "error": gate_error}

    courses = list(config.active_courses() or [])
    if course_key:
        courses = [course for course in courses if str(course.get("id") or "") == course_key]
    from api.powergrader import scoring_queue

    snapshots = []
    needs_refresh = []
    for course in courses:
        current_id = str(course.get("id") or "")
        snapshot, error = _load_snapshot(current_id)
        if error or snapshot is None:
            needs_refresh.append(current_id)
        else:
            snapshots.append((course, snapshot))
    if needs_refresh:
        return {"ok": True, "status": "needs_refresh", "code": "needs_refresh",
                "course_ids": needs_refresh,
                "message": "Refresh the listed Current-course mirrors, then retry this start."}

    queue = []
    exact_assignment_found = False
    for course, snapshot in snapshots:
        course_id_value = str(course.get("id") or "")
        course_label = str(course.get("nickname") or course.get("name") or course_id_value)
        for assignment in snapshot.get("assignments") or []:
            assignment_id_value = str(assignment.get("id") or "")
            if assignment_key and assignment_id_value == assignment_key:
                exact_assignment_found = True
            if assignment_key and assignment_id_value != assignment_key:
                continue
            try:
                ungraded = int(assignment.get("ungraded") or 0)
            except (TypeError, ValueError):
                ungraded = 0
            if ungraded <= 0:
                continue
            try:
                partially_scored = int(assignment.get("partially_scored") or 0)
            except (TypeError, ValueError):
                partially_scored = 0
            queue.append({
                "course_id": course_id_value,
                "course_label": course_label,
                "assignment_id": assignment_id_value,
                "assignment_label": str(assignment.get("name") or assignment.get("title") or assignment_id_value),
                "due_at": str(assignment.get("due_at") or ""),
                "ungraded": ungraded,
                "partially_scored": partially_scored,
            })
    if assignment_key and not exact_assignment_found:
        return {"ok": False, "code": "invalid_scope",
                "error": "assignment_id is not present in the fresh gradebook snapshot for this Current course."}
    if not queue:
        return {"ok": True, "status": "nothing_to_grade", "counts": {
            "total": 0, "completed": 0, "completed_with_holds": 0,
            "no_longer_needs_grading": 0, "remaining": 0, "failed": 0,
        }}

    root = scoring_queue.create_root_session(
        queue=queue,
        scope={"course_ids": [str(course.get("id") or "") for course in courses],
               "requested_course_id": course_key, "requested_assignment_id": assignment_key},
    )
    if not root:
        return {"ok": False, "code": "session_store_unavailable",
                "error": "The Scoring Session queue could not be saved locally."}
    return _with_next("start_scoring_session", {
        "ok": True,
        "status": "started",
        "scoring_session_id": root["session_id"],
        "queued_assignments": len(queue),
        "counts": scoring_queue.public_progress(root),
        "active_course": queue[0]["course_label"],
        "active_assignment": queue[0]["assignment_label"],
    })


def _prepared_assignment_summary(root_id: str, item: dict, child: dict) -> dict:
    from api.powergrader import scoring_packet as sp, scoring_queue

    basis = child.get("scoring_basis")
    if not isinstance(basis, dict) or not basis.get("source") or not basis.get("label"):
        return {"ok": False, "code": "invalid_scoring_session",
                "error": "The active assignment has no resolved scoring basis."}
    bundle_path = _safe_bundle_path(child)
    if not bundle_path:
        return {"ok": False, "code": "packet_missing",
                "error": "The active assignment's SAFE packet is unavailable."}
    try:
        with open(bundle_path, encoding="utf-8") as handle:
            safe_bundle = json.load(handle)
        page = sp.build_packet(session=child, safe_bundle=safe_bundle,
                               offset=0, limit=1, include_context=False)
    except Exception:
        return {"ok": False, "code": "packet_unavailable",
                "error": "The active assignment's SAFE packet could not be verified."}
    return {
        "ok": True,
        "status": "ready",
        "scoring_session_id": root_id,
        "course": str(item.get("course_label") or ""),
        "assignment_name": str(item.get("assignment_label") or ""),
        "due_at": str(item.get("due_at") or ""),
        "ungraded": int(item.get("ungraded") or 0),
        "partially_scored": int(item.get("partially_scored") or 0),
        "student_count": len(child.get("students") or []),
        "response_count": int(page.get("total") or 0),
        "held": int(page.get("held") or 0),
        "scoring_basis": basis,
        "queue_counts": scoring_queue.public_progress(
            scoring_queue.load_root_session(root_id) or {"queue": []}),
    }


def continue_scoring_session(scoring_session_id: str, rubric_name: str = "",
                             scoring_guidance: str = "") -> dict:
    """Prepare or resume only the current assignment in a root Scoring Session."""
    from api.powergrader import scoring_packet as sp, scoring_queue, session_store, start_workflow

    root_id = str(scoring_session_id or "")
    while True:
        claim = scoring_queue.claim_active_item(root_id)
        if claim["kind"] == "not_found":
            return {"ok": False, "code": "session_not_found", "error": "Scoring Session not found."}
        if claim["kind"] == "complete":
            return {"ok": True, "status": "complete", "scoring_session_id": root_id,
                    "counts": scoring_queue.public_progress(claim["root"])}
        if claim["kind"] == "busy":
            return {"ok": False, "code": "preparation_in_progress",
                    "error": "The active assignment is already being prepared. Retry continue_scoring_session shortly."}
        item = claim["item"]
        if claim["kind"] == "ready":
            resolved = scoring_queue.resolve_active_child(root_id)
            if not resolved.get("ok"):
                return {"ok": False, "code": str(resolved.get("code") or "child_unavailable"),
                        "error": "The active assignment run could not be safely resumed."}
            summary = _prepared_assignment_summary(root_id, item, resolved["child"])
            if summary.get("ok"):
                summary["queue_counts"] = scoring_queue.public_progress(resolved["root"])
                return _with_next("continue_scoring_session", summary)
            return summary

        result = start_workflow.run_start_session(
            course_id=str(item.get("course_id") or ""),
            assignment_id=str(item.get("assignment_id") or ""),
            mode="packet", watch_late="false", auto_post="false",
            rubric_name=str(rubric_name or "").strip(), persona_id="",
            feedback_pattern_id="", model_id="", response_kind="scr",
            source_text="", source_files_json="", source_uploads=None,
            oral_reading_passage="", oral_reading_enabled="false",
            save_session=session_store.save_session, scoring_session=True,
            scoring_guidance=str(scoring_guidance or ""),
            parent_scoring_session_id=root_id,
        )
        payload = result.get("payload") or {}
        if not result.get("ok"):
            if payload.get("code") == "nothing_to_grade":
                if not scoring_queue.record_nothing_to_grade(root_id, claim["index"], claim["claim"]):
                    return {"ok": False, "code": "active_item_changed",
                            "error": "The active assignment changed during refresh; no queue item was skipped."}
                continue
            if payload.get("code") == "needs_scoring_norms":
                if not scoring_queue.record_needs_teacher_input(root_id, claim["index"], claim["claim"]):
                    return {"ok": False, "code": "active_item_changed",
                            "error": "The active assignment changed during preparation."}
                root = scoring_queue.load_root_session(root_id) or {"queue": []}
                return {
                    "ok": True,
                    "status": "needs_teacher_input",
                    "code": "needs_scoring_norms",
                    "scoring_session_id": root_id,
                    "course": str(item.get("course_label") or ""),
                    "assignment_name": str(item.get("assignment_label") or ""),
                    "rubric_labels": [str(label) for label in payload.get("rubric_labels") or []],
                    "question": "Which rubric should I use, or what bounded scoring guidance should I follow for this assignment?",
                    "queue_counts": scoring_queue.public_progress(root),
                }
            code = str(payload.get("code") or "start_failed")
            scoring_queue.record_preparation_failure(root_id, claim["index"], claim["claim"], code)
            return {"ok": False, "code": code,
                    "error": "The active assignment could not be prepared safely. It remains in this Scoring Session and can be retried."}

        child_id = str(result.get("session_id") or "")
        child = session_store.load_session(child_id) if child_id else None
        if not child or not scoring_queue.attach_child(
                root_id, claim["index"], claim["claim"], child_id):
            return {"ok": False, "code": "active_item_changed",
                    "error": "The active assignment changed during preparation; its private run was not attached."}
        resolved = scoring_queue.resolve_active_child(root_id)
        if not resolved.get("ok"):
            return {"ok": False, "code": "child_unavailable",
                    "error": "The active assignment run could not be safely resumed."}
        summary = _prepared_assignment_summary(root_id, item, resolved["child"])
        if summary.get("ok"):
            summary["queue_counts"] = scoring_queue.public_progress(resolved["root"])
            return _with_next("continue_scoring_session", summary)
        return summary


def list_scoring_sessions() -> dict:
    """List identity-free root Scoring Sessions and aggregate queue progress."""
    from api.powergrader import scoring_queue

    rows = []
    visible = _visible_scoring_sessions()
    for summary, root in visible:
        labels = scoring_queue.active_labels(root)
        counts = scoring_queue.public_progress(root)
        rows.append([
            summary.get("session_id"),
            summary.get("created"),
            root.get("status"),
            labels["active_course"],
            labels["active_assignment"],
            counts["total"],
            counts["completed"],
            counts["completed_with_holds"],
            counts["no_longer_needs_grading"],
            counts["remaining"],
            counts["failed"],
        ])

    return {
        "ok": True,
        "sessions": {"columns": list(_SCORING_SESSION_COLUMNS), "rows": rows},
    }


def get_scoring_packet(scoring_session_id: str, offset: int = 0, limit: int = 10,
                       include_context: bool = True) -> dict:
    """Retrieve one page of student responses from a Scoring Session's SAFE bundle.

    Parameters:
    - scoring_session_id: private Scoring Session identifier
    - offset: starting row (default 0)
    - limit: rows to return (default 10)
    - include_context: if True, include contract text, rubric, shared materials

    Paging walks responses, not students: on a multi-item quiz one student
    holds several rows, so offset, limit, total and next_offset all count
    rows. students_total carries the distinct-student count separately.

    Do not read this ``total`` against list_scoring_sessions' ``total``, which
    counts students in the session instead of scorable rows. A lower number
    here can reflect held responses, students excluded from the SAFE bundle,
    or bundle students without response rows. Membership counts expose those
    differences separately from response paging.

    Returns a packet with:
    - packet_digest: bundle identity, required by submit_scoring_results
    - items: {columns, rows} table of prompts, deduplicated by item_id
    - students: {columns, rows} table of (pseudonym, item_id, text)
    - total: scorable rows in the whole session
    - students_total: distinct students holding at least one scorable row
    - session_student_count / bundle_student_count: distinct membership counts
    - excluded_student_count: nonnegative session-minus-bundle count gap,
      without an identity join or inferred exclusion reason
    - students_without_responses: bundle students with no response rows
    - returned: rows in this page
    - next_offset: offset for the next page, absent on the final page
    - held: responses with no scorable text (media-only or empty)
    - held_pseudonyms: distinct pseudonyms holding at least one held response
    - included_context: bool (true if contract/rubric were included)
    - rubric: declared rubric label and whether its text resolved, when context is included
    - estimated_tokens: projected token count for this response

    Course-gated on the session's course_id. Refuses when:
    - scoring_session_id is not found
    - session has no SAFE bundle
    - teacher's course is not a Current course
    - the page projects over the token budget (retry with a smaller limit)

    Text-only (no media entries, no attachment filenames). Never raises.
    """
    from api.powergrader import context, scoring_packet as sp, scoring_queue

    resolved = scoring_queue.resolve_active_child(scoring_session_id)
    if not resolved.get("ok"):
        code = str(resolved.get("code") or "session_not_found")
        if code == "needs_teacher_input":
            return {"ok": False, "code": code,
                    "error": "Continue the Scoring Session with a rubric or bounded guidance before requesting its packet."}
        return {"ok": False, "code": code, "error": "The Scoring Session has no ready active assignment packet."}
    session = resolved["child"]

    gate_err = _course_gate_check(str(session.get("course_id") or ""))
    if gate_err:
        return {"ok": False, "error": gate_err}

    bundle_path = _safe_bundle_path(session)
    if not bundle_path:
        return {"ok": False, "error": "Safe AI Packet student response bundle is missing."}

    try:
        with open(bundle_path, encoding="utf-8") as f:
            safe_bundle = json.load(f)
    except Exception as e:
        return {"ok": False, "error": f"Could not load Safe AI Packet bundle: {e}"}

    if offset <= 0 and not include_context:
        return {"ok": False, "code": "scoring_context_required",
                "error": "The first packet page must include its scoring contract and basis."}

    basis = session.get("scoring_basis") or {}
    rubric_name = str(basis.get("label") or session.get("rubric_name") or "")
    rubric_text = (session.get("scoring_rubric_text")
                   or session.get("rubric_text")
                   or context.load_rubric_text(rubric_name))
    persona = None

    try:
        packet = sp.build_packet(
            session=session,
            safe_bundle=safe_bundle,
            offset=offset,
            limit=limit,
            include_context=include_context,
            rubric_text=rubric_text,
            persona=persona,
        )
    except sp.PacketTooLarge as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Could not build packet: {e}"}

    # build_packet hands back dict rows so the scan can walk into the response
    # text. Tabulating first would bury every cell in a list, where the
    # scanner's key-based walk cannot reach it.
    result = _pseudonym_gate(packet, _vault_factory())
    if result.get("ok"):
        if include_context:
            result["rubric"] = {
                "label": rubric_name,
                "included": bool(str(rubric_text or "").strip()),
            }
        result["items"] = _tabulate(result["items"], _PACKET_ITEM_COLUMNS)
        result["students"] = _tabulate(result["students"], _PACKET_STUDENT_COLUMNS)
        result = _with_next("get_scoring_packet", result)
    return result


def submit_scoring_results(scoring_session_id: str, results: list,
                           expected_packet_digest: str, review_digest: str = "",
                           answers: dict | None = None) -> dict:
    """Validate SAFE results, ask only bounded risk questions, then write them.

    The Canvas transport and identity lookup stay below this MCP boundary.
    No result content or real identity is returned, including on failure.
    """
    from api import feedback_pipeline as fp
    from api.powergrader import scoring_packet as sp, scoring_queue, session_store

    resolved = scoring_queue.resolve_active_child(scoring_session_id)
    if not resolved.get("ok"):
        return {"ok": False, "code": str(resolved.get("code") or "session_not_found"),
                "error": "The Scoring Session has no ready active assignment."}
    session = resolved["child"]
    child_session_id = str(session.get("session_id") or "")
    gate_error = _course_gate_check(str(session.get("course_id") or ""))
    if gate_error:
        return {"ok": False, "code": "course_unavailable", "error": gate_error}
    if not session.get("scoring_basis"):
        return {"ok": False, "code": "invalid_scoring_session", "error": "This is not a Scoring Session."}
    bundle_path = _safe_bundle_path(session)
    if not bundle_path:
        return {"ok": False, "code": "packet_missing", "error": "The SAFE scoring packet is unavailable."}
    try:
        with open(bundle_path, encoding="utf-8") as handle:
            safe_bundle = json.load(handle)
    except Exception:
        return {"ok": False, "code": "packet_unavailable", "error": "The SAFE scoring packet could not be read."}
    packet_digest = sp.packet_digest(
        scoring_session_id, safe_bundle, assignment_run_id=child_session_id,
        course_id=session.get("course_id"), assignment_id=session.get("assignment_id"),
    )
    if str(expected_packet_digest or "") != packet_digest:
        return {"ok": False, "code": "stale_packet", "error": "The scoring packet changed. Retrieve the current packet before submitting."}

    vault, vault_error = _open_vault()
    if vault_error:
        return {"ok": False, "code": "identity_unavailable", "error": "The private identity vault is unavailable."}
    verdict = fp.validate_results(results, safe_bundle, vault)
    if not verdict.get("ok"):
        return {"ok": False, "code": "invalid_results",
                "error": "Results must match the supplied pseudonyms and item ids and contain valid feedback.",
                "validation": {"errors": len(verdict.get("errors") or []),
                               "warnings": len(verdict.get("warnings") or [])}}
    try:
        rows = fp.reidentify(results, vault)
    except Exception:
        return {"ok": False, "code": "invalid_results", "error": "Results could not be safely matched to this session."}
    for index, row in enumerate(rows):
        row["pseudonym"] = str((results[index] or {}).get("pseudonym") or "")
    if any(not row.get("resolved") for row in rows):
        return {"ok": False, "code": "invalid_results", "error": "Every result must match a supplied pseudonym."}

    names = {}
    every_pseudonym = []
    for entry in vault.entries():
        label = str(entry.get("pseudonym") or "").strip()
        if label:
            names[str(entry.get("canvas_id"))] = label
            every_pseudonym.append(label)
    by_uid = fp.merge_rows_by_uid(rows)
    item_by_uid = fp.item_rows_by_uid(rows)
    candidate = copy.deepcopy(session)
    students_by_uid = {str(st.get("user_id")): st for st in candidate.get("students") or []}
    for user_id, row in by_uid.items():
        student = students_by_uid.get(str(user_id))
        if student:
            student["ai_score"] = row.get("score")
            student["ai_feedback"] = row.get("feedback") or ""
            student["ai_item_results"] = item_by_uid.get(str(user_id), [])

    # Ordinary assignment risk planning reuses the exact freeze/drift lane used
    # by Canvas Expert's guarded scorer. Its internal user ids are translated
    # before any question can cross MCP.
    if not session.get("new_quiz_item_finalization_supported"):
        from api.powergrader import scoring_apply
        plan = scoring_apply.build_plan(candidate, pseudonyms=every_pseudonym)
        if not plan.get("ok"):
            return {"ok": False, "code": str(plan.get("code") or "canvas_preflight_failed"),
                    "error": "Canvas could not safely prepare this scoring submission."}
        if not plan.get("candidate_ids"):
            return {"ok": False, "code": "no_valid_results", "error": "No scored results are ready to post."}
        if plan.get("questions"):
            safe = _scoring_apply_safe(plan, names)
            if not review_digest:
                candidate_ids = set(plan["candidate_ids"])
                held_count = sum(1 for st in candidate.get("students") or []
                                 if str(st.get("user_id") or "") not in candidate_ids
                                 and not st.get("posted"))
                response = {"ok": True, "status": "needs_teacher_input",
                    "review_digest": plan["digest"], "questions": safe["questions"],
                    "counts": {"ready": len(plan["candidate_ids"]),
                               "held": held_count}}
                return _record_scoring_root_result(
                    scoring_session_id, child_session_id, pseudonym.gate(response, vault))
            if str(review_digest) != str(plan.get("digest")):
                return {"ok": False, "code": "review_changed", "error": "The scoring review changed. Submit the current review again."}
        elif review_digest:
            return {"ok": False, "code": "review_changed", "error": "No teacher questions remain for this review."}

        resolved = scoring_apply.resolve_answers(plan, answers)
        if not resolved.get("ok"):
            return {"ok": False, "code": resolved.get("code") or "invalid_answer",
                    "error": "Answer every listed scoring question with one of its offered options."}
        # Preserve the scoring_apply caller-held lock contract across both the
        # staged-result update and the complete plan/freeze/push sequence.
        # session_store uses an RLock and re-entrant interprocess lock, so the
        # guarded helpers can safely acquire the same session lock again.
        with session_store.session_lock(child_session_id):
            current = session_store.load_session(child_session_id)
            if not current:
                return {"ok": False, "code": "session_not_found", "error": "Scoring Session not found."}
            current_by_uid = {str(st.get("user_id")): st for st in current.get("students") or []}
            for uid, staged in students_by_uid.items():
                target = current_by_uid.get(uid)
                if target and uid in by_uid:
                    target["ai_score"] = staged.get("ai_score")
                    target["ai_feedback"] = staged.get("ai_feedback")
                    target["ai_item_results"] = staged.get("ai_item_results") or []
            session_store.save_session(current)
            payload, _status = scoring_apply.apply_plan(
                child_session_id, expected_digest=plan["digest"], answers=answers,
                load_session=session_store.load_session, save_session=session_store.save_session,
                pseudonyms=every_pseudonym,
            )
        held_user_ids = {str(st.get("user_id") or "") for st in session.get("students") or []
                         if str(st.get("user_id") or "") not in set(plan.get("candidate_ids") or [])}
        held_user_ids.update(str(uid) for uid in resolved.get("skipped") or [])
        return _record_scoring_root_result(
            scoring_session_id, child_session_id,
            _scoring_apply_result(payload, names, vault, held_user_ids=held_user_ids))

    # New Quizzes retain their item-preserving finalize lane. These conversational
    # questions are built from the exact SAFE results; the lane still enforces
    # complete-result preflight, version drift, idempotency and verification.
    nq_questions = _new_quiz_scoring_questions(candidate, rows, safe_bundle, names, every_pseudonym, vault)
    nq_digest = _canonical_digest({"packet_digest": packet_digest, "results": results,
                                   "questions": nq_questions})
    if nq_questions:
        public_questions = _new_quiz_public_questions(nq_questions, names)
        if not review_digest:
            return _record_scoring_root_result(
                scoring_session_id, child_session_id,
                pseudonym.gate({"ok": True, "status": "needs_teacher_input",
                    "review_digest": nq_digest, "questions": public_questions}, vault))
        if str(review_digest) != nq_digest:
            return {"ok": False, "code": "review_changed", "error": "The scoring review changed. Submit the current review again."}
        answer_result = _resolve_scoring_answers(nq_questions, answers)
        if not answer_result.get("ok"):
            return {"ok": False, "code": answer_result.get("code") or "invalid_answer",
                    "error": "Answer every listed scoring question with one of its offered options."}
        if answer_result.get("stop"):
            return _record_scoring_root_result(
                scoring_session_id, child_session_id,
                {"ok": True, "status": "held", "counts": {
                    "finalized": 0, "already_applied": 0,
                    "held": len(nq_questions), "failed": 0}, "results": []})
        excluded = answer_result.get("skip_pseudonyms") or set()
        rows = [row for row in rows if row.get("pseudonym") not in excluded]
        by_uid = fp.merge_rows_by_uid(rows)
        item_by_uid = fp.item_rows_by_uid(rows)

    with session_store.session_lock(child_session_id):
        current = session_store.load_session(child_session_id)
        if not current:
            return {"ok": False, "code": "session_not_found", "error": "Scoring Session not found."}
        current_by_uid = {str(st.get("user_id")): st for st in current.get("students") or []}
        for uid in by_uid:
            target = current_by_uid.get(str(uid))
            if target:
                target["ai_item_results"] = item_by_uid.get(str(uid), [])
        session_store.save_session(current)
    with _new_quiz_http_scope():
        preview = _prepare_new_quiz_finalization(child_session_id)
        if not preview.get("ok"):
            return _record_scoring_root_result(
                scoring_session_id, child_session_id,
                pseudonym.gate({"ok": False, "code": "new_quiz_preflight_failed",
                    "error": "Canvas could not safely prepare New Quiz item finalization.",
                    "counts": {"finalized": 0, "already_applied": 0,
                               "held": max(0, len(session.get("students") or []) - len(by_uid)),
                               "failed": 1}}, vault))
        applied = _finalize_new_quiz_results(preview["operation_id"], preview["review_digest"])
    applied.pop("operation_id", None)
    applied.pop("next", None)
    applied_counts = applied.get("counts") or {}
    held_count = max(0, len(session.get("students") or []) - len(by_uid))
    final_counts = {"finalized": int(applied_counts.get("finalized") or 0),
                    "already_applied": int(applied_counts.get("already_applied") or 0),
                    "held": held_count,
                    "failed": int(applied_counts.get("failed") or 0)}
    return _record_scoring_root_result(
        scoring_session_id, child_session_id,
        pseudonym.gate({"ok": bool(applied.get("ok")), "counts": final_counts,
            "results": applied.get("results") or []}, vault))


def _record_scoring_root_result(root_session_id: str, child_session_id: str,
                                result: dict) -> dict:
    """Persist aggregate queue progress after the assignment write owner returns."""
    from api.powergrader import scoring_queue

    update = scoring_queue.record_submit_result(root_session_id, child_session_id, result)
    if not update.get("ok"):
        return {"ok": False, "code": str(update.get("code") or "active_item_changed"),
                "error": "The Scoring Session changed during submission; its queue was not advanced."}
    response = {**result,
                "scoring_session_id": root_session_id,
                "queue_counts": update.get("progress") or {},
                "session_status": update.get("status") or "active"}
    if result.get("ok") and result.get("status") != "needs_teacher_input":
        response["next"] = (
            "Call continue_scoring_session with this same scoring_session_id to advance "
            "to the next assignment or receive aggregate completion."
        )
    return response


def _scoring_apply_result(payload: dict, names: dict, vault, *, held_user_ids=()) -> dict:
    """Project ordinary assignment writes to aggregate, pseudonym-only outcomes."""
    counts = {"finalized": 0, "already_applied": 0, "held": 0, "failed": 0}
    outcomes = []
    for item in payload.get("results") or []:
        status = str(item.get("status") or "failed")
        public_status = "finalized" if status == "pushed" else status
        if public_status not in counts:
            public_status = "failed"
        counts[public_status] += 1
        outcomes.append({"pseudonym": names.get(str(item.get("user_id"))) or "(unknown student)",
                         "status": public_status,
                         **({"code": str(item.get("code") or "failed")} if public_status == "failed" else {})})
    held_ids = {str(uid) for uid in held_user_ids}
    counts["held"] = len(held_ids)
    outcomes.extend({"pseudonym": names.get(uid) or "(unknown student)", "status": "held"}
                    for uid in sorted(held_ids))
    result = {"ok": bool(payload.get("ok")), "counts": counts, "results": outcomes}
    if not result["ok"]:
        result["code"] = str(payload.get("code") or "write_failed")
        result["error"] = "One or more results could not be safely finalized. Review Canvas before retrying."
    return pseudonym.gate(result, vault)


def _new_quiz_scoring_questions(session: dict, rows: list[dict], bundle: dict,
                                names: dict, pseudonyms: list[str], vault) -> list[dict]:
    """Derive New Quiz questions without returning Canvas identifiers."""
    from api.powergrader import scoring_apply

    by_id = {(str(st.get("pseudonym") or ""), str(response.get("item_id") or "")): response
             for st in (bundle.get("students") or [])
             for response in (st.get("responses") or [])}
    reverse = {label: uid for uid, label in names.items()}
    expected_keys = set(by_id)
    received_keys = {(str(row.get("pseudonym") or ""), str(row.get("item_id") or "")) for row in rows}
    above, missing, tainted, overwrites = set(), set(), set(), set()
    for row in rows:
        pseudonym_value = str(row.get("pseudonym") or "")
        response = by_id.get((pseudonym_value, str(row.get("item_id") or "")), {})
        identity = vault.reverse(pseudonym_value) or {}
        uid = str(identity.get("canvas_id") or "")
        score = row.get("score")
        if score is None:
            missing.add(uid)
        elif isinstance(score, (int, float)) and isinstance(response.get("possible"), (int, float)) and score > response["possible"]:
            above.add(uid)
        if any(name and name in str(row.get("feedback") or "") for name in pseudonyms):
            tainted.add(uid)
        for student in session.get("students") or []:
            if str(student.get("user_id") or "") != uid:
                continue
            if any(
                str(item.get("item_id") or "") == str(row.get("item_id") or "")
                and item.get("earned_score") is not None
                and str(item.get("status") or "").strip().casefold() not in {
                    "", "notgraded", "not_graded", "ungraded",
                }
                for item in student.get("new_quiz_items") or []
            ):
                overwrites.add(uid)
    sessions = {str(st.get("user_id")): st for st in session.get("students") or []}
    held = {reverse.get(pseudonym_value, "") for pseudonym_value, _item_id
            in expected_keys - received_keys if reverse.get(pseudonym_value)}
    held.update(set(sessions) - {reverse.get(str(row.get("pseudonym") or ""), "") for row in rows})
    questions = []
    for kind, ids in (("score_above_possible", above), ("overwrites_existing_score", overwrites),
                      ("missing_score", missing), ("pseudonym_in_feedback", tainted),
                      ("held_not_scored", held)):
        if ids:
            options = list(scoring_apply.QUESTION_OPTIONS[kind])
            detail = kind.replace("_", " ")
            if kind == "missing_score":
                # New Quiz item finalization has no comment-only path. Keep
                # feedback-only rows held unless the teacher explicitly skips
                # them; never advertise the ordinary-assignment comment lane.
                options = ["skip_those"]
                detail = (
                    "These New Quiz items have feedback but no score. "
                    "Comment-only posting is unavailable; skip to hold the "
                    "affected student's result."
                )
            questions.append({"id": kind, "kind": kind, "user_ids": sorted(ids),
                              "pseudonyms": sorted(names.get(uid, "(unknown student)") for uid in ids),
                              "detail": detail, "options": options})
    return questions


def _new_quiz_public_questions(questions: list[dict], names: dict) -> list[dict]:
    return [{"id": q["id"], "detail": q["detail"],
             "students": sorted(names.get(uid, "(unknown student)") for uid in q["user_ids"]),
             "answer_with": q["options"]} for q in questions]


def _resolve_scoring_answers(questions: list[dict], answers: dict | None) -> dict:
    answers = {str(key): str(value) for key, value in (answers or {}).items()}
    if any(question["id"] not in answers for question in questions):
        return {"ok": False, "code": "unanswered_questions"}
    for question in questions:
        if answers[question["id"]] not in question["options"]:
            return {"ok": False, "code": "invalid_answer"}
        if answers[question["id"]] == "stop":
            return {"ok": True, "stop": True}
    return {"ok": True, "skip_pseudonyms": {
        pseudonym for question in questions
        if answers[question["id"]] == "skip_those"
        for pseudonym in question.get("pseudonyms") or []}}


# ---------------------------------------------------------------------------
# Private New Quiz item-finalization machinery used by submit_scoring_results.
#
# The helper freezes per-student reviews; finalization replays only those
# private coordinates. The write itself reuses
# session_actions.review_new_quiz_finalization / finalize_new_quiz /
# converge_new_quiz_after_finalize unchanged -- the same guarded,
# drift-checked, receipt-backed lane.
# Frozen review tokens are stashed on the session itself, keyed by a
# generated operation_id, never in the Operation Ledger (D10): the session
# is already the locked unit of state here, and nothing else needs a ledger
# record for a New Quiz item score.
# ---------------------------------------------------------------------------


def _new_quiz_scored_decisions(student: dict) -> list[dict]:
    """Item decisions for review_new_quiz_finalization/finalize_new_quiz,
    built from the exact SAFE result rows submitted for this student
    (student["ai_item_results"], each {item_id, score, feedback}). Only
    items carrying a score are included, so a session that only scores the
    essay items on a mixed New Quiz never touches the auto-graded ones.
    teacher_feedback stays empty: the result carries agent-authored feedback."""
    decisions = []
    for item in student.get("ai_item_results") or []:
        if item.get("score") is None:
            continue
        decisions.append({
            "item_id": str(item.get("item_id") or ""),
            "score": item.get("score"),
            "teacher_feedback": "",
            "ta_feedback": str(item.get("feedback") or ""),
        })
    return decisions


def _new_quiz_operation_digest(operation_id: str, ready_students: dict) -> str:
    fingerprint = {
        user_id: {
            "decisions": entry["decisions"],
            "review_token": (entry.get("pending") or {}).get("token"),
        }
        for user_id, entry in ready_students.items()
    }
    raw = json.dumps(
        {"operation_id": operation_id, "students": fingerprint},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_quiz_preflight(session: dict, student: dict, decisions: list[dict]) -> dict:
    from api.powergrader import new_quiz_grader

    return new_quiz_grader.preflight(
        canvas_base=config.get_canvas_base(), token=config.get_token(),
        assignment_id=str(session["assignment_id"]), user_id=str(student["user_id"]),
        decisions=decisions, http_session=_new_quiz_http_session(),
    )


def _new_quiz_apply(session: dict, student: dict, decisions: list[dict], pending: dict) -> dict:
    from api.powergrader import new_quiz_grader

    return new_quiz_grader.apply(
        canvas_base=config.get_canvas_base(), token=config.get_token(),
        assignment_id=str(session["assignment_id"]), user_id=str(student["user_id"]),
        decisions=decisions, baseline=pending, http_session=_new_quiz_http_session(),
    )


@contextmanager
def _new_quiz_http_scope():
    """Keep one teacher web session for every student in one Scoring Session write."""
    import requests

    http = requests.Session()
    token = _NEW_QUIZ_HTTP.set(http)
    try:
        yield http
    finally:
        _NEW_QUIZ_HTTP.reset(token)
        http.close()


def _new_quiz_http_session():
    return _NEW_QUIZ_HTTP.get()


# finalize_new_quiz's own apply(...) exception path already refuses to retry
# an ambiguous or rejected write through the same frozen review token (it
# invalidates the session's live pending_new_quiz_review specifically so a
# second call cannot try the same POST again). Naively re-injecting our own
# stashed copy of that token on every finalize call would defeat
# that protection, so a student whose finalize_new_quiz failure code names an
# actual Canvas write attempt is dropped from OUR stash too -- replaying the
# same operation_id can no longer reach that student's write again. A refusal
# that never touched Canvas (mismatched/expired review, unresolved
# provenance, SpeedGrader-only evidence) carries no such risk and is left in
# place, since retrying it is safe, if usually futile without a fresh preview.
_NEW_QUIZ_UNVERIFIED_WRITE_CODES = {"write_rejected", "write_unknown", "write_unverified"}


def _new_quiz_notify_write_through(session: dict, pushed) -> None:
    """Best-effort write-through mirror refresh after one verified New Quiz
    finalize.

    Request the existing narrow mirror refresh after a verified write. The
    MCP boundary never receives the live Canvas response.
    """
    try:
        course_id = (session or {}).get("course_id")
        if course_id and pushed:
            mirror_service.notify_course_changed(course_id)
    except Exception as exc:
        operational_log.emit("mirror.notify_course_changed", "failed", error_class=type(exc))


def _prepare_new_quiz_finalization(scoring_session_id: str) -> dict:
    """Privately preflight submitted New Quiz item scores for finalization.

    Frozen review tokens are stashed on the session under a new operation_id.
    This is an internal helper and is not exposed as a tool.

    Returns on success: operation_id and review_digest (both opaque; pass
    both, unchanged, to _finalize_new_quiz_results), and counts: students
    (with a submitted item score), items (distinct item_id across them), ready (froze
    cleanly), refused, and already_finalized (already landed in an earlier
    finalize). warnings names a refused student only by pseudonym and
    reason, never a real name or Canvas/SIS id.

    Refuses cleanly, with no Canvas call, when: the session is not found,
    the course is not a Current course, this session has no New Quiz
    item-finalization lane, or no student carries a staged item score.
    Never raises.
    """
    from api.powergrader import session_actions, session_store

    # The MCP-visible name is scoring_session_id; below this boundary the
    # session-store concept, and the operation_id composite built further
    # down, stay session_id (locked decisions 3 and 5).
    session_id = scoring_session_id

    session = session_store.load_session(session_id)
    if not session:
        return {"ok": False, "error": "Session not found."}

    gate_err = _course_gate_check(str(session.get("course_id") or ""))
    if gate_err:
        return {"ok": False, "error": gate_err}

    if not session.get("new_quiz_item_finalization_supported"):
        return {"ok": False, "error": "This session does not support New Quiz item finalization."}

    candidates = []
    for student in session.get("students") or []:
        decisions = _new_quiz_scored_decisions(student)
        if decisions:
            candidates.append((student, decisions))
    if not candidates:
        return {
            "ok": False,
            "error": "No submitted item scores are ready for finalization.",
        }

    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}

    item_ids: set = set()
    already_finalized = 0
    ready_students: dict = {}
    warnings: list = []
    refused_reasons: dict = {}

    with _vault_transaction(vault):
        for student, decisions in candidates:
            user_id = str(student.get("user_id") or "")
            pseudonym_value = vault.get_or_assign(user_id)
            for decision in decisions:
                item_ids.add(decision["item_id"])
            if student.get("new_quiz_finalized"):
                already_finalized += 1

            with session_store.session_lock(session_id):
                payload, _status = session_actions.review_new_quiz_finalization(
                    session_id, user_id=user_id, decisions_json=json.dumps(decisions),
                    load_session=session_store.load_session,
                    save_session=session_store.save_session,
                    preflight=_new_quiz_preflight,
                )
                pending = None
                if payload.get("ok"):
                    fresh = session_store.load_session(session_id) or {}
                    pending = dict(fresh.get("pending_new_quiz_review") or {})

            if pending is not None:
                ready_students[user_id] = {
                    "pseudonym": pseudonym_value, "pending": pending, "decisions": decisions,
                }
            else:
                code = str(payload.get("code") or "refused")
                refused_reasons[code] = refused_reasons.get(code, 0) + 1
                warnings.append({
                    "pseudonym": pseudonym_value, "code": code,
                    "reason": payload.get("error") or code,
                })

    if not ready_students:
        result = {"ok": False, "error": "No student is ready to finalize.", "warnings": warnings}
        return pseudonym.gate(result, vault)

    operation_id = f"{session_id}::{secrets.token_urlsafe(16)}"
    review_digest = _new_quiz_operation_digest(operation_id, ready_students)

    with session_store.session_lock(session_id):
        session = session_store.load_session(session_id) or session
        session.setdefault("new_quiz_scoring_operations", {})[operation_id] = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "review_digest": review_digest,
            "course_id": session.get("course_id"),
            "assignment_id": session.get("assignment_id"),
            "students": ready_students,
        }
        session_store.save_session(session)

    result = {
        "ok": True,
        "operation_id": operation_id,
        "review_digest": review_digest,
        "counts": {
            "students": len(candidates),
            "items": len(item_ids),
            "ready": len(ready_students),
            "refused": sum(refused_reasons.values()),
            "already_finalized": already_finalized,
        },
        "warnings": warnings,
    }
    if refused_reasons:
        result["refused_reasons"] = refused_reasons
    return pseudonym.gate(result, vault)


def _finalize_new_quiz_results(operation_id: str, review_digest: str) -> dict:
    """Apply the exact private New Quiz item-finalization review.

    Takes only the opaque operation_id/review_digest pair prepared internally;
    there is no scoring_session_id, course_id, or student parameter, so
    nothing here can reach any session, course, or student beyond the one
    already frozen.

    Replays each stashed per-student review token through finalize_new_quiz
    (the same guarded, drift-checked, receipt-backed item-finalization lane), then
    converges the gradebook write-through and New Quiz response-snapshot
    invalidation after each freshly verified finalize. A concluded or
    otherwise restricted enrollment can still refuse one student (Canvas 403
    per docs/reference/new-quizzes-grading-transport.md); that student's
    outcome is reported and the rest of the batch continues -- partial
    failure is the normal case, not a crash.

    Idempotent: calling this again with the same operation_id and
    review_digest never re-applies a student whose decisions already
    finalized; that student reports status "already_applied" and Canvas is
    not written again. A student whose review token has since expired
    reports status "failed" with code "review_expired" rather than crashing
    or silently skipping; create a fresh Scoring Session before retrying that student.
    A student whose write came back ambiguous or rejected is likewise never
    retried through this same operation_id (finalize_new_quiz's own rule);
    create a fresh Scoring Session before retrying that student too.

    Returns operation_id, counts (finalized, already_applied, failed), and a
    results list keyed only by pseudonym, never a real name or Canvas/SIS id.

    Refuses cleanly, with no Canvas call, when operation_id is not one
    private review was minted, or review_digest does not match the
    frozen review. Never raises.
    """
    from api.powergrader import session_actions, session_store

    session_id, _sep, suffix = str(operation_id or "").partition("::")
    if not session_id or not suffix:
        return {"ok": False, "error": "The private finalization review is unavailable."}

    session = session_store.load_session(session_id)
    if not session:
        return {"ok": False, "error": "The private finalization review is unavailable."}

    gate_err = _course_gate_check(str(session.get("course_id") or ""))
    if gate_err:
        return {"ok": False, "error": gate_err}

    stash = (session.get("new_quiz_scoring_operations") or {}).get(operation_id)
    if not stash:
        return {"ok": False, "error": "The private finalization review is unavailable."}
    if str(review_digest or "") != stash.get("review_digest"):
        return {"ok": False, "error": "The private finalization review does not match."}

    vault, vault_err = _open_vault()
    if vault_err:
        return {"ok": False, "error": vault_err}

    results = []
    finalized = already_applied = failed = 0

    for user_id, entry in list((stash.get("students") or {}).items()):
        pseudonym_value = entry.get("pseudonym")
        pending = entry.get("pending") or {}
        decisions = entry.get("decisions") or []

        with session_store.session_lock(session_id):
            current = session_store.load_session(session_id)
            if current is None:
                payload = {"ok": False, "code": "session_missing", "error": "Session not found."}
            else:
                current["pending_new_quiz_review"] = dict(pending)
                session_store.save_session(current)
                payload, _status = session_actions.finalize_new_quiz(
                    session_id, user_id=user_id, review_token=str(pending.get("token") or ""),
                    decisions_json=json.dumps(decisions),
                    load_session=session_store.load_session,
                    save_session=session_store.save_session,
                    apply=_new_quiz_apply,
                )

        status = payload.get("status")
        if payload.get("ok") and status == "finalized":
            finalized += 1
            reloaded = session_store.load_session(session_id)
            if reloaded is not None:
                session_actions.converge_new_quiz_after_finalize(
                    reloaded, user_id, notify_write_through=_new_quiz_notify_write_through,
                )
            results.append({"pseudonym": pseudonym_value, "status": "finalized", "code": payload.get("code")})
        elif payload.get("ok") and status == "already_applied":
            already_applied += 1
            results.append({"pseudonym": pseudonym_value, "status": "already_applied", "code": payload.get("code")})
        else:
            failed += 1
            code = str(payload.get("code") or "failed")
            results.append({
                "pseudonym": pseudonym_value, "status": "failed",
                "code": code, "error": payload.get("error"),
            })
            if code in _NEW_QUIZ_UNVERIFIED_WRITE_CODES:
                with session_store.session_lock(session_id):
                    current = session_store.load_session(session_id)
                    live_stash = ((current or {}).get("new_quiz_scoring_operations") or {}).get(operation_id)
                    if live_stash is not None:
                        (live_stash.get("students") or {}).pop(user_id, None)
                        session_store.save_session(current)

    result = {
        "ok": failed == 0,
        "operation_id": operation_id,
        "counts": {"finalized": finalized, "already_applied": already_applied, "failed": failed},
        "results": results,
    }
    return pseudonym.gate(result, vault)


def _scoring_apply_safe(plan: dict, names: dict) -> dict:
    """Re-express a user_id-keyed plan in pseudonyms only.

    The powergrader layer speaks Canvas user_id. Nothing below this line may
    cross the MCP boundary, including inside question and error text.
    """
    def label(user_id: str) -> str:
        return names.get(str(user_id)) or "(unknown student)"

    return {
        "students": sorted(label(uid) for uid in plan["candidate_ids"]),
        "questions": [
            {
                "id": question["id"],
                "detail": question["detail"],
                "students": sorted(label(uid) for uid in question["user_ids"]),
                "answer_with": question["options"],
            }
            for question in plan["questions"]
        ],
        "notes": plan["notes"],
    }
