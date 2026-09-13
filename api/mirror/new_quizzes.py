"""CanvasMirror v2 storage and reads for New Quizzes.

New Quiz state is deliberately kept outside the generic v1 assignment and
submission collections.  Metadata is small enough for the normal mirror
cadence; response snapshots are written only after a focused Student Analysis
fetch.  The module has no Canvas client dependency so storage and freshness
rules stay offline-testable.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.storage_support import atomic_write_json
from api.platform_services import workspace

from . import store


NEW_QUIZ_VERSION = 2
NEW_QUIZZES_DIRNAME = "new_quizzes"
SYNC_FILENAME = "_sync.v2.json"
QUIZ_FILENAME = "quiz.v2.json"
STUDENTS_DIRNAME = "students"

COLLECTION_STATES = {"current", "incomplete"}
SYNC_STATES = COLLECTION_STATES | {"stale", "unavailable"}
_ENVELOPE_KEYS = {"state", "last_success_at", "last_attempt_at", "error_code"}
_SYNC_KEYS = {"schema_version", "course_id", "metadata", "responses"}
_QUIZ_KEYS = {
    "schema_version", "course_id", "assignment_id", "state",
    "last_success_at", "last_attempt_at", "error_code", "assignment",
    "quiz", "items",
}
_STUDENT_KEYS = {
    "schema_version", "course_id", "assignment_id", "user_id", "state",
    "last_success_at", "last_attempt_at", "error_code", "latest_attempt",
    "current", "attempts",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_hours(iso_z: str, now_iso_z: str | None = None) -> float | None:
    return store.age_hours(iso_z, now_iso_z)


def new_quizzes_dir(course_id, root=None):
    base = store.course_dir(course_id, root)
    return os.path.join(base, NEW_QUIZZES_DIRNAME) if base else None


def sync_path(course_id, root=None):
    directory = new_quizzes_dir(course_id, root)
    return os.path.join(directory, SYNC_FILENAME) if directory else None


def quiz_dir(course_id, assignment_id, root=None):
    directory = new_quizzes_dir(course_id, root)
    return os.path.join(directory, workspace.safe_id(assignment_id)) if directory else None


def quiz_path(course_id, assignment_id, root=None):
    directory = quiz_dir(course_id, assignment_id, root)
    return os.path.join(directory, QUIZ_FILENAME) if directory else None


def students_dir(course_id, assignment_id, root=None):
    directory = quiz_dir(course_id, assignment_id, root)
    return os.path.join(directory, STUDENTS_DIRNAME) if directory else None


def student_path(course_id, assignment_id, user_id, root=None):
    directory = students_dir(course_id, assignment_id, root)
    return os.path.join(directory, f"{workspace.safe_id(user_id)}.v2.json") if directory else None


def _require_dir(course_id, root):
    directory = new_quizzes_dir(course_id, root)
    if not directory:
        raise ValueError("workspace not configured — no mirror location")
    return directory


def _read_document(path, validator):
    if not path or not os.path.exists(workspace.extended_path(path)):
        return None
    try:
        with open(workspace.extended_path(path), encoding="utf-8") as handle:
            return validator(json.load(handle))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_document(path, document):
    atomic_write_json(Path(path), document)
    return document


def _require_exact_keys(value, keys: set[str], label: str):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} keys are invalid")


def _validate_envelope(document, label: str, states: set[str]):
    if document.get("state") not in states:
        raise ValueError(f"{label} state is invalid")
    for key in ("last_success_at", "last_attempt_at", "error_code"):
        if not isinstance(document.get(key), str):
            raise ValueError(f"{label} {key} is invalid")


def _validate_sync(document, course_id):
    _require_exact_keys(document, _SYNC_KEYS, "new quiz sync")
    if document.get("schema_version") != NEW_QUIZ_VERSION:
        raise ValueError("new quiz sync schema_version is unsupported")
    if str(document.get("course_id")) != str(course_id):
        raise ValueError("new quiz sync course_id mismatch")
    _require_exact_keys(document.get("metadata"), _ENVELOPE_KEYS, "new quiz metadata")
    _validate_envelope(document["metadata"], "new quiz metadata", SYNC_STATES)
    responses = document.get("responses")
    if not isinstance(responses, dict):
        raise ValueError("new quiz responses index is invalid")
    for assignment_id, envelope in responses.items():
        _require_exact_keys(envelope, _ENVELOPE_KEYS, f"new quiz response {assignment_id}")
        _validate_envelope(envelope, f"new quiz response {assignment_id}", SYNC_STATES)
    return document


def _validate_quiz(document, course_id, assignment_id):
    _require_exact_keys(document, _QUIZ_KEYS, "new quiz metadata")
    if document.get("schema_version") != NEW_QUIZ_VERSION:
        raise ValueError("new quiz metadata schema_version is unsupported")
    if str(document.get("course_id")) != str(course_id) or str(document.get("assignment_id")) != str(assignment_id):
        raise ValueError("new quiz metadata identity mismatch")
    _validate_envelope(document, "new quiz metadata", COLLECTION_STATES)
    if not isinstance(document.get("assignment"), dict) or not isinstance(document.get("quiz"), dict):
        raise ValueError("new quiz metadata payload is invalid")
    if not isinstance(document.get("items"), dict):
        raise ValueError("new quiz item catalog is invalid")
    return document


def _validate_student(document, course_id, assignment_id):
    _require_exact_keys(document, _STUDENT_KEYS, "new quiz student")
    if document.get("schema_version") != NEW_QUIZ_VERSION:
        raise ValueError("new quiz student schema_version is unsupported")
    if str(document.get("course_id")) != str(course_id) or str(document.get("assignment_id")) != str(assignment_id):
        raise ValueError("new quiz student identity mismatch")
    _validate_envelope(document, "new quiz student", COLLECTION_STATES)
    if not isinstance(document.get("attempts"), list):
        raise ValueError("new quiz student attempts are invalid")
    if document.get("current") is not None and not isinstance(document.get("current"), dict):
        raise ValueError("new quiz student current attempt is invalid")
    return document


def default_sync(course_id):
    empty = {"state": "unavailable", "last_success_at": "",
             "last_attempt_at": "", "error_code": ""}
    return {"schema_version": NEW_QUIZ_VERSION, "course_id": str(course_id),
            "metadata": dict(empty), "responses": {}}


def read_sync(course_id, *, root=None):
    document = _read_document(sync_path(course_id, root), lambda d: _validate_sync(d, course_id))
    return document if document is not None else default_sync(course_id)


def _record_sync(course_id, *, kind: str, assignment_id: str | None = None,
                 state: str, error_code: str = "", attempted_at: str | None = None,
                 root=None):
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    with store.course_lock(course_id):
        document = read_sync(course_id, root=root)
        target = document["metadata"] if kind == "metadata" else document["responses"].setdefault(str(assignment_id), {
            "state": "unavailable", "last_success_at": "", "last_attempt_at": "", "error_code": ""
        })
        target["last_attempt_at"] = attempted_at
        if state in {"current", "incomplete"}:
            target["state"] = state
            target["last_success_at"] = attempted_at
            target["error_code"] = error_code or ("incomplete" if state == "incomplete" else "")
        else:
            target["state"] = "stale" if target["last_success_at"] else "unavailable"
            target["error_code"] = error_code or "sync_failed"
        return _write_document(sync_path(course_id, root), _validate_sync(document, course_id))


def _text(value):
    return str(value) if value not in (None, "") else ""


def is_teacher_scorable_item(item: dict) -> bool:
    """Return whether an item uses a supported manual-response lane."""
    kind = _text(item.get("type")).strip().casefold().replace("-", "_")
    return kind == "essay" or "upload" in kind


def _is_safely_auto_scored_item(item: dict) -> bool:
    """Require explicit non-manual type and score evidence for mismatch tolerance."""
    kind = _text(item.get("type")).strip()
    return (
        bool(kind)
        and not is_teacher_scorable_item(item)
        and item.get("earned_score") is not None
    )


def normalize_assignment(row: dict, assignment_id=None) -> dict:
    row = row if isinstance(row, dict) else {}
    aid = assignment_id or row.get("id")
    return {
        "id": _text(aid),
        "name": _text(row.get("name")),
        "description": _text(row.get("description")),
        "due_at": _text(row.get("due_at")),
        "lock_at": _text(row.get("lock_at")),
        "unlock_at": _text(row.get("unlock_at")),
        "points_possible": row.get("points_possible"),
        "published": bool(row.get("published", True)),
        "html_url": _text(row.get("html_url")),
        "submission_types": [_text(value) for value in (row.get("submission_types") or [])],
        "updated_at": _text(row.get("updated_at")),
        "is_quiz_lti_assignment": bool(row.get("is_quiz_lti_assignment")),
        "quiz_id": _text(row.get("quiz_id") or aid),
    }


_QUIZ_METADATA_FIELDS = (
    "id", "name", "title", "description", "points_possible", "published",
    "workflow_state", "quiz_type", "time_limit", "shuffle_answers",
    "hide_results", "one_question_at_a_time", "allowed_attempts",
    "scoring_policy", "due_at", "lock_at", "unlock_at", "created_at",
    "updated_at", "item_count",
)

_REAL_QUIZ_FIELDS = {
    "title", "quiz_type", "time_limit", "shuffle_answers", "hide_results",
    "one_question_at_a_time", "allowed_attempts", "scoring_policy", "item_count",
    "workflow_state",
}


def normalize_quiz(quiz: dict, assignment_id) -> dict:
    quiz = quiz if isinstance(quiz, dict) else {}
    result = {key: quiz.get(key) for key in _QUIZ_METADATA_FIELDS if key in quiz}
    result.setdefault("id", _text(quiz.get("id") or assignment_id))
    result.setdefault("name", _text(quiz.get("name") or quiz.get("title")))
    result["id"] = _text(result.get("id"))
    for key in ("name", "title", "description", "workflow_state", "quiz_type", "scoring_policy",
                "due_at", "lock_at", "unlock_at", "created_at", "updated_at"):
        if key in result:
            result[key] = _text(result[key])
    return result


def _has_real_quiz_payload(quiz: dict | None) -> bool:
    """Distinguish a quiz API document from the core assignment projection."""
    return isinstance(quiz, dict) and bool(
        quiz.get("id") not in (None, "")
        and _REAL_QUIZ_FIELDS.intersection(quiz)
    )


def _choice(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("id", "position", "item_body", "text", "label", "value"):
        if value.get(key) not in (None, ""):
            result[key] = value[key] if key == "position" else _text(value[key])
    return result or None


def normalize_item(outer: dict, entry: dict) -> dict | None:
    if not isinstance(entry, dict) or entry.get("id") in (None, ""):
        return None
    item_id = _text(entry.get("id"))
    kind = entry.get("interaction_type_slug") or entry.get("item_type") or entry.get("type") or outer.get("item_type")
    points = outer.get("points_possible")
    if points is None:
        points = entry.get("points_possible") or entry.get("points")
    choices = entry.get("choices") or entry.get("answers") or entry.get("options") or []
    if isinstance(choices, dict):
        choices = list(choices.values())
    return {
        "id": item_id,
        "type": _text(kind),
        "points_possible": points,
        "prompt": _text(entry.get("item_body") or entry.get("body") or entry.get("prompt")),
        "entry_type": _text(entry.get("entry_type") or outer.get("entry_type")),
        "choices": [choice for choice in (_choice(value) for value in choices) if choice],
    }


def normalize_items(items) -> dict[str, dict]:
    result = {}
    for outer in items or []:
        if not isinstance(outer, dict):
            continue
        entries = outer.get("entry")
        entries = entries if isinstance(entries, list) else [entries or outer]
        for entry in entries:
            normalized = normalize_item(outer, entry)
            if normalized:
                result[normalized["id"]] = normalized
    return result


def write_quiz_metadata(course_id, assignment_id, *, assignment=None, quiz=None,
                         items=None, root=None, attempted_at=None, state="current",
                         error_code=""):
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    document = {
        "schema_version": NEW_QUIZ_VERSION,
        "course_id": str(course_id),
        "assignment_id": str(assignment_id),
        "state": state,
        "last_success_at": attempted_at,
        "last_attempt_at": attempted_at,
        "error_code": error_code or ("incomplete" if state == "incomplete" else ""),
        "assignment": normalize_assignment(assignment or {}, assignment_id),
        "quiz": normalize_quiz(quiz or {}, assignment_id),
        "items": normalize_items(items or []),
    }
    with store.course_lock(course_id):
        return _write_document(quiz_path(course_id, assignment_id, root),
                               _validate_quiz(document, course_id, assignment_id))


def read_quiz(course_id, assignment_id, *, root=None):
    return _read_document(quiz_path(course_id, assignment_id, root),
                          lambda d: _validate_quiz(d, course_id, assignment_id))


def list_assignment_ids(course_id, *, root=None):
    directory = new_quizzes_dir(course_id, root)
    if not directory or not os.path.isdir(workspace.extended_path(directory)):
        return []
    result = []
    for name in os.listdir(workspace.extended_path(directory)):
        child = os.path.join(directory, name)
        if os.path.isdir(workspace.extended_path(child)) and os.path.isfile(workspace.extended_path(os.path.join(child, QUIZ_FILENAME))):
            result.append(name)
    return sorted(result)


def _scrub(value, *, relative_root=None):
    """Remove transport secrets and URL-bearing fields from cached payloads."""
    forbidden = {"url", "signed_url", "signedurl", "launch_url", "launchurl", "headers",
                 "authorization", "cookie", "token", "jwt", "access_token", "accesstoken",
                 "result_token", "resulttoken", "workflow_jwt", "workflowjwt", "signature",
                 "quiz_host", "quizhost", "backend", "backend_url", "backendurl", "local_path",
                 "localpath", "preview_url", "previewurl", "download_url", "downloadurl",
                 "attachment_url", "attachmenturl", "href"}
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in forbidden:
                continue
            result[str(key)] = _scrub(item, relative_root=relative_root)
        return result
    if isinstance(value, list):
        return [_scrub(item, relative_root=relative_root) for item in value]
    return value


def _relative_evidence(record, root):
    if not isinstance(record, dict):
        return None
    path = record.get("local_path")
    relative = None
    if path and root and workspace.path_within_workspace(path, root):
        relative = os.path.relpath(path, root)
    return {
        key: record.get(key)
        for key in ("filename", "item_id", "attempt", "evidence_id", "download_status",
                    "extraction_status", "actual_size", "declared_size", "ai_eligible",
                    "local_only", "warnings", "error_code", "error_message")
        if record.get(key) not in (None, "")
    } | ({"relative_path": relative} if relative else {})


def _cache_submission(normalized, root):
    if not isinstance(normalized, dict):
        return {}
    cached = _scrub(normalized)
    cached.pop("local_path", None)
    evidence = []
    for record in normalized.get("attachments") or []:
        safe = _relative_evidence(record, root)
        if safe:
            evidence.append(safe)
    cached["attachments"] = evidence
    for item in cached.get("new_quiz_items") or []:
        if not isinstance(item, dict):
            continue
        item["files"] = [safe for safe in (_relative_evidence(record, root) for record in normalized.get("attachments") or [])
                         if safe and str(safe.get("item_id")) == str(item.get("item_id"))]
    return cached


def _attempt_number(value) -> float:
    """Attempt values come from report rows and are usually ints, but a
    malformed row must degrade to 0, never crash the snapshot write."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_identity(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    nested = value.get("authoritative_result") if isinstance(value.get("authoritative_result"), dict) else {}
    for key in ("quiz_session_id", "quiz_api_quiz_session_id", "quizSessionId"):
        if value.get(key) not in (None, ""):
            result["quiz_session_id"] = _text(value[key])
            break
    for key in ("result_id", "authoritative_result_id", "id"):
        if value.get(key) not in (None, ""):
            result["result_id"] = _text(value[key])
            break
    if not result.get("result_id"):
        for key in ("result_id", "id"):
            if nested.get(key) not in (None, ""):
                result["result_id"] = _text(nested[key])
                break
    for key in ("result_version", "version"):
        if value.get(key) not in (None, ""):
            result["result_version"] = _text(value[key])
            break
    if not result.get("result_version"):
        for key in ("result_version", "version"):
            if nested.get(key) not in (None, ""):
                result["result_version"] = _text(nested[key])
                break
    return result


