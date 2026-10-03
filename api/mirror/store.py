"""CanvasMirror on-disk store — envelope-wrapped collections of Canvas facts.

Layout, under ``%LOCALAPPDATA%\CanvasExpert\cache\Canvas Mirror\<course_id>\``:

  _sync.v1.json                    pass envelopes + delta watermarks
  roster.v1.json                   students + sections
  groups.v1.json                   private group categories/memberships
  assignments.v1.json              slim Canvas-shaped assignment index
  submissions/<assignment_id>.v1.json   per-student current row + attempts

Conventions follow ``api/course_catalog.py``: versioned filenames, exact-key
validation, catalog-style envelopes, atomic same-directory replace, and
per-course in-process locks. Two deliberate simplifications, both justified
by design law #1 (the mirror is disposable, Canvas is truth): there is no
previous-file promotion, and an unreadable or invalid file is treated as
absent — the next sync pass rewrites it.

Attempt history is append-only within a living submission: a merge may add
attempts and update ``current``, but never drops an attempt while its
submission row is retained. Merges are idempotent so watermark-overlap
duplicates from the sync engine are harmless.

Pure stdlib + storage_support; no Canvas imports. Store operations accept an
optional workspace ``root`` for the matching identity vault; mirror documents
always use the machine-local cache.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from api.storage_support import atomic_write_json
from api.platform_services import workspace
from api import feedback_scrub


MIRROR_VERSION = 1
SYNC_FILENAME = "_sync.v1.json"
REFRESH_VERSION = 1
REFRESH_FILENAME = "_refresh.v1.json"
REFRESH_STATES = {"syncing", "synced", "failed"}
ROSTER_FILENAME = "roster.v1.json"
GROUPS_FILENAME = "groups.v1.json"
ASSIGNMENTS_FILENAME = "assignments.v1.json"
SUBMISSIONS_DIRNAME = "submissions"

# Course lifecycle is a narrow, student-free scheduling scope.  It deliberately
# lives beside (rather than inside) _sync.v1.json: pass readers rely on that
# file's exact schema, while lifecycle has independent refresh and last-good
# semantics.
COURSE_CONTEXT_VERSION = 1
COURSE_CONTEXT_FILENAME = "course_context.v1.json"
COURSE_CONTEXT_STATES = {"current", "stale", "unavailable"}
LIFECYCLE_STATES = {"current", "concluded", "unknown"}
ENROLLMENT_STATES = {"active", "invited", "completed", "inactive"}
CONTEXT_ERROR_CODES = {"", "unauthorized", "forbidden", "not_found",
                       "rate_limited", "timeout", "connection",
                       "invalid_response", "storage"}

# Collection files are written only on successful acquisition, so their state
# is "current" (or "incomplete" when pagination dropped records). Failures are
# recorded in _sync.v1.json pass envelopes; old collection files just age.
COLLECTION_STATES = {"current", "incomplete"}
PASS_NAMES = ("full", "delta", "roster")
PASS_STATES = {"current", "stale", "unavailable"}

_ENVELOPE_KEYS = {"state", "last_success_at", "last_attempt_at", "error_code"}
_SYNC_KEYS = {"schema_version", "course_id", "passes", "watermarks"}
_REFRESH_KEYS = {
    "schema_version", "course_id", "operation_id", "state",
    "requested_at", "started_at", "finished_at", "error_code",
    "revision", "snapshot_id",
}
_WATERMARK_KEYS = {"submitted_since", "graded_since"}
_ROSTER_KEYS = {"schema_version", "course_id", "students", "sections"} | _ENVELOPE_KEYS
_GROUPS_KEYS = {"schema_version", "course_id", "categories"} | _ENVELOPE_KEYS
_ASSIGNMENTS_KEYS = {"schema_version", "course_id", "assignments"} | _ENVELOPE_KEYS
_SUBMISSIONS_KEYS = {"schema_version", "course_id", "assignment_id",
                     "submissions"} | _ENVELOPE_KEYS
_SUBMISSION_ENTRY_KEYS = {"current", "attempts"}
_COURSE_CONTEXT_KEYS = {
    "schema_version", "course_id", "state", "last_success_at",
    "last_attempt_at", "error_code", "lifecycle", "course_workflow_state",
    "course_concluded", "course_end_at", "term_end_at", "enrollment_states",
}

# New Quiz metadata-scope capability record (1.0beta slice 01a). Student-free:
# just enough to gate the sync_metadata fan-out per course. Kept as its own
# small file (same course_dir/course_lock/atomic-write conventions as the rest
# of this module) instead of widening _sync.v1.json, so this slice never has
# to bump MIRROR_VERSION or touch the exact-key validation every other reader
# of _sync.v1.json depends on.
NEW_QUIZ_CAPABILITY_VERSION = 1
NEW_QUIZ_CAPABILITY_FILENAME = "new_quiz_capability.v1.json"
CAPABILITY_STATES = {"supported", "restricted", "unknown"}  # "unsupported" reserved, unused
_CAPABILITY_KEYS = {"schema_version", "course_id", "capability", "last_probe_at",
                    "retry_after", "evidence"}
_EVIDENCE_KEYS = {"category", "consecutive_failures"}
_EVIDENCE_CATEGORIES = {"", "forbidden", "unauthorized"}

# Submission comments freshness sidecar (1.0beta Batch 6 / 01). Kept as its
# own separate file rather than inside _sync.v1.json because comment freshness
# has a different cadence and boundary than pass envelopes: only the
# comment-bearing full pass can advance this state; delta, focused, roster, and
# group passes must never touch it. Follows the existing capability/late-policy
# sidecar pattern and course_lock / atomic_write conventions.
SUBMISSION_COMMENTS_STATE_VERSION = 1
SUBMISSION_COMMENTS_STATE_FILENAME = "submission_comments_state.v1.json"
SUBMISSION_COMMENTS_STATE_KEYS = {
    "schema_version", "course_id", "state",
    "last_success_at", "last_attempt_at", "error_code",
}
SUBMISSION_COMMENTS_STATES = {"current", "stale", "unavailable"}
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_hours(iso_z: str, now_iso_z: str | None = None) -> float | None:
    """Age of a store timestamp in hours, or None if empty/unparseable."""
    if not iso_z:
        return None
    try:
        then = datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = (datetime.strptime(now_iso_z, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
               if now_iso_z else datetime.now(timezone.utc))
    except ValueError:
        return None
    return (now - then).total_seconds() / 3600.0


# --- locks -------------------------------------------------------------------

_LOCKS_GUARD = threading.Lock()
_COURSE_LOCKS: dict[str, threading.RLock] = {}


def course_lock(course_id) -> threading.RLock:
    """In-process per-course lock (catalog pattern). Cross-process safety
    comes from atomic replace, not this lock."""
    with _LOCKS_GUARD:
        return _COURSE_LOCKS.setdefault(str(course_id), threading.RLock())


# --- machine-local mirror paths ---------------------------------------------

def course_dir(course_id):
    return workspace.course_mirror_dir(course_id)


def sync_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, SYNC_FILENAME) if directory else None


def refresh_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, REFRESH_FILENAME) if directory else None


def roster_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, ROSTER_FILENAME) if directory else None


def groups_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, GROUPS_FILENAME) if directory else None


def assignments_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, ASSIGNMENTS_FILENAME) if directory else None


def submissions_dir(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, SUBMISSIONS_DIRNAME) if directory else None


def submission_path(course_id, assignment_id, root=None):
    directory = submissions_dir(course_id, root)
    if not directory:
        return None
    return os.path.join(directory, f"{workspace.safe_id(assignment_id)}.v1.json")


def new_quiz_capability_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, NEW_QUIZ_CAPABILITY_FILENAME) if directory else None


def course_context_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, COURSE_CONTEXT_FILENAME) if directory else None


def submission_comments_state_path(course_id, root=None):
    directory = course_dir(course_id)
    return os.path.join(directory, SUBMISSION_COMMENTS_STATE_FILENAME) if directory else None


def _require_dir(course_id, root):
    if (root is None and not workspace.workspace_root()) or (root is not None and not root):
        raise ValueError("workspace not configured — no mirror location")
    directory = course_dir(course_id)
    if not directory:
        raise ValueError("workspace not configured — no mirror location")
    return directory


def _identity_vault(root=None):
    """Open the one vault paired with this workspace root."""
    from api.identity_vault_service import open_vault
    return open_vault(root)


@contextmanager
def _vault_transaction(root=None):
    vault = _identity_vault(root)
    with vault.transaction():
        yield vault


def _pseudonym_for(vault, canvas_id) -> str:
    return vault.get_or_assign(str(canvas_id))


def _real_id_for(vault, pseudonym: str) -> str:
    entry = vault.reverse(str(pseudonym))
    return str(entry.get("canvas_id")) if entry else str(pseudonym)


def _rehydrate_student(vault, pseudonym: str, stored: dict) -> dict:
    identity = vault.reverse(str(pseudonym)) or {}
    real_name = str(identity.get("real_name") or "")
    nicknames = []
    for entry in vault.entries():
        if str(entry.get("canvas_id")) == str(identity.get("canvas_id")):
            nicknames = list(entry.get("nicknames") or [])
            break
    sortable_name = real_name
    if "," not in real_name and len(real_name.split()) >= 2:
        parts = real_name.split()
        sortable_name = f"{parts[-1]}, {' '.join(parts[:-1])}"
    return {
        "id": str(identity.get("canvas_id") or pseudonym),
        "name": real_name,
        "sortable_name": sortable_name,
        "short_name": nicknames[0] if nicknames else real_name,
        "sis_user_id": str(identity.get("sis_id") or ""),
        "enrollments": [dict(item) for item in stored.get("enrollments") or []],
    }


def _projection_vault(root=None):
    """The vault a private roster/groups projection needs, or ``None``.

    Those documents are pseudonym-keyed at rest; without the vault they cannot
    become the real-identity projection their readers expect, so the read is
    unavailable rather than a pseudonym-keyed stand-in.
    """
    from api.identity_vault_service import IdentityVaultUnavailable
    try:
        return _identity_vault(root)
    except (IdentityVaultUnavailable, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _rehydrate_roster(document: dict | None, root=None) -> dict | None:
    if document is None:
        return None
    vault = _projection_vault(root)
    if vault is None:
        return None
    result = dict(document)
    result["students"] = {
        _real_id_for(vault, pseudonym): _rehydrate_student(vault, pseudonym, stored)
        for pseudonym, stored in document.get("students", {}).items()
    }
    return result


def _rehydrate_groups(document: dict | None, root=None) -> dict | None:
    if document is None:
        return None
    vault = _projection_vault(root)
    if vault is None:
        return None
    result = dict(document)
    categories = []
    for category in document.get("categories", []):
        updated = dict(category)
        groups = []
        for group in category.get("groups", []):
            updated_group = dict(group)
            memberships = []
            for membership in group.get("memberships", []):
                updated_membership = dict(membership)
                updated_membership["user_id"] = _real_id_for(
                    vault, membership.get("user_id", "")
                )
                memberships.append(updated_membership)
            updated_group["memberships"] = memberships
            groups.append(updated_group)
        updated["groups"] = groups
        categories.append(updated)
    result["categories"] = categories
    return result


def _rehydrate_submissions(document: dict | None, root=None) -> dict | None:
    if document is None:
        return None
    try:
        vault = _identity_vault(root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return document
    result = dict(document)
    submissions = {}
    for pseudonym, entry in document.get("submissions", {}).items():
        updated = {"current": dict(entry["current"]),
                   "attempts": dict(entry["attempts"])}
        updated["current"]["user_id"] = _real_id_for(vault, pseudonym)
        comments = []
        for comment in updated["current"].get("submission_comments") or []:
            restored = dict(comment)
            if restored.get("author_id"):
                restored["author_id"] = _real_id_for(vault, restored["author_id"])
            comments.append(restored)
        if "submission_comments" in updated["current"]:
            updated["current"]["submission_comments"] = comments
        submissions[_real_id_for(vault, pseudonym)] = updated
    result["submissions"] = submissions
    return result


# --- validation ---------------------------------------------------------------

def _require_exact_keys(value, keys: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} keys are invalid")


def _validate_envelope(document: dict, label: str, states: set[str]) -> None:
    if document.get("state") not in states:
        raise ValueError(f"{label} state is invalid")
    for key in ("last_success_at", "last_attempt_at", "error_code"):
        if not isinstance(document.get(key), str):
            raise ValueError(f"{label} {key} is invalid")


def _validate_common(document: dict, keys: set[str], course_id, label: str) -> None:
    _require_exact_keys(document, keys, label)
    if document.get("schema_version") != MIRROR_VERSION:
        raise ValueError(f"{label} schema_version is unsupported")
    if str(document.get("course_id")) != str(course_id):
        raise ValueError(f"{label} course_id mismatch")
    _validate_envelope(document, label, COLLECTION_STATES)


def _valid_iso_z(value) -> bool:
    if value == "":
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def validate_roster(document: dict, course_id) -> dict:
    _validate_common(document, _ROSTER_KEYS, course_id, "roster")
    if not isinstance(document.get("students"), dict) or not isinstance(document.get("sections"), dict):
        raise ValueError("roster collections are invalid")
    for pseudonym, student in document["students"].items():
        if (not isinstance(pseudonym, str) or not pseudonym.strip()
                or not isinstance(student, dict)
                or set(student) != {"enrollments"}
                or not isinstance(student["enrollments"], list)):
            raise ValueError("roster students must be pseudonym-keyed")
        for enrollment in student["enrollments"]:
            if (not isinstance(enrollment, dict)
                    or set(enrollment) != {"course_section_id"}
                    or not isinstance(enrollment["course_section_id"], str)):
                raise ValueError("roster enrollment is invalid")
    return document


def _validate_groups_categories(categories) -> None:
    if not isinstance(categories, list):
        raise ValueError("groups categories are invalid")
    category_ids = set()
    for category in categories:
        _require_exact_keys(category, {"category_id", "category_name", "groups"}, "group category")
        if not isinstance(category["category_id"], str) or not isinstance(category["category_name"], str):
            raise ValueError("group category identity is invalid")
        if category["category_id"] in category_ids:
            raise ValueError("group category id is duplicated")
        category_ids.add(category["category_id"])
        if not isinstance(category["groups"], list):
            raise ValueError("group category groups are invalid")
        group_ids = set()
        for group in category["groups"]:
            _require_exact_keys(group, {"id", "name", "memberships"}, "group")
            if not isinstance(group["id"], str) or not isinstance(group["name"], str):
                raise ValueError("group identity is invalid")
            if group["id"] in group_ids:
                raise ValueError("group id is duplicated")
            group_ids.add(group["id"])
            if not isinstance(group["memberships"], list):
                raise ValueError("group memberships are invalid")
            membership_ids = set()
            user_ids = set()
            for membership in group["memberships"]:
                _require_exact_keys(membership, {"id", "user_id"}, "group membership")
                if not isinstance(membership["id"], str) or not isinstance(membership["user_id"], str):
                    raise ValueError("group membership identity is invalid")
                if membership["id"] in membership_ids or membership["user_id"] in user_ids:
                    raise ValueError("group membership is duplicated")
                membership_ids.add(membership["id"])
                user_ids.add(membership["user_id"])


def validate_groups(document: dict, course_id) -> dict:
    _require_exact_keys(document, _GROUPS_KEYS, "groups")
    if document.get("schema_version") != MIRROR_VERSION:
        raise ValueError("groups schema_version is unsupported")
    if str(document.get("course_id")) != str(course_id):
        raise ValueError("groups course_id mismatch")
    _validate_envelope(document, "groups", PASS_STATES)
    _validate_groups_categories(document["categories"])
    return document


def validate_assignments(document: dict, course_id) -> dict:
    _validate_common(document, _ASSIGNMENTS_KEYS, course_id, "assignments")
    if not isinstance(document.get("assignments"), dict):
        raise ValueError("assignments collection is invalid")
    return document


def validate_submissions(document: dict, course_id, assignment_id) -> dict:
    _validate_common(document, _SUBMISSIONS_KEYS, course_id, "submissions")
    if str(document.get("assignment_id")) != str(assignment_id):
        raise ValueError("submissions assignment_id mismatch")
    entries = document.get("submissions")
    if not isinstance(entries, dict):
        raise ValueError("submissions collection is invalid")
    for user_id, entry in entries.items():
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError(f"submission {user_id} pseudonym is invalid")
        _require_exact_keys(entry, _SUBMISSION_ENTRY_KEYS, f"submission {user_id}")
        if not isinstance(entry["current"], dict) or not isinstance(entry["attempts"], dict):
            raise ValueError(f"submission {user_id} shape is invalid")
        if entry["current"].get("user_id") != user_id:
            raise ValueError(f"submission {user_id} identity is invalid")
    return document


def validate_new_quiz_capability(document: dict, course_id) -> dict:
    _require_exact_keys(document, _CAPABILITY_KEYS, "new quiz capability")
    if document.get("schema_version") != NEW_QUIZ_CAPABILITY_VERSION:
        raise ValueError("new quiz capability schema_version is unsupported")
    if str(document.get("course_id")) != str(course_id):
        raise ValueError("new quiz capability course_id mismatch")
    if document.get("capability") not in CAPABILITY_STATES:
        raise ValueError("new quiz capability state is invalid")
    for key in ("last_probe_at", "retry_after"):
        if not isinstance(document.get(key), str):
            raise ValueError(f"new quiz capability {key} is invalid")
    evidence = document.get("evidence")
    _require_exact_keys(evidence, _EVIDENCE_KEYS, "new quiz capability evidence")
    if evidence.get("category") not in _EVIDENCE_CATEGORIES:
        raise ValueError("new quiz capability evidence category is invalid")
    failures = evidence.get("consecutive_failures")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
        raise ValueError("new quiz capability evidence consecutive_failures is invalid")
    return document


def validate_course_context(document: dict, course_id) -> dict:
    """Validate the lifecycle scheduling record's exact student-free shape."""
    _require_exact_keys(document, _COURSE_CONTEXT_KEYS, "course context")
    if (not isinstance(document.get("schema_version"), int)
            or isinstance(document.get("schema_version"), bool)
            or document["schema_version"] != COURSE_CONTEXT_VERSION):
        raise ValueError("course context schema_version is unsupported")
    if not isinstance(document.get("course_id"), str) or document["course_id"] != str(course_id):
        raise ValueError("course context course_id mismatch")
    if document.get("state") not in COURSE_CONTEXT_STATES:
        raise ValueError("course context state is invalid")
    if document.get("error_code") not in CONTEXT_ERROR_CODES:
        raise ValueError("course context error_code is invalid")
    for key in ("last_success_at", "last_attempt_at", "course_end_at", "term_end_at"):
        if not _valid_iso_z(document.get(key)):
            raise ValueError(f"course context {key} is invalid")
    if document.get("lifecycle") not in LIFECYCLE_STATES:
        raise ValueError("course context lifecycle is invalid")
    if not isinstance(document.get("course_workflow_state"), str):
        raise ValueError("course context course_workflow_state is invalid")
    if not isinstance(document.get("course_concluded"), bool):
        raise ValueError("course context course_concluded is invalid")
    enrollment_states = document.get("enrollment_states")
    if (not isinstance(enrollment_states, list)
            or any(state not in ENROLLMENT_STATES for state in enrollment_states)
            or len(set(enrollment_states)) != len(enrollment_states)):
        raise ValueError("course context enrollment_states are invalid")
    return document


