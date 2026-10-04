"""Read-only inventory of the pilot's bounded legacy evidence locations.

Results are private in-memory data, never an agent-safe serialization contract.
No workspace discovery, identity rehydration, quarantine, or writes occur here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import ntpath
from pathlib import Path
import re
from typing import Iterable

from api import course_catalog
from api.mirror import new_quizzes, store

MAX_SOURCE_BYTES = 128 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_NAMED_ID = re.compile(r"^.+ — ([A-Za-z0-9_-]+)$")


@dataclass(frozen=True)
class LegacySource:
    kind: str
    course_id: str
    assignment_id: str | None
    path: Path
    source_digest: str
    document: dict


@dataclass(frozen=True)
class LegacyGap:
    kind: str
    course_id: str
    assignment_id: str | None
    path: Path
    code: str


@dataclass(frozen=True)
class LegacyInventory:
    sources: tuple[LegacySource, ...]
    gaps: tuple[LegacyGap, ...]


def _history(document, course, assignment):
    if (not isinstance(document, dict)
            or set(document) != {"schema_version", "course_id", "assignment_id", "revision", "updated_at", "attempts"}
            or document["schema_version"] != 1
            or document["course_id"] != course or document["assignment_id"] != assignment
            or type(document["revision"]) is not int or document["revision"] < 0
            or not isinstance(document["updated_at"], str)
            or not isinstance(document["attempts"], dict)):
        raise ValueError("invalid history")
    for attempt in document["attempts"].values():
        if (not isinstance(attempt, dict) or not isinstance(attempt.get("pseudonym"), str)
                or type(attempt.get("attempt")) is not int
                or not isinstance(attempt.get("submitted_at"), str)
                or not isinstance(attempt.get("observations"), list)
                or not isinstance(attempt.get("files", []), list)):
            raise ValueError("invalid attempt")
        files = attempt.get("files", [])
        for observation in attempt["observations"]:
            if (not isinstance(observation, dict)
                    or not _HEX.fullmatch(str(observation.get("digest", "")))
                    or not isinstance(observation.get("body"), str)
                    or not isinstance(observation.get("captured_at"), str)
                    or not isinstance(observation.get("file_keys"), list)):
                raise ValueError("invalid observation")
            associated = []
            for key in observation["file_keys"]:
                matches = [item for item in files if isinstance(item, dict) and item.get("key") == key]
                if len(matches) != 1:
                    raise ValueError("invalid file association")
                associated.append({field: matches[0][field] for field in ("key", "filename", "size", "content_type")})
            payload = json.dumps({"body": observation["body"], "files": associated}, ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":")).encode("utf-8")
            if hashlib.sha256(payload).hexdigest() != observation["digest"]:
                raise ValueError("observation digest mismatch")
    return document


def _ordinary_manifest(document, course, assignment):
    required = {"version", "course_id", "assignment_id", "refreshed_at", "status",
                "assignment_indicators", "assignment_name", "evidence",
                "binary_budget_bytes", "binary_bytes_reserved"}
    allowed = required | {"media_budget_bytes", "media_bytes_reserved"}
    if (not isinstance(document, dict) or not required.issubset(document)
            or not set(document).issubset(allowed) or document["version"] != 1
            or str(document["course_id"]) != course or str(document["assignment_id"]) != assignment
            or document["status"] not in {"current", "incomplete"}
            or not isinstance(document["refreshed_at"], str)
            or not isinstance(document["assignment_name"], str)
            or not isinstance(document["assignment_indicators"], dict)
            or not isinstance(document["evidence"], list)):
        raise ValueError("invalid evidence manifest")
    for entry in document["evidence"]:
        if (not isinstance(entry, dict) or entry.get("kind") not in {"ordinary", "new_quiz", "media_recording"}
                or str(entry.get("course_id")) != course or str(entry.get("assignment_id")) != assignment
                or not isinstance(entry.get("user_id"), str)
                or not isinstance(entry.get("evidence_id"), str)):
            raise ValueError("invalid evidence record")
        # Incomplete records may have no acquired path. Never accept an escape
        # locator for downstream migration, even though this reader opens none.
        for field in ("relative_path", "original_relative_path"):
            relative = entry.get(field)
            if relative is None or relative == "":
                continue
            if (not isinstance(relative, str) or ntpath.isabs(relative)
                    or ntpath.splitdrive(relative)[0]
                    or ".." in relative.replace("\\", "/").split("/")):
                raise ValueError("unsafe evidence locator")
    return document


def collect_legacy_sources(*, workspace_root: Path, mirror_cache_root: Path,
                           course_ids: Iterable[str] | None = None) -> LegacyInventory:
    """Inventory exact cache/archive layouts without following linked directories.

    Catalog lives beside the machine's Canvas Mirror cache. Absent optional
    artifacts are not corruption; discovered unsupported artifacts are gaps.
    Each source digest hashes the exact bytes that were parsed and validated.
    """
    workspace_root = Path(workspace_root).absolute()
    mirror_cache_root = Path(mirror_cache_root).absolute()
    history_root = workspace_root / "_System" / "Archive" / "Submission History"
    catalog_root = mirror_cache_root.parent / "Canvas Catalog"
    sources, gaps = [], []

    def gap(kind, course, assignment, path, code):
        gaps.append(LegacyGap(kind, course, assignment, path, code))

    def secure(path, anchor):
        try:
            path.relative_to(anchor)
            for part in (path, *path.parents):
                if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                    return False
                if part == anchor:
                    break
            return path.resolve().is_relative_to(anchor.resolve())
        except (OSError, ValueError):
            return False

    def children(path, anchor, kind, course="", assignment=None):
        if not secure(path, anchor):
            gap(kind, course, assignment, path, "unsafe_path")
            return []
        try:
            return sorted(path.iterdir(), key=lambda p: p.name) if path.exists() else []
        except OSError:
            gap(kind, course, assignment, path, "unreadable_source")
            return []

    def read(path, anchor, kind, course, assignment, validator):
        if not secure(path, anchor):
            gap(kind, course, assignment, path, "unsafe_path")
            return
        if not path.exists():
            return
        try:
            with path.open("rb") as handle:
                payload = handle.read(MAX_SOURCE_BYTES + 1)
            if len(payload) > MAX_SOURCE_BYTES:
                gap(kind, course, assignment, path, "source_too_large")
                return
            document = json.loads(payload.decode("utf-8"))
            validator(document)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
            gap(kind, course, assignment, path, "invalid_source")
            return
        sources.append(LegacySource(kind, course, assignment, path,
                                    hashlib.sha256(payload).hexdigest(), document))

    roots = ((mirror_cache_root, mirror_cache_root), (history_root, workspace_root),
             (catalog_root, mirror_cache_root.parent))
    ordinary_folders = []
    for course_folder in children(workspace_root / "Student Work" / "Submissions", workspace_root,
                                  "ordinary_evidence_manifest"):
        match = _NAMED_ID.fullmatch(course_folder.name)
        if not match:
            continue
        course = match.group(1)
        for assignment_folder in children(course_folder / "Assignments", workspace_root,
                                          "ordinary_evidence_manifest", course):
            assignment_match = _NAMED_ID.fullmatch(assignment_folder.name)
            if assignment_match:
                ordinary_folders.append((course, assignment_match.group(1), assignment_folder))
    if course_ids is None:
        courses = {course for course, _, _ in ordinary_folders}
        for root, anchor in roots:
            for path in children(root, anchor, "inventory"):
                if _ID.fullmatch(path.name) and (path.is_dir() or path.is_symlink()):
                    courses.add(path.name)
                else:
                    gap("inventory", "", None, path, "unsupported_record")
    else:
        courses = set(map(str, course_ids))
    for course in sorted(courses):
        if not _ID.fullmatch(course):
            gap("inventory", course, None, mirror_cache_root, "invalid_identity")
            continue
        for source_course, assignment, folder in ordinary_folders:
            if source_course != course:
                continue
            manifests = [path for path in children(folder, workspace_root,
                         "ordinary_evidence_manifest", course, assignment)
                         if "assignment_evidence_manifest" in path.name and path.name.endswith(".json")]
            competing = any(path.name != "_assignment_evidence_manifest.json" for path in manifests)
            for path in manifests:
                if competing:
                    gap("ordinary_evidence_manifest", course, assignment, path, "manifest_conflict")
                read(path, workspace_root, "ordinary_evidence_manifest", course, assignment,
                     lambda d: _ordinary_manifest(d, course, assignment))
        base = mirror_cache_root / course
        known_mirror = {"roster.v1.json", "groups.v1.json", "assignments.v1.json",
                        "_sync.v1.json", "_refresh.v1.json", "course_context.v1.json",
                        "new_quiz_capability.v1.json", "submission_comments_state.v1.json"}
        for path in children(base, mirror_cache_root, "inventory", course):
            if path.name.endswith(".json") and path.name not in known_mirror:
                gap("inventory", course, None, path, "unsupported_record")
        for name, kind, validator in (
                ("roster.v1.json", "mirror_roster", store.validate_roster),
                ("groups.v1.json", "mirror_groups", store.validate_groups),
                ("assignments.v1.json", "mirror_assignments", store.validate_assignments)):
            read(base / name, mirror_cache_root, kind, course, None, lambda d, v=validator: v(d, course))
        for path in children(base / "submissions", mirror_cache_root, "mirror_submissions", course):
            assignment = path.name.removesuffix(".v1.json")
            if not path.name.endswith(".v1.json") or not _ID.fullmatch(assignment):
                gap("mirror_submissions", course, None, path, "unsupported_record")
            else:
                read(path, mirror_cache_root, "mirror_submissions", course, assignment,
                     lambda d: store.validate_submissions(d, course, assignment))
        for path in children(history_root / course, workspace_root, "history_manifest", course):
            assignment = path.name
            if not _ID.fullmatch(assignment):
                gap("history_manifest", course, None, path, "unsupported_record")
                continue
            for record in children(path, workspace_root, "history_manifest", course, assignment):
                if record.name.endswith(".json") and record.name != "history.v1.json":
                    gap("history_manifest", course, assignment, record, "unsupported_record")
            read(path / "history.v1.json", workspace_root, "history_manifest", course, assignment,
                 lambda d: _history(d, course, assignment))
        for path in children(catalog_root / course, mirror_cache_root.parent, "course_catalog", course):
            if path.name.endswith(".json") and path.name not in {
                    "catalog.v3.json", "catalog.v3.previous.json", "pending_writes.v1.json"}:
                gap("course_catalog", course, None, path, "unsupported_record")
        read(catalog_root / course / "catalog.v3.json", mirror_cache_root.parent,
             "course_catalog", course, None,
             lambda d: course_catalog.validate_catalog(d) if d.get("course_id") == course else (_ for _ in ()).throw(ValueError()))
        read(catalog_root / course / "catalog.v3.previous.json", mirror_cache_root.parent,
             "course_catalog", course, None,
             lambda d: course_catalog.validate_catalog(d) if d.get("course_id") == course else (_ for _ in ()).throw(ValueError()))
        quizzes = base / "new_quizzes"
        for path in children(quizzes, mirror_cache_root, "new_quiz_inventory", course):
            if path.name == "_sync.v2.json":
                read(path, mirror_cache_root, "new_quiz_inventory", course, None,
                     lambda d: new_quizzes._validate_sync(d, course))
                continue
            assignment = path.name
            if not _ID.fullmatch(assignment):
                gap("new_quiz_inventory", course, None, path, "unsupported_record")
                continue
            read(path / "quiz.v2.json", mirror_cache_root, "new_quiz_inventory", course, assignment,
                 lambda d: new_quizzes._validate_quiz(d, course, assignment))
            for student in children(path / "students", mirror_cache_root, "new_quiz_inventory", course, assignment):
                if not student.name.endswith(".v2.json"):
                    gap("new_quiz_inventory", course, assignment, student, "unsupported_record")
                else:
                    read(student, mirror_cache_root, "new_quiz_inventory", course, assignment,
                         lambda d: new_quizzes._validate_student(d, course, assignment))
    key = lambda item: (item.course_id, item.kind, item.assignment_id or "", str(item.path))
    return LegacyInventory(tuple(sorted(sources, key=key)), tuple(sorted(gaps, key=key)))