def _attempt_record(normalized, *, catalog, root, duplicate=False):
    attempt = normalized.get("new_quiz_attempt")
    items = normalized.get("new_quiz_items") or []
    item_responses = []
    incomplete = not items
    for item in items:
        item_id = _text(item.get("item_id"))
        if not item_id or (item_id not in catalog and not _is_safely_auto_scored_item(item)):
            incomplete = True
        item_responses.append({
            "item_id": item_id,
            "type": _text(item.get("type")),
            "prompt": _text(item.get("prompt")),
            "response": item.get("raw_html_answer") if isinstance(item.get("raw_html_answer"), str) else "",
            "earned_score": item.get("earned_score"),
            "status": _text(item.get("status") or item.get("response_status")),
            "possible": item.get("possible"),
        })
    identity = _safe_identity(normalized.get("new_quiz_result_identity"))
    join_state = "ambiguous" if duplicate else ("incomplete" if incomplete or normalized.get("new_quiz_join_state") else "joined")
    join_error = "ambiguous_attempt_join" if duplicate else (_text(normalized.get("new_quiz_join_error")) if join_state != "joined" else "")
    if not join_error and join_state == "incomplete":
        join_error = "item_catalog_join_incomplete" if items else "item_responses_missing"
    overall = _scrub(normalized.get("new_quiz_overall") or {})
    if not overall:
        overall = {key: normalized.get(key) for key in ("score", "grade", "workflow_state", "submitted_at")
                   if normalized.get(key) not in (None, "")}
    return {
        "attempt": attempt,
        "submitted_at": normalized.get("submitted_at"),
        "reported_at": normalized.get("new_quiz_reported_at"),
        "join_state": join_state,
        "join_error": join_error,
        "result_identity": identity,
        "overall": overall,
        "items": item_responses,
        "evidence": [safe for safe in (_relative_evidence(record, root) for record in normalized.get("attachments") or []) if safe],
        "submission": _cache_submission(normalized, root),
    }