def validate_submission_comments_state(document: dict, course_id) -> dict:
    """Validate the submission comments freshness sidecar (1.0beta Batch 6)."""
    _require_exact_keys(document, SUBMISSION_COMMENTS_STATE_KEYS,
                        "submission comments state")
    if (not isinstance(document.get("schema_version"), int)
            or isinstance(document.get("schema_version"), bool)
            or document["schema_version"] != SUBMISSION_COMMENTS_STATE_VERSION):
        raise ValueError("submission comments state schema_version is unsupported")
    if not isinstance(document.get("course_id"), str) or document["course_id"] != str(course_id):
        raise ValueError("submission comments state course_id mismatch")
    if document.get("state") not in SUBMISSION_COMMENTS_STATES:
        raise ValueError("submission comments state is invalid")
    # error_code is a free-form sanitized string, not a closed set: this sidecar's
    # only writer (full_pass) reuses the same course_catalog.error_code() value
    # that also feeds the unrestricted-string full/delta pass envelope (record_pass /
    # _validate_envelope), so a narrower allowlist here would reject legitimate codes.
    if not isinstance(document.get("error_code"), str):
        raise ValueError("submission comments state error_code is invalid")
    for key in ("last_success_at", "last_attempt_at"):
        if not _valid_iso_z(document.get(key)):
            raise ValueError(f"submission comments state {key} is invalid")
    return document


