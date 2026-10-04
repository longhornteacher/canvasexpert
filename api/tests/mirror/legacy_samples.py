"""Synthetic on-disk samples from the retired CanvasMirror/history stores.

All identifiers and document content in this module are invented.  The trees
match the legacy layout closely enough for importer tests without consulting a
teacher workspace or Canvas.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from api.mirror.attempt_text import digest as attempt_text_digest


COURSE = "73001"
ASSIGNMENT = "84002"
PSEUDONYM = "Student-7F3A"
QUIZ_ASSIGNMENT = "95003"
ATTEMPT_ONE = "2026-08-12T09:15:00Z"
ATTEMPT_TWO = "2026-08-13T10:20:00Z"
CAPTURED = "2026-08-13T10:21:00Z"
PAYLOAD = b"synthetic retained upload\n"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _observation_digest(body: str, files: list[dict]) -> str:
    payload = json.dumps({"body": body, "files": files}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _course(root: Path, course_id: str = COURSE) -> Path:
    return root / "cache" / "Canvas Mirror" / course_id


def _submission(root: Path, assignment_id: str = ASSIGNMENT) -> Path:
    return _course(root) / "submissions" / f"{assignment_id}.v1.json"


def _history_dir(root: Path, assignment_id: str = ASSIGNMENT) -> Path:
    return root / "_System" / "Archive" / "Submission History" / COURSE / assignment_id


def _mirror_attempt(number: int, submitted_at: str, body: str = "", **fields) -> dict:
    current = {
        "assignment_id": ASSIGNMENT, "user_id": PSEUDONYM,
        "workflow_state": "submitted", "submitted_at": submitted_at,
        "graded_at": None, "score": None, "entered_score": None,
        "points_deducted": None, "late_policy_status": None, "late_days": None,
        "grade": None, "late": number == 1, "missing": False, "excused": False,
        "cached_due_date": None, "seconds_late": None, "attempt": number,
        "grade_matches_current_submission": None, "submission_type": "online_text_entry",
        "body": body, "url": "", "submission_comments": [],
        "submission_digest": attempt_text_digest(body),
        "attachments": [],
    }
    current.update(fields)
    return current


def _rich_manifest() -> dict:
    file_record = {
        "key": "file-17", "filename": "draft.txt", "size": len(PAYLOAD),
        "content_type": "text/plain", "status": "captured",
        "digest": PAYLOAD_SHA256, "artifact_ref": PAYLOAD_SHA256,
    }
    associated_file = {key: file_record[key] for key in
                       ("key", "filename", "size", "content_type")}
    first_body = "First synthetic draft"
    second_body = "Second synthetic draft"
    return {
        "schema_version": 1, "course_id": COURSE, "assignment_id": ASSIGNMENT,
        "revision": 3, "updated_at": CAPTURED,
        "attempts": {
            f"{PSEUDONYM}|1|{ATTEMPT_ONE}": {
                "pseudonym": PSEUDONYM, "attempt": 1, "submitted_at": ATTEMPT_ONE,
                "captured_at": CAPTURED, "body": "First synthetic draft",
                "files": [file_record], "conflict": False,
                "observations": [{
                    "digest": _observation_digest(first_body, [associated_file]), "captured_at": CAPTURED,
                    "body": first_body, "text_digest": attempt_text_digest(first_body),
                    "body_provenance": "canvas_attempt", "file_keys": ["file-17"],
                    "conflict": False,
                }],
            },
            f"{PSEUDONYM}|2|{ATTEMPT_TWO}": {
                "pseudonym": PSEUDONYM, "attempt": 2, "submitted_at": ATTEMPT_TWO,
                "captured_at": CAPTURED, "body": "Second synthetic draft",
                "files": [], "conflict": False,
                "observations": [{
                    "digest": _observation_digest(second_body, []), "captured_at": CAPTURED,
                    "body": second_body, "text_digest": attempt_text_digest(second_body),
                    "body_provenance": "canvas_attempt", "file_keys": [],
                    "conflict": False,
                }],
            },
        },
    }


def _write_ordinary(root: Path, *, rich: bool = True, conflict: bool = False) -> None:
    attempts = {"1": _mirror_attempt(1, ATTEMPT_ONE,
                                    "First synthetic draft" if rich else "")}
    current = _mirror_attempt(2, ATTEMPT_TWO,
                              "Second synthetic draft" if rich else "")
    if rich:
        current.update(late=False, score=8, grade="8", attachments=[{
            "filename": "draft.txt", "display_name": "draft.txt",
            "content_type": "text/plain", "size": len(PAYLOAD),
        }])
    else:
        current.update(late=True, attachments=[])
    attempts["2"] = current
    submission = {"current": current, "attempts": attempts}
    _write_json(_submission(root), {
        "schema_version": 1, "course_id": COURSE, "assignment_id": ASSIGNMENT,
        "state": "current", "last_success_at": CAPTURED,
        "last_attempt_at": CAPTURED, "error_code": "",
        "submissions": {PSEUDONYM: submission},
    })

    history = _rich_manifest()
    if not rich:
        for record in history["attempts"].values():
            record["body"] = ""
            for observation in record["observations"]:
                observation["body"] = ""
                observation["text_digest"] = attempt_text_digest("")
                stable_files = [
                    {key: entry[key] for key in ("key", "filename", "size", "content_type")}
                    for key in observation["file_keys"]
                    for entry in record.get("files", []) if entry.get("key") == key
                ]
                observation["digest"] = _observation_digest("", stable_files)
        history["attempts"].pop(f"{PSEUDONYM}|2|{ATTEMPT_TWO}")
    if conflict:
        key = f"{PSEUDONYM}|1|{ATTEMPT_ONE}"
        record = history["attempts"][key]
        alternate_body = "Alternate synthetic first draft"
        record["observations"].append({
            "digest": _observation_digest(alternate_body, []), "captured_at": "2026-08-14T11:00:00Z",
            "body": alternate_body, "text_digest": attempt_text_digest(alternate_body),
            "body_provenance": "canvas_attempt", "file_keys": [], "conflict": True,
        })
        record["conflict"] = True
        # The identical Canvas attempt key has been observed with two submitted times.
        alternate = dict(record)
        alternate["submitted_at"] = "2026-08-12T09:16:00Z"
        history["attempts"][f"{PSEUDONYM}|1|2026-08-12T09:16:00Z"] = alternate
    history_dir = _history_dir(root)
    _write_json(history_dir / "history.v1.json", history)
    if rich:
        blob = history_dir / "files" / f"{PAYLOAD_SHA256}.txt"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(PAYLOAD)


def _write_new_quiz_inventory(root: Path) -> None:
    quiz_dir = _course(root) / "new_quizzes" / QUIZ_ASSIGNMENT
    _write_json(quiz_dir / "quiz.v2.json", {
        "schema_version": 2, "course_id": COURSE, "assignment_id": QUIZ_ASSIGNMENT,
        "state": "current", "last_success_at": CAPTURED,
        "last_attempt_at": CAPTURED, "error_code": "",
        "assignment": {"id": QUIZ_ASSIGNMENT, "name": "Synthetic quiz"},
        "quiz": {"id": "96004", "title": "Synthetic quiz", "quiz_type": "new_quiz"},
        "items": {"item-1": {"id": "item-1", "points_possible": 1}},
    })
    _write_json(quiz_dir / "students" / "synthetic-student.v2.json", {
        "schema_version": 2, "course_id": COURSE, "assignment_id": QUIZ_ASSIGNMENT,
        "user_id": "synthetic-user-9", "state": "current",
        "last_success_at": CAPTURED, "last_attempt_at": CAPTURED, "error_code": "",
        "latest_attempt": 1, "current": {"attempt": 1, "responses": [{"text": "Inventory only"}]},
        "attempts": [{"attempt": 1, "responses": [{"text": "Inventory only"}]}],
    })


def legacy_root(tmp_path: Path, variant: str = "rich") -> Path:
    """Create a deterministic synthetic legacy store; return its workspace root.

    Variants: ``sparse``, ``rich``, ``timestamp_conflict``, ``malformed_manifest``,
    ``malformed_file``, ``computer_a``, ``computer_b``, and ``new_quiz_inventory``.
    ``computer_a`` and ``computer_b`` contribute distinct local attempts.
    """
    allowed = {"sparse", "rich", "timestamp_conflict", "malformed_manifest",
               "malformed_file", "computer_a", "computer_b", "new_quiz_inventory"}
    if variant not in allowed:
        raise ValueError(f"unknown legacy sample variant: {variant}")
    root = Path(tmp_path) / variant
    root.mkdir(parents=True, exist_ok=True)
    if variant != "new_quiz_inventory":
        _write_ordinary(root, rich=variant not in {"sparse"},
                        conflict=variant == "timestamp_conflict")
    else:
        _write_json(_submission(root), {
            "schema_version": 1, "course_id": COURSE, "assignment_id": ASSIGNMENT,
            "state": "current", "last_success_at": CAPTURED,
            "last_attempt_at": CAPTURED, "error_code": "", "submissions": {},
        })
    if variant == "malformed_manifest":
        (_history_dir(root) / "history.v1.json").write_text("{broken", encoding="utf-8")
    if variant == "malformed_file":
        (_history_dir(root) / "files" / f"{PAYLOAD_SHA256}.txt").write_bytes(b"corrupt bytes")
    if variant in {"computer_a", "computer_b"}:
        # Each computer has its own local mirror and a distinct captured attempt.
        number = 3 if variant == "computer_a" else 4
        timestamp = f"2026-08-{14 if number == 3 else 15:02d}T12:00:00Z"
        body = f"Synthetic computer {variant[-1]} draft"
        history_path = _history_dir(root) / "history.v1.json"
        manifest = json.loads(history_path.read_text(encoding="utf-8"))
        key = f"{PSEUDONYM}|{number}|{timestamp}"
        digest = _observation_digest(body, [])
        manifest["attempts"][key] = {
            "pseudonym": PSEUDONYM, "attempt": number, "submitted_at": timestamp,
            "captured_at": CAPTURED, "body": body, "files": [], "conflict": False,
            "observations": [{"digest": digest, "captured_at": CAPTURED, "body": body,
                              "text_digest": attempt_text_digest(body),
                              "body_provenance": "canvas_attempt", "file_keys": [],
                              "conflict": False}],
        }
        _write_json(history_path, manifest)
    if variant == "new_quiz_inventory":
        _write_new_quiz_inventory(root)
    return root


def sample_metadata(variant: str = "rich") -> dict:
    """Return stable identities and source shape metadata for assertions."""
    return {"variant": variant, "course_id": COURSE, "assignment_id": ASSIGNMENT,
            "pseudonym": PSEUDONYM, "quiz_assignment_id": QUIZ_ASSIGNMENT,
            "attempt_timestamps": [ATTEMPT_ONE, ATTEMPT_TWO],
            "retained_blob_sha256": PAYLOAD_SHA256}


def expected_observations(variant: str = "rich") -> dict:
    """Semantic expectations used by importer tests, independent of file paths."""
    if variant == "new_quiz_inventory":
        return {"ordinary_attempts": [], "inventory_only_quiz_assignment_ids": [QUIZ_ASSIGNMENT]}
    attempts = [
        {"attempt": 1, "submitted_at": ATTEMPT_ONE,
         "body": "First synthetic draft" if variant not in {"sparse", "malformed_manifest", "malformed_file"} else ""},
    ]
    if variant not in {"sparse", "malformed_manifest", "malformed_file"}:
        attempts.append({"attempt": 2, "submitted_at": ATTEMPT_TWO, "body": "Second synthetic draft"})
    if variant == "timestamp_conflict":
        attempts.append({"attempt": 1, "submitted_at": "2026-08-12T09:16:00Z",
                         "body": "Alternate synthetic first draft"})
    if variant == "computer_a":
        attempts.append({"attempt": 3, "submitted_at": "2026-08-14T12:00:00Z",
                         "body": "Synthetic computer a draft"})
    if variant == "computer_b":
        attempts.append({"attempt": 4, "submitted_at": "2026-08-15T12:00:00Z",
                         "body": "Synthetic computer b draft"})
    return {"ordinary_attempts": attempts,
            "retained_original_sha256": PAYLOAD_SHA256 if variant in {"rich", "timestamp_conflict", "computer_a", "computer_b"} else None,
            "malformed_source": variant in {"malformed_manifest", "malformed_file"},
            "timestamp_conflict": variant == "timestamp_conflict"}
