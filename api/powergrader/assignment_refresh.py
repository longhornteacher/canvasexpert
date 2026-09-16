"""Focused, private assignment-evidence refresh for PowerGrader starts.

This is deliberately a small owner, not a general Canvas cache.  It keeps
transport URLs in memory and persists only canonical relative evidence paths.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from api.powergrader import canvas_fetch
from api.mirror import new_quizzes, read_service, store as mirror_store
from api.platform_services import workspace


REFRESH_BINARY_LIMIT = 10 * 1024 * 1024
MEDIA_CLASS_LIMIT = 500 * 1024 * 1024

MIRROR_PREPARATION_ERROR = (
    "The local CanvasMirror for this course is missing, stale, or incomplete. "
    "Call refresh_mirror(course_id) and retry."
)

# A mirror submission row whose user no longer matches the roster is an
# identity mismatch unless it is a provably historical committed grade. The
# outward code and message name no ID, name, count, or other student identity.
MIRROR_SUBMISSION_IDENTITY_MISMATCH_CODE = "mirror_submission_identity_mismatch"
MIRROR_SUBMISSION_IDENTITY_MISMATCH = (
    "The local CanvasMirror contains a submission row that does not match the "
    "course roster. Call refresh_mirror(course_id) and retry."
)


def _is_historical_orphan(current: dict) -> bool:
    """A committed Canvas grade for a departed learner: safe to ignore.

    Only a row Canvas already graded, carrying a real score and no submission
    timestamp, can be a historical orphan. Every other unmatched row --
    submitted, pending, unscored, or ambiguous -- fails closed.
    """
    return (
        current.get("workflow_state") == "graded"
        and current.get("score") is not None
        and not str(current.get("submitted_at") or "").strip()
    )


class RefreshBudget:
    def __init__(self, limit=REFRESH_BINARY_LIMIT):
        self.limit = limit
        self.used = 0

    def reserve(self, size) -> bool:
        try:
            amount = int(size)
        except (TypeError, ValueError):
            return False
        if amount < 0 or amount > self.limit - self.used:
            return False
        self.used += amount
        return True


def _indicator(record):
    return {key: record.get(key) for key in ("size", "updated_at", "modified_at", "created_at", "uuid", "md5")
            if record.get(key) not in (None, "")}


def _relative(path, root):
    if not path or not root or not workspace.path_within_workspace(path, root):
        return None
    return os.path.relpath(path, root)


def _safe_error(record):
    return {key: record.get(key) for key in ("error_code", "error_message", "download_status", "extraction_status")
            if record.get(key) not in (None, "")}


def _existing_records(manifest, root, course_id, assignment_id, kind):
    reusable = {}
    for entry in (manifest or {}).get("evidence", []):
        if entry.get("kind") != kind or entry.get("course_id") != str(course_id) or entry.get("assignment_id") != str(assignment_id):
            continue
        evidence_id = str(entry.get("evidence_id") or "")
        rel = entry.get("relative_path")
        if not evidence_id or not rel:
            continue
        path = os.path.abspath(os.path.join(root, rel))
        if workspace.path_within_workspace(path, root) and os.path.isfile(path):
            reusable[evidence_id] = {"local_path": path, "actual_size": entry.get("actual_size"),
                                     "declared_size": entry.get("declared_size"), "ai_eligible": entry.get("ai_eligible"),
                                     "local_only": entry.get("local_only"), "extraction_status": entry.get("extraction_status"),
                                     "warnings": entry.get("warnings", []), "content_indicator": entry.get("content_indicator", {})}
    return reusable


def _existing_media_records(manifest, root, course_id, assignment_id):
    reusable = {}
    for entry in (manifest or {}).get("evidence", []):
        if entry.get("kind") != "media_recording" or entry.get("course_id") != str(course_id) or entry.get("assignment_id") != str(assignment_id):
            continue
        media_id = str(entry.get("evidence_id") or "")
        original_rel, canonical_rel = entry.get("original_relative_path"), entry.get("relative_path")
        if not media_id or not original_rel or not canonical_rel:
            continue
        original_path = os.path.abspath(os.path.join(root, original_rel))
        canonical_path = os.path.abspath(os.path.join(root, canonical_rel))
        if not (workspace.path_within_workspace(original_path, root) and workspace.path_within_workspace(canonical_path, root)
                and os.path.isfile(original_path) and os.path.isfile(canonical_path)):
            continue
        from api.powergrader import media_recordings
        try:
            if (media_recordings._sha256(original_path) != entry.get("original_sha256")
                    or media_recordings._sha256(canonical_path) != entry.get("canonical_sha256")):
                continue
        except OSError:
            continue
        reusable[media_id] = {
            "filename": "recording", "item_id": media_id, "item_link": media_id,
            "attempt": entry.get("attempt") or 1, "download_status": "reused",
            "extraction_status": "validated", "ai_eligible": False, "local_only": True,
            "media_recording": True, "analysis_unavailable": True,
            "actual_size": entry.get("actual_size"), "duration_seconds": entry.get("duration_seconds"),
            "original_path": original_path, "canonical_path": canonical_path,
            "original_sha256": entry.get("original_sha256"), "canonical_sha256": entry.get("canonical_sha256"),
            "content_indicator": entry.get("content_indicator") or {}, "warnings": [],
        }
    return reusable


def refresh_assignment(course_id: str, assignment_id: str, *, session_id: str):
    """Refresh one assignment and return URL-free submission material plus scope state."""
    root = workspace.workspace_root()
    if not root:
        return None, None, {"error": "No workspace configured — finish setup first."}
    from api.platform_services import config
    course_name = config.course_display_name(course_id) or course_id
    # Use the immutable ID as the display component for new managed evidence so
    # the record is resolvable before and after the Canvas title response.
    evidence_assignment_name = str(assignment_id)
    existing = workspace.read_assignment_evidence_manifest(course_name, course_id, evidence_assignment_name, assignment_id, root)
    budget = RefreshBudget()
    media_budget = RefreshBudget(MEDIA_CLASS_LIMIT)
    cached_new_quiz, _cache_state = new_quizzes.read_fresh_snapshot(
        course_id, assignment_id, root=root,
        max_age_hours=config.mirror_serve_max_age_hours(),
    )
    # Assignment name is learned from the authoritative assignment response.
    fetch_kwargs = {
        "session_id": session_id,
        "byte_budget": budget,
        "evidence_path": "managed",
        "reusable_records": _existing_records(existing, root, course_id, assignment_id, "new_quiz"),
    }
    if cached_new_quiz is not None:
        fetch_kwargs["cached_new_quiz"] = cached_new_quiz
    subs, assignment, error = canvas_fetch.fetch_submissions(course_id, assignment_id, **fetch_kwargs)
    if error:
        return None, None, {"error": error}
    assignment = assignment or {}
    assignment_name = str(assignment.get("name") or assignment_id)
    conflicts = workspace.assignment_evidence_conflicts(course_name, course_id, evidence_assignment_name, assignment_id, root)
    if not assignment.get("is_quiz_lti_assignment"):
        reusable = _existing_records(existing, root, course_id, assignment_id, "ordinary")

        def target_path(submission, source, filename, attempt):
            user = submission.get("user") or {}
            evidence_id = source.get("id") or source.get("file_id") or source.get("attachment_id")
            return workspace.managed_evidence_path(course_name, course_id, evidence_assignment_name, assignment_id,
                user.get("sortable_name") or user.get("name") or submission.get("user_id"), submission.get("user_id"),
                attempt, evidence_id, filename), attempt

        canvas_fetch.ingest_ordinary_attachments(subs or [], course_name=course_name, course_id=course_id,
            assignment_name=evidence_assignment_name, assignment_id=assignment_id, byte_budget=budget,
            target_path=target_path, reusable_records=reusable, require_identity=True)
        canvas_fetch.ingest_media_recordings(subs or [], course_name=course_name, course_id=course_id,
            assignment_name=evidence_assignment_name, assignment_id=assignment_id, target_path=target_path,
            byte_budget=media_budget,
            reusable_records=_existing_media_records(existing, root, course_id, assignment_id))

    evidence = []
    incomplete = bool(conflicts)
    for submission in subs or []:
        # Canvas includes roster rows with no submitted attempt.  They have no
        # evidence to validate and must not hold an unrelated completed record.
        if submission.get("workflow_state") == "unsubmitted" or (
            not submission.get("submission_type") and not submission.get("attachments")
            and submission.get("new_quiz_attempt") is None
        ):
            continue
        uid = str(submission.get("user_id") or "")
        attempt = submission.get("new_quiz_attempt") or submission.get("attempt") or submission.get("submission_attempt")
        if not uid or attempt in (None, ""):
            incomplete = True
        for attachment in submission.get("attachments") or []:
            item_id = str(attachment.get("item_id") or "")
            source_id = str(attachment.get("evidence_id") or item_id or attachment.get("id") or "")
            kind = "new_quiz" if submission.get("new_quiz_attempt") is not None else "ordinary"
            if attachment.get("media_recording"):
                kind = "media_recording"
            if kind == "ordinary":
                # Canvas file IDs are the only acceptable ordinary evidence identity.
                source_id = str(attachment.get("item_id") or "")
            private_path = attachment.get("canonical_path") if kind == "media_recording" else attachment.get("local_path")
            rel = _relative(private_path, root)
            record = {"kind": kind, "course_id": str(course_id), "assignment_id": str(assignment_id),
                "user_id": uid, "submission_id": str(submission.get("id") or ""), "attempt": attempt,
                "evidence_id": source_id, "content_indicator": attachment.get("content_indicator") or _indicator(attachment), "relative_path": rel,
                "declared_size": attachment.get("declared_size"), "actual_size": attachment.get("actual_size"),
                "ai_eligible": bool(attachment.get("ai_eligible")), "local_only": bool(attachment.get("local_only")),
                "extraction_status": attachment.get("extraction_status"), "warnings": attachment.get("warnings") or [],
                "acquisition": _safe_error(attachment)}
            if kind == "media_recording":
                record.update({
                    "original_relative_path": _relative(attachment.get("original_path"), root),
                    "duration_seconds": attachment.get("duration_seconds"),
                    "original_sha256": attachment.get("original_sha256"),
                    "canonical_sha256": attachment.get("canonical_sha256"),
                })
            if (not source_id or not rel or attachment.get("download_status") not in {"downloaded", "reused"}
                    or (kind == "media_recording" and not (record.get("original_relative_path") and record.get("original_sha256") and record.get("canonical_sha256")))):
                incomplete = True
            evidence.append(record)
        if submission.get("new_quiz_files_error"):
            incomplete = True
    status = "incomplete" if incomplete else "current"
    manifest = {"version": 1, "course_id": str(course_id), "assignment_id": str(assignment_id),
        "refreshed_at": datetime.now(timezone.utc).isoformat(), "status": status,
        "assignment_indicators": _indicator(assignment), "assignment_name": assignment_name, "evidence": evidence,
        "binary_budget_bytes": REFRESH_BINARY_LIMIT, "binary_bytes_reserved": budget.used,
        "media_budget_bytes": MEDIA_CLASS_LIMIT, "media_bytes_reserved": media_budget.used}
    path = None
    if not conflicts:
        path = workspace.write_assignment_evidence_manifest(manifest, course_name=course_name, course_id=course_id,
            assignment_name=evidence_assignment_name, assignment_id=assignment_id, root=root)
    if not path:
        status = "incomplete"
    return subs, assignment, {"manifest_path": _relative(path, root), "status": status, "binary_bytes_reserved": budget.used}


def _mirror_attachment(filename: str, *, media_recording: bool = False,
                       attempt=None) -> dict:
    """Describe held evidence without claiming that any bytes were acquired."""
    return {
        "filename": str(filename or ("recording" if media_recording else "attachment")),
        "display_name": str(filename or ("recording" if media_recording else "attachment")),
        "attempt": attempt,
        "media_recording": media_recording,
        "download_status": "not_downloaded",
        "extraction_status": "unavailable",
        "ai_eligible": False,
        "local_only": False,
    }


def prepare_assignment_from_mirror(course_id: str, assignment_id: str):
    """Prepare PowerGrader input strictly from fresh local mirror projections.

    This path intentionally has no Canvas transport or evidence owner. It
    supplies ordinary text to the existing SAFE pipeline and marks all
    attachment-bearing, media-only, and empty responses for the existing held
    accounting.
    """
    root = workspace.workspace_root()
    if not root:
        return None, None, {"error": "No workspace configured — finish setup first."}
    from api.platform_services import config

    max_age_hours = config.mirror_serve_max_age_hours()
    try:
        roster = read_service.private_roster(
            course_id, root=root, max_age_hours=max_age_hours)
        assignments = read_service.private_assignments(
            course_id, root=root, max_age_hours=max_age_hours)
        submissions_scope = read_service.private_submissions(
            course_id, root=root, max_age_hours=max_age_hours)
        roster_document = mirror_store.read_roster(course_id, root=root)
        assignment_document = mirror_store.read_assignments(course_id, root=root)
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}

    if not all(isinstance(scope, dict) and scope.get("state") == "current"
               for scope in (roster, assignments, submissions_scope)):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    if (not isinstance(roster_document, dict)
            or not isinstance(assignment_document, dict)
            or roster_document.get("state") != "current"
            or assignment_document.get("state") != "current"):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    assignment_records = assignments.get("records")
    if not isinstance(assignment_records, list):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    assignment = next(
        (dict(row) for row in assignment_records if isinstance(row, dict)
         and str(row.get("id") or "") == str(assignment_id)), None)
    if assignment is None:
        return None, None, {"error": MIRROR_PREPARATION_ERROR}

    # A versioned assignment record written before this slice, or a malformed
    # hand-edited record, must not be interpreted as an ordinary assignment.
    classification_keys = {
        "quiz_id", "is_quiz", "quiz_kind", "is_quiz_lti_assignment",
    }
    if not classification_keys.issubset(assignment):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    if (not isinstance(assignment.get("quiz_id"), str)
            or not isinstance(assignment.get("is_quiz"), bool)
            or not isinstance(assignment.get("is_quiz_lti_assignment"), bool)
            or assignment.get("quiz_kind") not in {"", "quiz", "classic_quiz", "new_quiz"}):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    quiz_kind = assignment.get("quiz_kind")
    is_quiz = assignment.get("is_quiz")
    if (quiz_kind == "quiz"
            or is_quiz != (quiz_kind != "")
            or (assignment.get("is_quiz_lti_assignment") and quiz_kind != "new_quiz")):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    # The mirror's assignment projection stores description_text under its
    # student-free name; the downstream workflow still consumes `description`.
    assignment["description"] = str(
        assignment.get("description_text") or assignment.get("description") or "")
    assignment["name"] = str(assignment.get("name") or assignment_id)
    assignment["points_possible"] = assignment.get("points_possible")
    roster_records = roster.get("records")
    if not isinstance(roster_records, list):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    roster_by_id = {
        str(student.get("id")): dict(student)
        for student in roster_records
        if isinstance(student, dict) and student.get("id") not in (None, "")
    }
    submission_document = mirror_store.read_submissions(
        course_id, assignment_id, root=root)
    if (not isinstance(submission_document, dict)
            or submission_document.get("state") != "current"):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}
    if not isinstance(submission_document.get("submissions"), dict):
        return None, None, {"error": MIRROR_PREPARATION_ERROR}

    rows = []
    entry_count = 0
    historical_orphans = 0
    required_current = {
        "user_id", "workflow_state", "submitted_at", "graded_at", "score",
        "grade", "late", "missing", "excused", "attempt",
        "submission_type", "body", "url",
    }
    for entry in submission_document["submissions"].values():
        if not isinstance(entry, dict) or not isinstance(entry.get("current"), dict):
            return None, None, {"error": MIRROR_PREPARATION_ERROR}
        current = dict(entry["current"])
        if not required_current.issubset(current):
            return None, None, {"error": MIRROR_PREPARATION_ERROR}
        if str(current.get("assignment_id") or "") != str(assignment_id):
            return None, None, {"error": MIRROR_PREPARATION_ERROR}
        entry_count += 1
        uid = str(current.get("user_id") or "")
        if not uid or uid not in roster_by_id:
            # A row that cannot be reconciled to the roster is an identity
            # mismatch, never live Canvas work. Only a provably historical
            # committed grade (graded, scored, never submitted) is ignored.
            if _is_historical_orphan(current):
                historical_orphans += 1
                continue
            return None, None, {
                "error": MIRROR_SUBMISSION_IDENTITY_MISMATCH,
                "code": MIRROR_SUBMISSION_IDENTITY_MISMATCH_CODE,
            }
        attempts = entry.get("attempts")
        if not isinstance(attempts, dict):
            return None, None, {"error": MIRROR_PREPARATION_ERROR}
        attempt = current.get("attempt")
        attempt_record = attempts.get(str(attempt)) if attempt not in (None, "") else None
        if attempt_record is not None and not isinstance(attempt_record, dict):
            return None, None, {"error": MIRROR_PREPARATION_ERROR}
        attachment_names = []
        if attempt_record is not None:
            attachment_names = attempt_record.get("attachment_names")
            if not isinstance(attachment_names, list) or not all(
                    isinstance(name, str) for name in attachment_names):
                return None, None, {"error": MIRROR_PREPARATION_ERROR}
        submission_type = str(current.get("submission_type") or "")
        body = str(current.get("body") or "")
        media_only = submission_type == "media_recording"
        unreadable = bool(attachment_names) or media_only or not body.strip()
        if unreadable:
            body = ""
        attachments = [
            _mirror_attachment(name, attempt=attempt) for name in attachment_names
        ]
        if media_only and not attachments:
            attachments = [_mirror_attachment("recording", media_recording=True,
                                              attempt=attempt)]
        row = current
        row.update({
            "assignment": assignment,
            "user": roster_by_id[uid],
            "body": body,
            "attachments": attachments,
            "expected_attachment_count": len(attachments) if attachments else None,
            "_mirror_unreadable": unreadable,
        })
        rows.append(row)
    return rows, assignment, {"status": "mirror", "manifest_path": None,
                              "historical_only": bool(entry_count) and historical_orphans == entry_count}