def validate_sync(document: dict, course_id) -> dict:
    _require_exact_keys(document, _SYNC_KEYS, "sync")
    if document.get("schema_version") != MIRROR_VERSION:
        raise ValueError("sync schema_version is unsupported")
    if str(document.get("course_id")) != str(course_id):
        raise ValueError("sync course_id mismatch")
    passes = document.get("passes")
    _require_exact_keys(passes, set(PASS_NAMES), "sync passes")
    for name in PASS_NAMES:
        _require_exact_keys(passes[name], _ENVELOPE_KEYS, f"sync pass {name}")
        _validate_envelope(passes[name], f"sync pass {name}", PASS_STATES)
    _require_exact_keys(document.get("watermarks"), _WATERMARK_KEYS, "sync watermarks")
    for key in _WATERMARK_KEYS:
        if not isinstance(document["watermarks"][key], str):
            raise ValueError(f"sync watermark {key} is invalid")
    return document


def validate_refresh(document: dict, course_id) -> dict:
    _require_exact_keys(document, _REFRESH_KEYS, "refresh")
    if document["schema_version"] != REFRESH_VERSION:
        raise ValueError("refresh schema_version is unsupported")
    if str(document["course_id"]) != str(course_id):
        raise ValueError("refresh course_id mismatch")
    if document["state"] not in REFRESH_STATES:
        raise ValueError("refresh state is invalid")
    for key in ("requested_at", "started_at", "finished_at"):
        if document[key] and not _valid_iso_z(document[key]):
            raise ValueError(f"refresh {key} is invalid")
    if not isinstance(document["operation_id"], str) or not document["operation_id"]:
        raise ValueError("refresh operation_id is invalid")
    if not isinstance(document["error_code"], str):
        raise ValueError("refresh error_code is invalid")
    if not isinstance(document["revision"], int) or isinstance(document["revision"], bool) or document["revision"] < 0:
        raise ValueError("refresh revision is invalid")
    if not isinstance(document["snapshot_id"], str):
        raise ValueError("refresh snapshot_id is invalid")
    if document["state"] == "synced" and (document["revision"] < 1 or not document["snapshot_id"]):
        raise ValueError("synced refresh must identify a snapshot")
    return document


# --- read/write primitives -----------------------------------------------------

def _read_document(path, validator):
    """Read + validate, or None. The mirror is disposable (design law #1):
    a missing, unparseable, or invalid file is simply absent — the next sync
    pass rewrites it. Never raise out of a read."""
    if not path or not os.path.exists(workspace.extended_path(path)):
        return None
    try:
        with open(workspace.extended_path(path), encoding="utf-8") as handle:
            document = json.load(handle)
        return validator(document)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_document(path, document) -> dict:
    atomic_write_json(Path(path), document)
    return document


# --- normalizers (Canvas rows -> stored shapes) ---------------------------------