def write_response_snapshot(course_id, assignment_id, *, assignment=None, quiz=None,
                            items=None, normalized_attempts=None, latest=None,
                            root=None, attempted_at=None):
    """Persist one successful focused response acquisition.

    Duplicate student/attempt joins are retained as separate list entries and
    make the collection ``incomplete``; they are never silently collapsed.

    Attempt history is append-preserving (mirror design law): attempts seen in
    an earlier snapshot but absent from this fetch are carried forward, while
    ``current``/``latest_attempt`` always reflect this fetch alone — Canvas is
    truth for the present, the mirror is richer about the past.

    ``latest`` is accepted for caller compatibility but unused — the
    current/latest pointer is derived from ``normalized_attempts``.
    """
    del latest
    _require_dir(course_id, root)
    attempted_at = attempted_at or now_iso()
    catalog = normalize_items(items or []) if isinstance(items, list) else (items or {})
    root_path = root or workspace.workspace_root()
    all_attempts = list(normalized_attempts or [])
    grouped: dict[str, list[dict]] = {}
    duplicate_keys: set[tuple[str, str]] = set()
    seen: set[tuple[str, str]] = set()
    missing_identity = False
    for row in all_attempts:
        uid = _text(row.get("user_id"))
        attempt = _text(row.get("new_quiz_attempt"))
        if not uid:
            missing_identity = True
            continue
        key = (uid, attempt)
        duplicate = key in seen
        if duplicate:
            duplicate_keys.add(key)
        seen.add(key)
        grouped.setdefault(uid, []).append(_attempt_record(row, catalog=catalog, root=root_path, duplicate=duplicate))

    current_rows = {}
    collection_incomplete = missing_identity
    for uid, records in grouped.items():
        records.sort(key=lambda item: (_attempt_number(item.get("attempt")),
                                       str(item.get("reported_at") or "")))
        for record in records:
            if record["join_state"] != "joined":
                collection_incomplete = True
        candidates = [record for record in records if record["join_state"] == "joined"]
        max_attempt = max((_attempt_number(record.get("attempt")) for record in candidates),
                          default=None)
        latest_candidates = [record for record in candidates
                             if _attempt_number(record.get("attempt")) == max_attempt]
        current = latest_candidates[0] if len(latest_candidates) == 1 else None
        if current is None:
            collection_incomplete = True
        current_rows[uid] = (current, records, int(max_attempt) if max_attempt is not None and max_attempt.is_integer() else max_attempt)

    state = "incomplete" if collection_incomplete else "current"
    with store.course_lock(course_id):
        existing_quiz = read_quiz(course_id, assignment_id, root=root)
        quiz_payload = quiz if _has_real_quiz_payload(quiz) else (
            (existing_quiz or {}).get("quiz") or quiz or assignment or {}
        )
        write_quiz_metadata(course_id, assignment_id, assignment=assignment, quiz=quiz_payload,
                            items=items, root=root, attempted_at=attempted_at)
        existing_ids = set(list_student_ids(course_id, assignment_id, root=root))
        for uid, (current, records, latest_attempt) in current_rows.items():
            # Append-preserving merge: carry forward previously captured
            # attempts whose attempt number is absent from this fetch. New
            # records (including deliberate ambiguous duplicates) are kept
            # exactly as fetched, so replaying a snapshot stays idempotent.
            prior = (read_student(course_id, assignment_id, uid, root=root)
                     or {}).get("attempts") or []
            fetched_numbers = {_text(record.get("attempt")) for record in records}
            carried = [record for record in prior
                       if _text(record.get("attempt")) not in fetched_numbers]
            merged = sorted(carried + records,
                            key=lambda item: (_attempt_number(item.get("attempt")),
                                              str(item.get("reported_at") or "")))
            document = {
                "schema_version": NEW_QUIZ_VERSION,
                "course_id": str(course_id),
                "assignment_id": str(assignment_id),
                "user_id": uid,
                "state": state if state == "incomplete" else "current",
                "last_success_at": attempted_at,
                "last_attempt_at": attempted_at,
                "error_code": "incomplete" if state == "incomplete" else "",
                "latest_attempt": latest_attempt,
                "current": current,
                "attempts": merged,
            }
            _write_document(student_path(course_id, assignment_id, uid, root),
                            _validate_student(document, course_id, assignment_id))
            existing_ids.discard(uid)
        for uid in existing_ids:
            try:
                os.remove(workspace.extended_path(student_path(course_id, assignment_id, uid, root)))
            except OSError:
                pass
        _record_sync(course_id, kind="responses", assignment_id=str(assignment_id),
                     state=state, error_code="incomplete" if state == "incomplete" else "",
                     attempted_at=attempted_at, root=root)
    return {"ok": state == "current", "state": state, "students": len(current_rows),
            "attempts": len(all_attempts)}