def normalize_student(user: dict) -> dict | None:
    """Keep exactly the fields existing consumers use (roster_service upsert,
    pseudonymize_roster). No emails, no avatars — privacy-lean at rest."""
    if not isinstance(user, dict) or user.get("id") in (None, ""):
        return None
    return {
        "id": str(user["id"]),
        "name": str(user.get("name") or ""),
        "sortable_name": str(user.get("sortable_name") or ""),
        "short_name": str(user.get("short_name") or ""),
        "sis_user_id": str(user.get("sis_user_id") or ""),
        "enrollments": [
            {"course_section_id": str(e.get("course_section_id") or "")}
            for e in (user.get("enrollments") or [])
            if isinstance(e, dict) and e.get("course_section_id")
        ],
    }


def normalize_group_categories(categories: list[dict], vault=None) -> list[dict]:
    """Copy only the private group fields Roster needs from its live loader.

    Group membership is FERPA-protected, so this deliberately rejects a
    malformed live result rather than keeping arbitrary Canvas response data.
    """
    if not isinstance(categories, list):
        raise ValueError("live group categories are invalid")
    normalized_categories = []
    category_ids = set()
    for category in categories:
        if not isinstance(category, dict) or category.get("category_id") in (None, ""):
            raise ValueError("live group category id is invalid")
        if not isinstance(category.get("category_name"), str) or not isinstance(category.get("groups"), list):
            raise ValueError("live group category is invalid")
        category_id = str(category["category_id"])
        if category_id in category_ids:
            raise ValueError("live group category id is duplicated")
        category_ids.add(category_id)
        normalized_groups = []
        group_ids = set()
        for group in category["groups"]:
            if not isinstance(group, dict) or group.get("id") in (None, ""):
                raise ValueError("live group id is invalid")
            if not isinstance(group.get("name"), str) or not isinstance(group.get("memberships"), list):
                raise ValueError("live group is invalid")
            group_id = str(group["id"])
            if group_id in group_ids:
                raise ValueError("live group id is duplicated")
            group_ids.add(group_id)
            memberships = []
            membership_ids = set()
            user_ids = set()
            for membership in group["memberships"]:
                if (not isinstance(membership, dict) or membership.get("id") in (None, "")
                        or membership.get("user_id") in (None, "")):
                    raise ValueError("live group membership is invalid")
                membership_id = str(membership["id"])
                user_id = str(membership["user_id"])
                if membership_id in membership_ids or user_id in user_ids:
                    raise ValueError("live group membership is duplicated")
                membership_ids.add(membership_id)
                user_ids.add(user_id)
                stored_user_id = (_pseudonym_for(vault, user_id)
                                  if vault is not None else user_id)
                memberships.append({"id": membership_id, "user_id": stored_user_id})
            normalized_groups.append({
                "id": group_id, "name": group["name"], "memberships": memberships,
            })
        normalized_categories.append({
            "category_id": category_id,
            "category_name": category["category_name"],
            "groups": normalized_groups,
        })
    return normalized_categories


def normalize_assignment(row: dict) -> dict | None:
    """Slim Canvas-shaped index — exactly what build_snapshot and the mirror
    queries need. The authoring catalog stays the rich source."""
    if not isinstance(row, dict) or row.get("id") in (None, ""):
        return None
    submission_types = row.get("submission_types") if isinstance(row.get("submission_types"), list) else []
    quiz_type = str(row.get("quiz_type") or "").lower()
    is_quiz_lti_assignment = row.get("is_quiz_lti_assignment") is True
    is_quiz = bool(
        "online_quiz" in submission_types
        or row.get("quiz_id") is not None
        or row.get("quiz_type") is not None
        or is_quiz_lti_assignment
    )
    if is_quiz_lti_assignment:
        quiz_kind = "new_quiz"
    elif "online_quiz" in submission_types:
        if "new_quiz" in quiz_type or quiz_type == "quizzes.next":
            quiz_kind = "new_quiz"
        else:
            external = row.get("external_tool_tag_attributes")
            hint = (f"{external.get('url') or ''} {external.get('content_type') or ''}"
                    if isinstance(external, dict) else "").lower()
            quiz_kind = "new_quiz" if "quiz" in hint or "new_quiz" in hint else "classic_quiz"
    elif row.get("quiz_id") is not None or row.get("quiz_type") is not None:
        quiz_kind = "quiz"
    else:
        quiz_kind = ""
    return {
        "id": str(row["id"]),
        "name": str(row.get("name") or ""),
        "due_at": str(row.get("due_at") or ""),
        "points_possible": row.get("points_possible"),
        "published": bool(row.get("published", True)),
        "html_url": str(row.get("html_url") or ""),
        "submission_types": [str(t) for t in (row.get("submission_types") or [])],
        "updated_at": str(row.get("updated_at") or ""),
        "description": row.get("description") if isinstance(row.get("description"), str) else "",
        # Student-free classification copied from the shared assignment receipt.
        # These fields let agent-facing preparation reject New Quizzes without
        # consulting Canvas or the private New Quiz response owner.
        "quiz_id": str(row.get("quiz_id") or ""),
        "is_quiz": is_quiz,
        "quiz_kind": quiz_kind,
        "is_quiz_lti_assignment": is_quiz_lti_assignment,
    }


def _attempt_record(entry: dict, replacement_map=None) -> dict | None:
    attempt = entry.get("attempt")
    submitted_at = entry.get("submitted_at")
    if not attempt or not submitted_at:
        return None
    record = {
        "attempt": int(attempt),
        "submitted_at": str(submitted_at),
        "submission_type": str(entry.get("submission_type") or ""),
        "body": (feedback_scrub.scrub_text(entry.get("body"), replacement_map)
                 if replacement_map is not None and isinstance(entry.get("body"), str)
                 else entry.get("body") if isinstance(entry.get("body"), str) else ""),
        "attachment_names": [
            str(a.get("filename") or a.get("display_name") or "")
            for a in (entry.get("attachments") or [])
            if isinstance(a, dict)
        ],
    }
    # URL contents are never retained. Capture a boolean only when Canvas
    # supplied the field, so old/sparse rows remain distinguishable from an
    # observed empty URL attempt.
    if "url" in entry:
        record["url_present"] = (isinstance(entry.get("url"), str)
                                 and bool(entry["url"].strip()))
    return record


def _comment_record(entry: dict, replacement_map=None, vault=None) -> dict:
    # author_role uses the same fallback chain as the work-registry
    # classifier (home_attention.author_role) so staff-authored comments
    # stay provably staff when served from the mirror. Role label only —
    # still no names, avatars, or attachments.
    author = entry.get("author") if isinstance(entry.get("author"), dict) else {}
    return {
        "id": str(entry.get("id") or ""),
        "author_id": (_pseudonym_for(vault, entry.get("author_id"))
                      if vault is not None and entry.get("author_id") not in (None, "")
                      else ""),
        "author_role": str(entry.get("author_role") or entry.get("author_type")
                           or author.get("role") or author.get("type") or ""),
        "comment": (feedback_scrub.scrub_text(str(entry.get("comment") or ""), replacement_map)
                    if replacement_map is not None else str(entry.get("comment") or "")),
        "created_at": str(entry.get("created_at") or ""),
    }


def normalize_submission(row: dict, vault=None) -> tuple[str, dict, dict] | None:
    """Map one Canvas submission row to ``(user_id, current, attempts)``.

    ``current`` keeps Canvas field names so mirror-backed queries can hand
    rows to existing consumers unchanged. ``attempts`` collects the row's
    ``submission_history`` (when fetched) plus the row itself, keyed by
    attempt number — so a delta fetched without history still records the
    newest attempt.
    """
    if not isinstance(row, dict) or row.get("user_id") in (None, ""):
        return None
    raw_user_id = str(row["user_id"])
    pseudonym = (_pseudonym_for(vault, raw_user_id) if vault is not None else raw_user_id)
    replacement_map = (feedback_scrub.build_replacement_map(vault.entries(), set())
                       if vault is not None else None)
    current = {
        "assignment_id": str(row.get("assignment_id") or ""),
        "user_id": pseudonym,
        "workflow_state": str(row.get("workflow_state") or ""),
        "submitted_at": row.get("submitted_at"),
        "graded_at": row.get("graded_at"),
        "score": row.get("score"),
        "entered_score": row.get("entered_score"),
        "points_deducted": row.get("points_deducted"),
        "late_policy_status": row.get("late_policy_status"),
        "late_days": row.get("late_days"),
        "grade": row.get("grade"),
        "late": bool(row.get("late")),
        "missing": bool(row.get("missing")),
        "excused": bool(row.get("excused")),
        # Per-student due/late facts (Batch 4): cached_due_date reflects the
        # student's overridden due date, seconds_late the raw lateness. Their
        # real consumers (late-catchup, sweep) live in the precision-grading
        # batch; kept nullable so a row that omits them stores None, not 0.
        "cached_due_date": row.get("cached_due_date"),
        "seconds_late": row.get("seconds_late"),
        "attempt": row.get("attempt"),
        "grade_matches_current_submission": row.get("grade_matches_current_submission"),
        "submission_type": str(row.get("submission_type") or ""),
        "body": (feedback_scrub.scrub_text(row.get("body"), replacement_map)
                 if replacement_map is not None and isinstance(row.get("body"), str)
                 else row.get("body") if isinstance(row.get("body"), str) else ""),
        "url": row.get("url") if isinstance(row.get("url"), str) else "",
        "submission_comments": [
            _comment_record(entry, replacement_map, vault) for entry in (row.get("submission_comments") or [])
            if isinstance(entry, dict)
        ],
    }
    from .attempt_text import digest as attempt_text_digest
    current["submission_digest"] = attempt_text_digest(current.get("body"))
    attempts: dict[str, dict] = {}
    # The row itself first, then history — a history entry for the same
    # attempt is richer (attachments, exact body) and must win.
    for entry in [row] + list(row.get("submission_history") or []):
        record = (_attempt_record(entry, replacement_map)
                  if isinstance(entry, dict) else None)
        if record is not None:
            attempts[str(record["attempt"])] = record
    return current["user_id"], current, attempts


# --- collection writers ----------------------------------------------------------

def _envelope(state: str, attempted_at: str) -> dict:
    return {"state": state, "last_success_at": attempted_at,
            "last_attempt_at": attempted_at, "error_code": ""}


def write_roster(course_id, users: list[dict], sections: dict, *,
                 root=None, attempted_at: str | None = None,
                 state: str = "current") -> dict:
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with _vault_transaction(root) as vault:
        from api import roster_service
        roster_service.upsert_roster(vault, users)
        students = {}
        for user in users or []:
            normalized = normalize_student(user)
            if normalized is None:
                continue
            pseudonym = _pseudonym_for(vault, normalized["id"])
            stored = {"enrollments": normalized["enrollments"]}
            existing = students.get(pseudonym)
            if existing is None:
                students[pseudonym] = stored
                continue
            # Canvas can list one user once per enrollment rather than once
            # with every enrollment attached; union the section memberships.
            seen = {item["course_section_id"] for item in existing["enrollments"]}
            for enrollment in stored["enrollments"]:
                if enrollment["course_section_id"] not in seen:
                    seen.add(enrollment["course_section_id"])
                    existing["enrollments"].append(enrollment)
        document = {
            "schema_version": MIRROR_VERSION,
            "course_id": str(course_id),
            **_envelope(state, attempted_at),
            "students": students,
            "sections": {str(k): str(v) for k, v in (sections or {}).items()},
        }
        with course_lock(course_id):
            return _write_document(roster_path(course_id, root),
                                   validate_roster(document, course_id))


def write_groups(course_id, categories: list[dict], *, root=None,
                 attempted_at: str | None = None, state: str = "current") -> dict:
    """Atomically replace the strict private group snapshot after a live read."""
    _require_dir(course_id, root)
    if state not in PASS_STATES:
        raise ValueError("groups state is invalid")
    attempted_at = attempted_at or now_iso()
    with _vault_transaction(root) as vault:
        document = {
            "schema_version": MIRROR_VERSION,
            "course_id": str(course_id),
            **_envelope(state, attempted_at),
            "categories": normalize_group_categories(categories, vault),
        }
        with course_lock(course_id):
            return _write_document(groups_path(course_id, root), validate_groups(document, course_id))


def build_assignments_document(course_id, rows: list[dict], *, attempted_at: str,
                               state: str = "current") -> dict:
    """Normalize + envelope-wrap the assignments document without writing it.

    Factored out of ``write_assignments`` so a caller (``_commit_assignment_index``
    in ``api/mirror/sync.py``) can build the candidate document, diff it
    against the previously committed one, and skip the actual write (and the
    OneDrive re-sync it triggers) when nothing but envelope timestamps would
    change.
    """
    assignments = {}
    for row in rows or []:
        normalized = normalize_assignment(row)
        if normalized is not None:
            assignments[normalized["id"]] = normalized
    return {
        "schema_version": MIRROR_VERSION,
        "course_id": str(course_id),
        **_envelope(state, attempted_at),
        "assignments": assignments,
    }


def write_assignments(course_id, rows: list[dict], *, root=None,
                      attempted_at: str | None = None,
                      state: str = "current") -> dict:
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    document = build_assignments_document(course_id, rows, attempted_at=attempted_at, state=state)
    with course_lock(course_id):
        return _write_document(assignments_path(course_id, root),
                               validate_assignments(document, course_id))