def read_student(course_id, assignment_id, user_id, *, root=None):
    return _read_document(student_path(course_id, assignment_id, user_id, root),
                          lambda d: _validate_student(d, course_id, assignment_id))


def list_student_ids(course_id, assignment_id, *, root=None):
    directory = students_dir(course_id, assignment_id, root)
    if not directory or not os.path.isdir(workspace.extended_path(directory)):
        return []
    suffix = ".v2.json"
    return sorted(name[:-len(suffix)] for name in os.listdir(workspace.extended_path(directory)) if name.endswith(suffix))


def _response_envelope(course_id, assignment_id, *, root=None):
    return read_sync(course_id, root=root)["responses"].get(str(assignment_id))


def response_freshness(course_id, assignment_id, *, root=None, max_age_hours=6.0, now=None):
    envelope = _response_envelope(course_id, assignment_id, root=root)
    if not envelope or envelope.get("state") not in COLLECTION_STATES:
        return ""
    stamp = envelope.get("last_success_at") or ""
    return stamp if age_hours(stamp, now) is not None and age_hours(stamp, now) < max_age_hours else ""


def read_fresh_snapshot(course_id, assignment_id, *, root=None, max_age_hours=6.0, now=None):
    """Return ``(snapshot, error)`` with explicit mirror/stale/incomplete states."""
    quiz = read_quiz(course_id, assignment_id, root=root)
    if quiz is None:
        return None, "unavailable"
    envelope = _response_envelope(course_id, assignment_id, root=root)
    if not envelope:
        return None, "unavailable"
    if envelope.get("state") == "incomplete":
        return None, "incomplete"
    synced_at = response_freshness(course_id, assignment_id, root=root, max_age_hours=max_age_hours, now=now)
    if not synced_at:
        return None, "stale"
    students = []
    for uid in list_student_ids(course_id, assignment_id, root=root):
        document = read_student(course_id, assignment_id, uid, root=root)
        if document is None:
            return None, "incomplete"
        if document.get("state") != "current" or not document.get("current"):
            return None, "incomplete"
        submission = document["current"].get("submission") or {}
        if not submission:
            return None, "incomplete"
        students.append(submission)
    return {
        "source": "mirror",
        "state": "mirror",
        "synced_at": synced_at,
        "course_id": str(course_id),
        "assignment_id": str(assignment_id),
        "assignment": quiz["assignment"],
        "quiz": quiz["quiz"],
        "items": quiz["items"],
        "students": students,
    }, None