def merge_submissions(course_id, assignment_id, rows: list[dict], *,
                      root=None, attempted_at: str | None = None,
                      state: str = "current", replace: bool = False,
                      stream_get=None, canvas_origin="", capture_budget=None,
                      score_summary: dict | None = None) -> dict:
    """Merge Canvas submission rows into one assignment's mirror file.

    ``replace=False`` (delta): upsert the incoming users, keep everyone else.
    ``replace=True`` (full pass): the incoming rows define the user set —
    users no longer present are pruned — but attempts of retained users are
    carried over (append-only survives the rewrite).
    Idempotent: replaying the same rows yields an identical document.
    """
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with _vault_transaction(root) as vault:
        with course_lock(course_id):
            existing = _read_document(
                submission_path(course_id, assignment_id, root),
                lambda d: validate_submissions(d, course_id, assignment_id),
            )
            existing_entries = (existing or {}).get("submissions") or {}
            from . import submission_history
            # The mirror file is disposable. Seed it before any incoming row,
            # including sparse deltas, can replace its observed fields.
            if existing is not None:
                submission_history.capture_cached_document(
                    course_id, assignment_id, existing, root=root)
            # Persist URL-free metadata before committing the mirror. Actual
            # network streaming is retried below after both store locks exit.
            submission_history.capture_rows(
                course_id, assignment_id, rows or [], vault=vault, root=root,
                budget=capture_budget,
            )
            entries: dict[str, dict] = {} if replace else {
                user_id: {"current": dict(entry["current"]),
                          "attempts": dict(entry["attempts"])}
                for user_id, entry in existing_entries.items()
            }
            from api import score_ledger
            ledger_root = workspace.workspace_root()
            ledger_events = score_ledger.list_events(str(course_id), str(assignment_id), root=ledger_root)
            latest_observation = {}
            for event in ledger_events:
                is_mirror_observation = (event.get("source") in {"mirror_observed", "canvas_external"}
                    and event.get("action") in {"observed", "linked_ce_evidence", "external_change"})
                is_verified_ce_observation = (event.get("source") in {"ce_apply", "ce_curve", "ce_adjustment"}
                    and event.get("action") == "verified" and event.get("canvas_score") is not None)
                if not (is_mirror_observation or is_verified_ce_observation):
                    continue
                key = (str(event.get("student_id") or ""), str(event.get("attempt") or ""))
                if key not in latest_observation or str(event.get("timestamp") or "") >= str(latest_observation[key].get("timestamp") or ""):
                    latest_observation[key] = event
            evidence_changed = False
            external_count = 0
            for row in rows or []:
                normalized = normalize_submission(row, vault)
                if normalized is None:
                    continue
                user_id, current, attempts = normalized
                attempt_value = current.get("attempt")
                attempt_key = str(attempt_value or "")
                raw_student_id = str(row.get("user_id") or "")
                prior = latest_observation.get((raw_student_id, attempt_key))
                score_fact = (current.get("score"), current.get("entered_score"),
                    current.get("points_deducted"), current.get("late_policy_status"),
                    current.get("late_days"), current.get("submission_digest"))
                def same(left, right):
                    return tuple(left or ()) == tuple(right or ())
                prior_fact = ((prior.get("canvas_score"), prior.get("entered_score"),
                    prior.get("points_deducted"), prior.get("late_status"), prior.get("late_days"),
                    prior.get("submission_digest")) if prior else None)
                if prior and same(prior_fact, score_fact) and prior.get("source") in {"mirror_observed", "canvas_external"}:
                    # Replay of an unchanged refresh is not a new event.
                    pass
                else:
                    ce_candidates = [event for event in ledger_events
                        if event.get("source") in {"ce_apply", "ce_curve", "ce_adjustment"}
                        and event.get("action") in {"accepted", "verified", "unknown"}
                        and str(event.get("student_id") or "") == raw_student_id
                        and str(event.get("attempt") or "") == attempt_key
                        and event.get("entered_score") is not None]
                    ce_candidates.sort(key=lambda item: str(item.get("timestamp") or ""))
                    consumed = {str(event.get("origin_event_id")) for event in ledger_events
                                if event.get("source") in {"mirror_observed", "canvas_external"}
                                and event.get("origin_event_id")}
                    newer_verified = [event for event in ce_candidates
                        if event.get("action") == "verified" and event.get("canvas_score") is not None
                        and str(event.get("timestamp") or "") > str(attempted_at)]
                    stale_conflict = next((event for event in reversed(newer_verified)
                        if score_fact[1] is None
                        or abs(float(event.get("canvas_score")) - float(score_fact[0] or 0)) > 1e-6
                        or (event.get("entered_score") is not None and
                            abs(float(event["entered_score"]) - float(score_fact[1])) > 1e-6)), None)
                    if stale_conflict:
                        score_ledger.append_event({
                            "source": "mirror_observed", "action": "stale_snapshot",
                            "course_id": str(course_id), "assignment_id": str(assignment_id),
                            "student_id": raw_student_id, "attempt": attempt_value,
                            "entered_score": score_fact[1], "canvas_score": score_fact[0],
                            "submission_digest": current.get("submission_digest"),
                            "observation_at": str(attempted_at),
                            "origin_event_id": stale_conflict.get("event_id"),
                            "actor": "unknown",
                        }, idempotency_key=f"mirror-stale:{user_id}:{attempt_key}:{attempted_at}:{score_fact}",
                           root=ledger_root)
                        previous = entries.get(user_id) or existing_entries.get(user_id) or {}
                        merged_attempts = dict(previous.get("attempts") or {})
                        for attempt_num, incoming in attempts.items():
                            merged_attempts.setdefault(attempt_num, incoming)
                        entries[user_id] = {"current": dict(previous.get("current") or current),
                                            "attempts": merged_attempts}
                        ledger_events = score_ledger.list_events(str(course_id), str(assignment_id), root=ledger_root)
                        evidence_changed = True
                        continue
                    eligible_ce = [event for event in ce_candidates
                        if str(event.get("event_id") or "") not in consumed
                        and (not prior or str(event.get("timestamp") or "") >
                             str(prior.get("timestamp") or ""))]
                    matching_ce = (prior if prior and prior.get("source") in {"ce_apply", "ce_curve", "ce_adjustment"}
                        and prior.get("action") == "verified" and score_fact[1] is not None
                        and abs(float(prior.get("entered_score")) - float(score_fact[1])) <= 1e-6
                        and abs(float(prior.get("canvas_score")) - float(score_fact[0] or 0)) <= 1e-6
                        else next((event for event in reversed(eligible_ce)
                        if score_fact[1] is not None
                        and abs(float(event["entered_score"]) - float(score_fact[1])) <= 1e-6), None))
                    latest_ce = eligible_ce[-1] if eligible_ce else None
                    changed = (bool(prior and not same(prior_fact, score_fact))
                        or bool(not prior and latest_ce and score_fact[1] is not None
                                and abs(float(latest_ce["entered_score"]) - float(score_fact[1])) > 1e-6))
                    source = "canvas_external" if changed and matching_ce is None else "mirror_observed"
                    action = "external_change" if source == "canvas_external" else (
                        "linked_ce_evidence" if matching_ce else "observed")
                    new_event = score_ledger.append_event({
                        "source": source, "action": action,
                        "course_id": str(course_id), "assignment_id": str(assignment_id),
                        "student_id": str(row.get("user_id") or ""), "attempt": attempt_value,
                        "submission_digest": current.get("submission_digest"),
                        "entered_score": score_fact[1], "canvas_score": score_fact[0],
                        "old_entered_score": prior.get("entered_score") if prior else None,
                        "new_entered_score": score_fact[1],
                        "old_canvas_score": prior.get("canvas_score") if prior else None,
                        "new_canvas_score": score_fact[0], "actor": "unknown",
                        "points_deducted": score_fact[2], "late_status": score_fact[3],
                        "late_days": score_fact[4],
                        "old_points_deducted": prior.get("points_deducted") if prior else None,
                        "new_points_deducted": score_fact[2],
                        "old_late_status": prior.get("late_status") if prior else None,
                        "new_late_status": score_fact[3],
                        "old_late_days": prior.get("late_days") if prior else None,
                        "new_late_days": score_fact[4],
                        "observation_at": str(attempted_at),
                        "origin_event_id": matching_ce.get("event_id") if matching_ce else None,
                    }, idempotency_key=(f"mirror-score:{user_id}:{attempt_key}:"
                                        f"{prior.get('event_id') if prior else 'initial'}:{score_fact}"), root=ledger_root)
                    latest_observation[(raw_student_id, attempt_key)] = new_event
                    ledger_events.append(new_event)
                    evidence_changed = True
                    if source == "canvas_external":
                        external_count += 1
                previous = entries.get(user_id) or existing_entries.get(user_id) or {}
                merged_attempts = dict(previous.get("attempts") or {})
                for attempt_key, incoming in attempts.items():
                    prior = merged_attempts.get(attempt_key)
                    if prior:
                        merged = dict(prior)
                        if incoming.get("body"):
                            if not merged.get("body"):
                                merged["body"] = incoming["body"]
                            # Keep the first captured draft in the disposable
                            # projection; conflicting observations live in the
                            # durable history archive.
                        if incoming.get("attachment_names"):
                            merged["attachment_names"] = list(dict.fromkeys(
                                list(merged.get("attachment_names") or [])
                                + list(incoming["attachment_names"])))
                        if incoming.get("submission_type"):
                            merged["submission_type"] = incoming["submission_type"]
                        if "url_present" in incoming and "url_present" not in merged:
                            merged["url_present"] = incoming["url_present"]
                        merged_attempts[attempt_key] = merged
                    else:
                        merged_attempts[attempt_key] = incoming
                # A row fetched without submission_comments (delta; or a full
                # pass that omitted the include) must not erase comments a prior
                # pass already stored — carry them forward when this row is bare.
                if not current.get("submission_comments"):
                    previous_current = previous.get("current") or {}
                    if previous_current.get("submission_comments"):
                        current["submission_comments"] = previous_current["submission_comments"]
                entries[user_id] = {"current": current, "attempts": merged_attempts}
            # Deduplicated retries still reconcile the export snapshot after a
            # prior event publication whose export flush may have failed.
            score_ledger.flush_exports(str(course_id), str(assignment_id), root=ledger_root)
            if score_summary is not None:
                score_summary["canvas_external_count"] = external_count
            document = {
                "schema_version": MIRROR_VERSION,
                "course_id": str(course_id),
                "assignment_id": str(assignment_id),
                **_envelope(state, attempted_at),
                "submissions": entries,
            }
            written = _write_document(
                submission_path(course_id, assignment_id, root),
                validate_submissions(document, course_id, assignment_id))
    if stream_get is not None:
        # Keep Canvas I/O outside both the vault transaction and course lock.
        # Metadata is already durable, so a failed download is independently
        # visible and retryable without affecting the projection commit.
        submission_history.capture_rows(
            course_id, assignment_id, rows or [], vault=vault, root=root,
            stream_get=stream_get, canvas_origin=canvas_origin,
            budget=capture_budget,
        )
    return written