def invalidate_responses(course_id, assignment_id, *, root=None, attempted_at=None):
    """Mark an assignment's New Quiz response snapshot stale after a verified
    write-back, so the next read falls back through the existing live native
    fetch chain instead of serving pre-write item scores.

    Mirrors ``store.invalidate_groups``: a missing or never-fetched snapshot is
    a no-op (nothing to falsely age), and this never writes a ``current``
    state. It adds no transport — only flips the local freshness flag.
    """
    envelope = _response_envelope(course_id, assignment_id, root=root)
    if not envelope or not envelope.get("last_success_at"):
        return None
    return _record_sync(course_id, kind="responses", assignment_id=str(assignment_id),
                        state="stale", error_code="invalidated", attempted_at=attempted_at,
                        root=root)


def write_fetch_snapshot(course_id, assignment_id, *, assignment, items,
                         normalized_attempts, latest=None, root=None,
                         attempted_at=None):
    """Adapter used by ``new_quiz_fetch.fetch`` after report/native work.
    ``latest`` is accepted for callback compatibility but unused — the
    current/latest pointer is derived from ``normalized_attempts``."""
    del latest
    return write_response_snapshot(
        course_id, assignment_id, assignment=assignment, items=items,
        normalized_attempts=normalized_attempts, root=root,
        attempted_at=attempted_at,
    )


METADATA_TRUEUP_HOURS = 24.0


def _metadata_is_current(course_id, assignment, assignment_id, *, root, now) -> bool:
    """True when the stored quiz doc already reflects this assignment row —
    same assignment ``updated_at`` and less than a day old. Item edits inside
    the LTI tool may not bump the assignment's ``updated_at``, so the daily
    true-up bounds that staleness; the delta cadence itself stays free."""
    existing = read_quiz(course_id, assignment_id, root=root)
    if existing is None or existing.get("state") != "current":
        return False
    if existing["assignment"].get("updated_at") != _text(assignment.get("updated_at")):
        return False
    age = age_hours(existing.get("last_success_at") or "", now)
    return age is not None and age < METADATA_TRUEUP_HOURS


# --- New Quiz metadata-scope capability gate (1.0beta slices 01a / 02b) -----
#
# The active-enrollment gate applies to the course collection endpoint
# (api/README.md ~205-213), so one collection 403/401 is course/scope evidence;
# a successful collection proves the scope supported. Metadata refresh first
# collects matching quiz documents once, then fetches only the needed items.
# A missing/duplicate collection row falls back narrowly to that quiz document;
# item failures remain record-level and never poison a supported scope.
CAPABILITY_COOLDOWN_HOURS = 24.0