def prune_submission_files(course_id, keep_assignment_ids, *, root=None) -> list[str]:
    """Full-pass deletion true-up: remove mirror files for assignments that no
    longer exist in Canvas. Returns the removed assignment ids."""
    _require_dir(course_id, root)
    directory = submissions_dir(course_id, root)
    if not directory or not os.path.isdir(workspace.extended_path(directory)):
        return []
    keep = {f"{workspace.safe_id(a)}.v1.json" for a in keep_assignment_ids}
    removed = []
    with course_lock(course_id):
        for name in os.listdir(workspace.extended_path(directory)):
            if not name.endswith(".v1.json") or name in keep:
                continue
            try:
                assignment_id = name[: -len(".v1.json")]
                # Archive the stored projection directly. The public reader
                # rehydrates pseudonym keys into Canvas ids for local callers,
                # but the durable history manifest must retain only the
                # pseudonymous on-disk representation.
                document = _read_document(
                    submission_path(course_id, assignment_id, root),
                    lambda d: validate_submissions(d, course_id, assignment_id),
                )
                if document is not None:
                    from . import submission_history
                    submission_history.capture_cached_document(
                        course_id, assignment_id, document, root=root)
                os.remove(workspace.extended_path(os.path.join(directory, name)))
                removed.append(assignment_id)
            except Exception:
                # The disposable copy is the only remaining evidence if the
                # archive cannot be validated or written. Keep it in place.
                continue
    return sorted(removed)


# --- collection readers -----------------------------------------------------------

def read_roster(course_id, *, root=None) -> dict | None:
    document = _read_document(roster_path(course_id, root),
                              lambda d: validate_roster(d, course_id))
    return _rehydrate_roster(document, root)


def read_groups(course_id, *, root=None) -> dict | None:
    document = _read_document(groups_path(course_id, root),
                              lambda d: validate_groups(d, course_id))
    return _rehydrate_groups(document, root)


def groups_are_current(document: dict | None, *, max_age_hours: float) -> bool:
    """A group snapshot is display-only and usable only while strictly fresh."""
    if not document or document.get("state") != "current":
        return False
    age = age_hours(document.get("last_success_at", ""))
    return age is not None and 0 <= age < max_age_hours


def groups_for_roster(document: dict) -> list[dict]:
    """Restore Roster's legacy convenience list without duplicating it at rest."""
    return [
        {
            "category_id": category["category_id"],
            "category_name": category["category_name"],
            "groups": [
                {
                    "id": group["id"],
                    "name": group["name"],
                    "memberships": [dict(membership) for membership in group["memberships"]],
                    "student_ids": [membership["user_id"] for membership in group["memberships"]],
                }
                for group in category["groups"]
            ],
        }
        for category in document["categories"]
    ]


def invalidate_groups(course_id, *, root=None, attempted_at: str | None = None) -> dict | None:
    """Mark an existing group snapshot stale after a confirmed Canvas write."""
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with course_lock(course_id):
        document = _read_document(groups_path(course_id, root),
                                  lambda d: validate_groups(d, course_id))
        if document is None:
            return None
        document["state"] = "stale"
        document["last_attempt_at"] = attempted_at
        document["error_code"] = "invalidated"
        return _write_document(groups_path(course_id, root), validate_groups(document, course_id))


def merge_group_category(course_id, category: dict, *, root=None,
                         attempted_at: str | None = None) -> dict | None:
    """Merge one live-refetched category into an existing group snapshot.

    Targeted reconciliation after a single group/membership write: replaces
    only the one category that actually changed, leaving every other
    category byte-identical, instead of the whole-document
    ``invalidate_groups`` staling. Reconciliation only ever runs against a
    previously written document — a lone category is never treated as the
    course's complete membership (vision doc S10.1: "a complete collection
    defines membership") — so this returns ``None`` (no write, no-op) when
    no prior document exists; the caller falls back to the existing
    whole-document ``invalidate_groups`` path.

    When ``category["category_name"]`` is falsy, the previous entry's name
    is kept: only a genuinely new category (created moments ago, with a
    Canvas-confirmed name) should ever supply a real name here — every other
    caller only knows the category's id, not its current display name, and
    must not invent one.
    """
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with course_lock(course_id):
        document = read_groups(course_id, root=root)
        if document is None:
            return None
        category_id = str(category.get("category_id") or "")
        existing = document.get("categories") or []
        previous = next((c for c in existing if c.get("category_id") == category_id), None)
        category_name = (category.get("category_name")
                         or (previous or {}).get("category_name") or "")
        with _vault_transaction(root) as vault:
            normalized = normalize_group_categories([{
                "category_id": category_id,
                "category_name": category_name,
                "groups": category.get("groups") or [],
            }], vault)[0]
        merged = []
        replaced = False
        for existing_category in existing:
            if existing_category.get("category_id") == category_id:
                merged.append(normalized)
                replaced = True
            else:
                merged.append(existing_category)
        if not replaced:
            merged.append(normalized)
        updated_document = {
            "schema_version": MIRROR_VERSION,
            "course_id": str(course_id),
            **_envelope("current", attempted_at),
            "categories": merged,
        }
        written = _write_document(groups_path(course_id, root),
                                  validate_groups(updated_document, course_id))
        return _rehydrate_groups(written, root)


def mark_groups_stale(course_id, *, root=None, attempted_at: str | None = None) -> dict | None:
    """Retain a last-good group snapshot but make a failed refresh honest."""
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with course_lock(course_id):
        document = _read_document(groups_path(course_id, root),
                                  lambda d: validate_groups(d, course_id))
        if document is None:
            return None
        document["state"] = "stale"
        document["last_attempt_at"] = attempted_at
        document["error_code"] = "refresh_failed"
        return _write_document(groups_path(course_id, root), validate_groups(document, course_id))


def read_assignments(course_id, *, root=None) -> dict | None:
    return _read_document(assignments_path(course_id, root),
                          lambda d: validate_assignments(d, course_id))


def read_submissions(course_id, assignment_id, *, root=None) -> dict | None:
    document = _read_document(submission_path(course_id, assignment_id, root),
                              lambda d: validate_submissions(d, course_id, assignment_id))
    return _rehydrate_submissions(document, root)


def list_submission_assignment_ids(course_id, *, root=None) -> list[str]:
    directory = submissions_dir(course_id, root)
    if not directory or not os.path.isdir(workspace.extended_path(directory)):
        return []
    return sorted(name[: -len(".v1.json")] for name in os.listdir(workspace.extended_path(directory))
                  if name.endswith(".v1.json"))


# --- sync state --------------------------------------------------------------------

def default_sync(course_id) -> dict:
    empty = {"state": "unavailable", "last_success_at": "",
             "last_attempt_at": "", "error_code": ""}
    return {
        "schema_version": MIRROR_VERSION,
        "course_id": str(course_id),
        "passes": {name: dict(empty) for name in PASS_NAMES},
        "watermarks": {"submitted_since": "", "graded_since": ""},
    }


def read_sync(course_id, *, root=None) -> dict:
    document = _read_document(sync_path(course_id, root),
                              lambda d: validate_sync(d, course_id))
    return document if document is not None else default_sync(course_id)


def default_refresh(course_id) -> dict:
    return {
        "schema_version": REFRESH_VERSION,
        "course_id": str(course_id),
        "operation_id": "uninitialized",
        "state": "failed",
        "requested_at": "",
        "started_at": "",
        "finished_at": "",
        "error_code": "not_refreshed",
        "revision": 0,
        "snapshot_id": "",
    }


def read_refresh(course_id, *, root=None) -> dict:
    document = _read_document(refresh_path(course_id, root),
                              lambda d: validate_refresh(d, course_id))
    return document if document is not None else default_refresh(course_id)