def _classify_http_error(error) -> str:
    """Map the canvas_client error string's ``HTTP {status}`` prefix to a
    sanitized capability-evidence category. Returns "" for anything else
    (transient transport failures are item-level noise, not scope evidence).
    Does not rewrite canvas_client error handling — only reads its shape."""
    if not isinstance(error, str):
        return ""
    if error.startswith("HTTP 403"):
        return "forbidden"
    if error.startswith("HTTP 401"):
        return "unauthorized"
    return ""


def _retry_after_iso(attempted_at: str, hours: float = CAPABILITY_COOLDOWN_HOURS) -> str:
    moment = datetime.strptime(attempted_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (moment + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cooldown_active(capability_doc: dict, attempted_at: str) -> bool:
    """True while a restricted scope's cooldown has not yet expired."""
    if capability_doc.get("capability") != "restricted":
        return False
    retry_after = capability_doc.get("retry_after") or ""
    if not retry_after:
        return False
    remaining = age_hours(retry_after, attempted_at)
    return remaining is not None and remaining < 0


def _fetch_one_quiz(course_id, assignment, *, canvas_get_all, root, attempted_at,
                    force=False, quiz=None):
    """Refresh one New Quiz's metadata, using a collection match when given.

    A missing or duplicate collection match leaves ``quiz`` as ``None`` and
    uses the narrow per-quiz document fallback. The result reports the
    assignment ID, freshness skip, success/error, and Canvas calls made.
    """
    assignment_id = str(assignment.get("id") or "")
    if not assignment_id:
        return {"assignment_id": "", "skipped": True, "ok": False, "error": None, "calls": 0}
    if not force and _metadata_is_current(course_id, assignment, assignment_id,
                                         root=root, now=attempted_at):
        return {"assignment_id": assignment_id, "skipped": True, "ok": True,
                "error": None, "calls": 0}
    calls = 0
    if quiz is None:
        quiz_rows, error = canvas_get_all(
            f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}", {"per_page": 100}
        )
        calls += 1
        if error:
            return {"assignment_id": assignment_id, "skipped": False, "ok": False,
                    "error": error, "calls": calls}
        quiz = next((row for row in (quiz_rows or []) if isinstance(row, dict)), {})
    item_rows, item_error = canvas_get_all(
        f"/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items", {"per_page": 100}
    )
    calls += 1
    if item_error or not isinstance(item_rows, list):
        return {"assignment_id": assignment_id, "skipped": False, "ok": False,
                "error": item_error or "items_malformed", "calls": calls}
    write_quiz_metadata(course_id, assignment_id, assignment=assignment, quiz=quiz,
                        items=item_rows, root=root, attempted_at=attempted_at)
    return {"assignment_id": assignment_id, "skipped": False, "ok": True,
            "error": None, "calls": calls}


def sync_metadata(course_id, assignments, *, canvas_get_all, root=None, now=None,
                  bypass_cooldown=False):
    """Refresh New Quiz assignment/quiz/item metadata without reports.

    Freshness is evaluated before the one collection request, so an ordinary
    under-24-hour unchanged tick costs zero requests. A stale assignment with
    one matching collection ID fetches only its items; missing/duplicate
    collection matches use the narrow per-quiz compatibility fallback.

    A restricted cooldown skips all metadata work. Manual and expired-
    restriction probes with New Quiz assignments use the collection endpoint:
    its 403/401 renews the circuit without fan-out, while success clears a
    restriction even when no local metadata needs writing. ``bypass_cooldown``
    is manual ``sync_now``; the 15-minute heartbeat never passes it."""
    _require_dir(course_id, root)
    attempted_at = now or now_iso()
    new_quizzes = [row for row in (assignments or [])
                   if isinstance(row, dict) and row.get("is_quiz_lti_assignment") is True]

    capability_doc = store.read_new_quiz_capability(course_id, root=root)
    skipped_restricted = False
    circuit_opened = False
    circuit_cleared = False

    if not bypass_cooldown and _cooldown_active(capability_doc, attempted_at):
        prior_metadata = read_sync(course_id, root=root)["metadata"]
        state = "stale" if prior_metadata.get("last_success_at") else "unavailable"
        _record_sync(course_id, kind="metadata", state=state,
                     error_code="capability_restricted", attempted_at=attempted_at, root=root)
        return {"ok": True, "state": state, "quizzes": 0, "skipped": 0, "failures": [],
                "capability": "restricted", "skipped_restricted": True,
                "circuit_opened": False, "circuit_cleared": False}

    failures = []
    written = []
    skipped = []
    examined_full_list = True
    collection_succeeded = False
    stale_assignments = []
    for assignment in new_quizzes:
        assignment_id = str(assignment.get("id") or "")
        if not assignment_id:
            continue
        if _metadata_is_current(course_id, assignment, assignment_id,
                                root=root, now=attempted_at):
            skipped.append(assignment_id)
        else:
            stale_assignments.append(assignment)

    # Manual sync explicitly probes capability when this course has New Quiz
    # assignments. An expired restricted record gets the same bounded
    # collection probe; ordinary fresh ticks (and an empty assignment set) do
    # neither.
    collection_due = bool(new_quizzes) and (bool(stale_assignments) or
                                             bypass_cooldown or
                                             capability_doc.get("capability") == "restricted")
    if collection_due:
        collection_rows, collection_error = canvas_get_all(
            f"/api/quiz/v1/courses/{course_id}/quizzes", {"per_page": 100}
        )
        if collection_error or not isinstance(collection_rows, list):
            examined_full_list = False
            error = collection_error or "collection_malformed"
            failures.append({"assignment_id": "", "error": error})
            category = _classify_http_error(error)
            if category:
                # One collection 403/401 is enough course/scope evidence.
                # Only sanitized category/count reach capability storage.
                circuit_opened = True
                store.write_new_quiz_capability(
                    course_id, capability="restricted", last_probe_at=attempted_at,
                    retry_after=_retry_after_iso(attempted_at),
                    evidence_category=category, consecutive_failures=1, root=root,
                )
        else:
            collection_succeeded = True
            if capability_doc.get("capability") == "restricted":
                circuit_cleared = True
            store.write_new_quiz_capability(
                course_id, capability="supported", last_probe_at=attempted_at,
                retry_after="", evidence_category="", consecutive_failures=0, root=root,
            )
            collection_matches = {}
            for row in collection_rows:
                if not isinstance(row, dict):
                    continue
                assignment_id = str(row.get("id") or "")
                if assignment_id:
                    collection_matches.setdefault(assignment_id, []).append(row)
            for assignment in stale_assignments:
                assignment_id = str(assignment.get("id") or "")
                matches = collection_matches.get(assignment_id, [])
                # A unique collection ID match needs only /items. Missing or
                # duplicate rows use the narrow record-level document fallback.
                quiz = matches[0] if len(matches) == 1 else None
                outcome = _fetch_one_quiz(
                    course_id, assignment, canvas_get_all=canvas_get_all, root=root,
                    attempted_at=attempted_at, force=True, quiz=quiz,
                )
                if outcome["ok"]:
                    written.append(outcome["assignment_id"])
                else:
                    failures.append({"assignment_id": outcome["assignment_id"],
                                     "error": outcome["error"]})

    complete = not failures
    state = "current" if complete else (
        "incomplete" if (collection_succeeded or written or skipped) else "unavailable")
    _record_sync(course_id, kind="metadata", state=state,
                 error_code="metadata_partial" if failures else "",
                 attempted_at=attempted_at, root=root)
    if complete and examined_full_list:
        keep = set(str(row.get("id")) for row in new_quizzes if row.get("id") not in (None, ""))
        for assignment_id in set(list_assignment_ids(course_id, root=root)) - keep:
            try:
                directory = quiz_dir(course_id, assignment_id, root)
                if directory:
                    import shutil
                    shutil.rmtree(workspace.extended_path(directory))
            except OSError:
                pass
    capability_now = store.read_new_quiz_capability(course_id, root=root)["capability"]
    return {"ok": complete, "state": state, "quizzes": len(written),
            "skipped": len(skipped), "failures": failures,
            "capability": capability_now, "skipped_restricted": skipped_restricted,
            "circuit_opened": circuit_opened, "circuit_cleared": circuit_cleared}