def begin_refresh(course_id, *, operation_id: str, requested_at: str | None = None,
                  root=None) -> dict:
    """Durably mark one refresh attempt as in progress.

    A prior usable revision is retained while the new attempt runs.  The
    operation id is caller-owned so a coordinator can return the same identity
    to every coalesced waiter.
    """
    _require_dir(course_id, root)
    attempted_at = requested_at or now_iso()
    with course_lock(course_id):
        previous = read_refresh(course_id, root=root)
        document = {
            "schema_version": REFRESH_VERSION,
            "course_id": str(course_id),
            "operation_id": str(operation_id),
            "state": "syncing",
            "requested_at": attempted_at,
            "started_at": attempted_at,
            "finished_at": "",
            "error_code": "",
            "revision": previous.get("revision", 0),
            "snapshot_id": previous.get("snapshot_id", ""),
        }
        return _write_document(refresh_path(course_id, root),
                               validate_refresh(document, course_id))


def finish_refresh(course_id, *, operation_id: str, ok: bool,
                   finished_at: str | None = None, error_code: str = "",
                   root=None) -> dict:
    """Commit the terminal lifecycle state after projection writes settle."""
    _require_dir(course_id, root)
    finished_at = finished_at or now_iso()
    with course_lock(course_id):
        document = read_refresh(course_id, root=root)
        if document.get("operation_id") != str(operation_id):
            raise ValueError("refresh_operation_mismatch")
        if ok:
            revision = int(document.get("revision", 0)) + 1
            document.update({
                "state": "synced", "finished_at": finished_at,
                "error_code": "", "revision": revision,
                "snapshot_id": f"{str(course_id)}:{revision}",
            })
        else:
            document.update({
                "state": "failed", "finished_at": finished_at,
                "error_code": str(error_code or "sync_failed"),
            })
        return _write_document(refresh_path(course_id, root),
                               validate_refresh(document, course_id))


def default_course_context(course_id) -> dict:
    return {
        "schema_version": COURSE_CONTEXT_VERSION,
        "course_id": str(course_id),
        "state": "unavailable",
        "last_success_at": "",
        "last_attempt_at": "",
        "error_code": "",
        "lifecycle": "unknown",
        "course_workflow_state": "",
        "course_concluded": False,
        "course_end_at": "",
        "term_end_at": "",
        "enrollment_states": [],
    }


def read_course_context(course_id, *, root=None) -> dict:
    """Return a valid lifecycle record, or the unavailable default.

    Corrupt/foreign files are intentionally treated as absent, like every
    other disposable mirror record.  This keeps a malformed lifecycle file
    from authorizing cadence suppression.
    """
    document = _read_document(course_context_path(course_id, root),
                              lambda d: validate_course_context(d, course_id))
    return document if document is not None else default_course_context(course_id)


def record_course_context(course_id, *, ok: bool, attempted_at: str | None = None,
                          error_code: str = "", lifecycle: str = "unknown",
                          course_workflow_state: str = "",
                          course_concluded: bool = False, course_end_at: str = "",
                          term_end_at: str = "", enrollment_states: list[str] | None = None,
                          root=None) -> dict:
    """Commit a lifecycle acquisition outcome without touching any other scope.

    On failure this changes only the context envelope.  The prior successful
    lifecycle proof remains available as stale context for conservative
    concluded-course cadence suppression.
    """
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with course_lock(course_id):
        document = read_course_context(course_id, root=root)
        document["last_attempt_at"] = attempted_at
        if ok:
            document.update({
                "state": "current",
                "last_success_at": attempted_at,
                "error_code": "",
                "lifecycle": lifecycle,
                "course_workflow_state": course_workflow_state,
                "course_concluded": course_concluded,
                "course_end_at": course_end_at,
                "term_end_at": term_end_at,
                "enrollment_states": list(enrollment_states or []),
            })
        else:
            document["state"] = "stale" if document["last_success_at"] else "unavailable"
            document["error_code"] = error_code or "connection"
        return _write_document(course_context_path(course_id, root),
                               validate_course_context(document, course_id))


def default_new_quiz_capability(course_id) -> dict:
    return {
        "schema_version": NEW_QUIZ_CAPABILITY_VERSION,
        "course_id": str(course_id),
        "capability": "unknown",
        "last_probe_at": "",
        "retry_after": "",
        "evidence": {"category": "", "consecutive_failures": 0},
    }


def read_new_quiz_capability(course_id, *, root=None) -> dict:
    document = _read_document(new_quiz_capability_path(course_id, root),
                              lambda d: validate_new_quiz_capability(d, course_id))
    return document if document is not None else default_new_quiz_capability(course_id)


def write_new_quiz_capability(course_id, *, capability: str, last_probe_at: str,
                              retry_after: str = "", evidence_category: str = "",
                              consecutive_failures: int = 0, root=None) -> dict:
    """Persist the New Quiz metadata-scope capability record. Student-free:
    only the fields the locked design allows (capability, probe/retry
    timestamps, a sanitized evidence category + count) — never status text,
    response bodies, URLs, or quiz titles."""
    _require_dir(course_id, root)
    document = {
        "schema_version": NEW_QUIZ_CAPABILITY_VERSION,
        "course_id": str(course_id),
        "capability": capability,
        "last_probe_at": last_probe_at,
        "retry_after": retry_after,
        "evidence": {"category": evidence_category,
                     "consecutive_failures": int(consecutive_failures)},
    }
    with course_lock(course_id):
        return _write_document(new_quiz_capability_path(course_id, root),
                               validate_new_quiz_capability(document, course_id))


def record_pass(course_id, pass_name: str, *, ok: bool, error_code: str = "",
                attempted_at: str | None = None, watermarks: dict | None = None,
                root=None) -> dict:
    """Record one sync-pass outcome (catalog-style degradation: a failure
    after a prior success is 'stale', never-succeeded is 'unavailable').
    Watermarks are advanced only on success."""
    if pass_name not in PASS_NAMES:
        raise ValueError(f"unknown sync pass: {pass_name}")
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with course_lock(course_id):
        document = read_sync(course_id, root=root)
        entry = document["passes"][pass_name]
        entry["last_attempt_at"] = attempted_at
        if ok:
            entry["state"] = "current"
            entry["last_success_at"] = attempted_at
            entry["error_code"] = ""
            if watermarks:
                for key in _WATERMARK_KEYS:
                    if key in watermarks:
                        document["watermarks"][key] = str(watermarks[key])
        else:
            entry["state"] = "stale" if entry["last_success_at"] else "unavailable"
            entry["error_code"] = error_code or "sync_failed"
        return _write_document(sync_path(course_id, root),
                               validate_sync(document, course_id))


def default_submission_comments_state(course_id) -> dict:
    return {
        "schema_version": SUBMISSION_COMMENTS_STATE_VERSION,
        "course_id": str(course_id),
        "state": "unavailable",
        "last_success_at": "",
        "last_attempt_at": "",
        "error_code": "",
    }


def read_submission_comments_state(course_id, *, root=None) -> dict:
    document = _read_document(
        submission_comments_state_path(course_id, root),
        lambda d: validate_submission_comments_state(d, course_id))
    return document if document is not None else default_submission_comments_state(course_id)


def record_submission_comments_state(course_id, *, ok: bool,
                                     attempted_at: str | None = None,
                                     error_code: str = "",
                                     root=None) -> dict:
    """Record a submission-comments acquisition outcome.

    On failure this changes only the sidecar envelope (state degrades from
    current to stale, or stays unavailable).  Last-good submission files are
    never touched — only the sidecar timestamp is updated.
    """
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with course_lock(course_id):
        document = read_submission_comments_state(course_id, root=root)
        document["last_attempt_at"] = attempted_at
        if ok:
            document.update({
                "state": "current",
                "last_success_at": attempted_at,
                "error_code": "",
            })
        else:
            document["state"] = ("stale" if document["last_success_at"]
                                 else "unavailable")
            document["error_code"] = error_code or "sync_failed"
        return _write_document(
            submission_comments_state_path(course_id, root),
            validate_submission_comments_state(document, course_id))
