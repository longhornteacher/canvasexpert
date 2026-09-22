"""Canonical workspace ownership: the single source of truth for the v3
synced-workspace tree (``Assignments/``, ``Library/``, ``To Review/``, ``Printables/``,
``Canvas Uploads/``, ``Student Work/``, ``For AI/``, ``_System/``).

The synced workspace is teacher-visible data.  Keep path construction here so
callers cannot accidentally create a second student tree or put new machine
state outside ``_System/``.
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from api import runtime_paths
from engine.utils.text_utils import safe_filename_component

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.dirname(MODULE_DIR)
REPO_ROOT = os.path.dirname(API_DIR)
# Pre-0.75 machine-local config lived inside the app folder, which a
# self-update mirrors wholesale -- see runtime_paths.migrate_legacy_file().
# Kept in step with config/_io.py's own CONFIG_PATH/LEGACY_CONFIG_PATH pair:
# both resolve to the same physical file, computed independently because the
# two modules already read machine config independently (see config/_io.py).
LEGACY_CONFIG_PATH = os.path.join(MODULE_DIR, "config.json")
CONFIG_PATH = str(runtime_paths.local_app_dir() / "config.json")
DEFAULT_DOCS_DIR = os.path.join(API_DIR, "default_docs")
WORKSPACE_NAME = "CanvasExpert"

# The Library: reusable collections the teacher authors or keeps.
LIBRARY_NAME = "Library"
AI_AUTHORING_SUBFOLDER = "AI Authoring"
LEARNING_OBJECTIVES_SUBFOLDER = "Learning Objectives"
FEEDBACK_CONTRACTS_SUBFOLDER = "Feedback Contracts"
LIBRARY_SUBFOLDERS = [
    AI_AUTHORING_SUBFOLDER, "Quizzes", "Pages",
    "Calendars", "Source Materials", LEARNING_OBJECTIVES_SUBFOLDER,
    FEEDBACK_CONTRACTS_SUBFOLDER,
]

# Authored/staged assignment content has one source tree.  ``_Shared`` is
# reusable assignment content; course-owned content is keyed by stable Canvas
# course ID and remains readable through the teacher's nickname.
ASSIGNMENTS_NAME = "Assignments"
SHARED_ASSIGNMENTS_NAME = "_Shared"

# Assistant-staged drafts waiting for the teacher to push to Canvas.
TO_REVIEW_NAME = "To Review"
TO_REVIEW_SUBFOLDERS = ["Quizzes", "Assignments", "Pages"]

# Outputs to print/photocopy vs. Canvas import packages -- the old flat
# "Exports" split by teacher verb.
PRINTABLES_NAME = "Printables"
CANVAS_UPLOADS_NAME = "Canvas Uploads"

# Real-name student data. PRIVATE.
STUDENT_WORK_NAME = "Student Work"
SUBMISSIONS_NAME = "Submissions"
STUDENT_WORK_REPORTS_NAME = "Reports"
GRADING_KEYS_NAME = "Grading Keys"

# Pseudonymized packets safe to hand to an external AI. No real names.
FOR_AI_NAME = "For AI"

SYSTEM_NAME = "_System"
SYSTEM_SUBFOLDERS = ("Identity Vault", "PowerGrader", "Audits", "Archive",
                     "Canvas Catalog", "Canvas Mirror")
CANVAS_CATALOG_NAME = "Canvas Catalog"
CANVAS_MIRROR_NAME = "Canvas Mirror"

MAX_COMPONENT_LENGTH = 120
MAX_PATH_LENGTH = 240
TEACHER_VISIBLE_BUDGET = 230
_BAD_ID = re.compile(r"[^A-Za-z0-9._-]+")

# Deterministic short-hash length for compact path components.
# 8 hex chars → 2^32 namespace, negligible collision risk within one assignment.
_COMPACT_HASH_LENGTH = 8


class TeacherVisiblePathBudgetError(ValueError):
    """Raised when even the compact form of a teacher-visible path exceeds
    the 230-character budget."""


def _deterministic_hash(text: str, length: int = _COMPACT_HASH_LENGTH) -> str:
    """Return a deterministic lowercase hex hash of *text*.

    Uses Python's built-in hash() salted with a fixed seed so results are
    stable across interpreter runs on the same platform.  The hex digest is
    short enough to fit within the path budget.
    """
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


_SHA256_ABBREV = _deterministic_hash


def teacher_visible_path(
    base: str,
    *components: str,
    filename: str = "",
    reserve: int = 0,
) -> str:
    """Build a teacher-visible absolute path guaranteed to be at most 230 characters.

    Parameters
    ----------
    base:
        Absolute base directory (e.g. workspace root).
    *components:
        Ordered path segments to join under *base*.  Each may be a plain
        string or a ``(display, stable_id)`` tuple.  For tuples, pass the
        **raw** display and the **raw** id: this builds ``<display> — <id>``
        itself via ``named_id_folder`` and the stable id portion is always
        preserved in the compact fallback.  Never pre-join the id into the
        display (that yields ``Name — 123 — 123``), and never pass a bare
        constant as a tuple against itself (that yields ``For AI — For AI``).
        A component with no stable id is a plain string.
    filename:
        Optional final file name with extension.
    reserve:
        Extra characters to reserve for projected suffix components that
        will be appended by the caller *after* this call (e.g. batch
        index, extension).  When given, the returned path is *shorter* so
        the caller's full path still fits.

    Returns
    -------
    str
        The projected plain (unprefixed) absolute path, at most
        ``TEACHER_VISIBLE_BUDGET`` characters.

    Raises
    ------
    TeacherVisiblePathBudgetError
        When even the compact form (shortened display portions, stable-id
        suffixes preserved) cannot fit within the budget.
    """
    budget = TEACHER_VISIBLE_BUDGET - reserve
    segments: list[str] = [os.path.abspath(base)]

    # First pass: assemble with full display names.
    raw_segments: list[str] = []
    for c in components:
        if isinstance(c, tuple):
            display, stable_id = c
            # named_id_folder owns the join, so a caller passes the raw display
            # and the raw id and cannot double-append the id into the display.
            raw_segments.append(named_id_folder(display, stable_id))
        else:
            raw_segments.append(str(c))

    # Try full-readable form first.
    def _project(seg: list[str]) -> str:
        parts = [str(s) for s in seg]
        if filename:
            parts.append(str(filename))
        return os.path.join(*parts)

    candidate = _project(segments + raw_segments)
    if len(candidate) <= budget:
        return candidate

    # Compact fallback: shorten display portions, keep stable IDs.  Category
    # roots are part of the privacy/layout contract and must remain visible;
    # hashing them would turn ``For AI`` into an ambiguous top-level folder.
    compact_segments: list[str] = list(segments)
    for c in components:
        if isinstance(c, tuple):
            display, stable_id = c
            # Use a deterministic short hash of the full stable-id-bearing
            # name so that two different assignments with the same stable ID
            # still produce distinct paths.
            short_hash = _deterministic_hash(f"{display}—{stable_id}")
            compact_segments.append(f"{short_hash} — {safe_id(stable_id)}")
        elif str(c) in {FOR_AI_NAME, STUDENT_WORK_NAME, SYSTEM_NAME}:
            compact_segments.append(str(c))
        else:
            # Non-identity component: shorten aggressively.
            short = _deterministic_hash(str(c))
            compact_segments.append(short)

    candidate = _project(compact_segments)
    if len(candidate) <= budget:
        return candidate

    # Strip display portions entirely: keep only the hash.
    minimal_segments: list[str] = list(segments)
    for c in components:
        if isinstance(c, tuple):
            _display, stable_id = c
            minimal_segments.append(stable_id)
        elif str(c) in {FOR_AI_NAME, STUDENT_WORK_NAME, SYSTEM_NAME}:
            minimal_segments.append(str(c))
        else:
            minimal_segments.append(_deterministic_hash(str(c)))

    candidate = _project(minimal_segments)
    if len(candidate) <= budget:
        return candidate

    raise TeacherVisiblePathBudgetError(
        f"Path too deep for teacher-visible output "
        f"(projected {len(candidate)} > {budget} budget)"
    )


def needs_compact_layout(base_dir: str, *deepest_child: str, budget: int = TEACHER_VISIBLE_BUDGET) -> bool:
    """Return True when the readable-name projected path for the deepest
    expected child under *base_dir* would exceed the teacher-visible budget.

    Every writer that falls back to a shorter compact naming scheme on deep
    workspaces (PowerGrader's Safe AI Packet, Copilot batches, SAFE/PRIVATE
    bundle files) should probe with this instead of hand-rolling the same
    path-length comparison.
    """
    projected = os.path.join(os.path.abspath(base_dir), *deepest_child)
    return len(projected) > budget


def _machine_config():
    runtime_paths.migrate_legacy_file(LEGACY_CONFIG_PATH, CONFIG_PATH)
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def onedrive_root():
    for name in ("OneDriveCommercial", "OneDrive"):
        path = os.environ.get(name, "").strip()
        if path:
            return path
    return None


def workspace_root():
    machine = _machine_config()
    override = str(machine.get("workspace_path") or "").strip()
    if override:
        return override
    root = onedrive_root()
    if root:
        return os.path.join(root, WORKSPACE_NAME)
    return None


def safe_component(value, max_len: int = MAX_COMPONENT_LENGTH, fallback: str = "_unnamed") -> str:
    """Return a readable Windows/POSIX-safe path component.

    The result is deliberately not an identity scrubber; Canvas IDs are kept in
    the containing folder name as the stable, private collision suffix.
    """
    return safe_filename_component(value, max_len=max_len, fallback=fallback)


def safe_id(value, fallback: str = "unknown") -> str:
    text = _BAD_ID.sub("_", str(value or "").strip())
    return text.strip("._-") or fallback


def named_id_folder(display_name, stable_id, *, max_len: int = MAX_COMPONENT_LENGTH) -> str:
    """Build ``<readable display> — <stable id>`` without losing the ID."""
    suffix = f" — {safe_id(stable_id)}"
    available = max(1, int(max_len) - len(suffix))
    return safe_component(display_name, available) + suffix


def bounded_join(base: str, *parts: str, max_path: int = MAX_PATH_LENGTH) -> str:
    """Join readable components and shrink display portions for Windows limits."""
    values = [str(base)] + [str(part) for part in parts]
    path = os.path.join(*values)
    if len(path) <= max_path:
        return path
    # Keep the stable ID suffix and shorten human-facing portions first.
    for _ in range(len(values) * 3):
        changed = False
        for index in range(len(values) - 1, 0, -1):
            part = values[index]
            if " — " not in part:
                continue
            label, suffix = part.split(" — ", 1)
            excess = max(1, len(path) - max_path)
            target_len = max(1, len(part) - excess)
            label_len = max(1, target_len - len(suffix) - 3)
            if label_len >= len(label):
                continue
            values[index] = f"{safe_component(label, label_len)} — {suffix}"
            path = os.path.join(*values)
            changed = True
            if len(path) <= max_path:
                break
        if len(path) <= max_path or not changed:
            break
    return path


def extended_path(path: str) -> str:
    """Return a form of ``path`` safe for file I/O past Windows' 260-char limit.

    ``bounded_join`` keeps our *directory* names short, but a deep workspace
    (e.g. a long OneDrive root + long course/assignment names) can still push a
    full *file* path over the legacy MAX_PATH ceiling — at which point
    ``open()``/``makedirs`` raise ``FileNotFoundError [Errno 2]`` even though the
    parent directory exists. On Windows, prefixing an absolute, backslash-only
    path with ``\\\\?\\`` opts that single call out of MAX_PATH (raising the
    limit to ~32,767), the supported way to reach such files without the OS-wide
    LongPathsEnabled policy.

    The prefix is applied ONLY when the absolute path approaches the limit
    (>= ``MAX_PATH_LENGTH``). Short paths are returned unchanged so the vast
    majority of I/O keeps its exact current behavior — the ``\\\\?\\`` form
    disables normalization and has subtle edge cases, so it is used only where a
    normal call would actually fail. Non-Windows paths and already-prefixed
    paths are returned as-is (idempotent).
    """
    if os.name != "nt" or not path:
        return path
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        return path
    abs_path = os.path.abspath(path)
    if len(abs_path) < MAX_PATH_LENGTH:
        return path
    if abs_path.startswith("\\\\"):
        # UNC share: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + abs_path[2:]
    return "\\\\?\\" + abs_path


def _root_or_workspace(root=None):
    return root if root is not None else workspace_root()


def _join_root(name: str, root=None):
    base = _root_or_workspace(root)
    return os.path.join(base, name) if base else None


def folder(name):
    return _join_root(name)


def library_root(root=None):
    return _join_root(LIBRARY_NAME, root)


def library_folder(name, root=None):
    base = library_root(root)
    return os.path.join(base, name) if base else None


def assignments_root(root=None):
    """Return the sole authored-assignment source root."""
    return _join_root(ASSIGNMENTS_NAME, root)


def shared_assignments_root(root=None):
    base = assignments_root(root)
    return os.path.join(base, SHARED_ASSIGNMENTS_NAME) if base else None


def course_assignments_root(course_id, course_nickname="", root=None):
    """Return ``Assignments/<course-id> - <nickname>``.

    The ID is always present; a missing nickname does not create a second
    naming scheme or silently fall back to the old Library tree.
    """
    base = assignments_root(root)
    if not base:
        return None
    course_id_text = safe_id(course_id, "unknown-course")
    available = max(1, MAX_COMPONENT_LENGTH - len(course_id_text) - 3)
    nickname = safe_component(course_nickname or "Course", available)
    return os.path.join(base, f"{course_id_text} - {nickname}")


def assignment_source_folder(course_id, course_nickname="", assignment_id="",
                             assignment_name="", root=None):
    """Resolve one authored assignment below the canonical course/shared tree."""
    parent = (shared_assignments_root(root) if not course_id else
              course_assignments_root(course_id, course_nickname, root))
    if not parent:
        return None
    if not assignment_id and not assignment_name:
        return parent
    label = safe_component(assignment_name or "Assignment", MAX_COMPONENT_LENGTH)
    stable = safe_id(assignment_id, _deterministic_hash(assignment_name or label))
    return os.path.join(parent, f"{label} — {stable}")


def to_review_root(root=None):
    return _join_root(TO_REVIEW_NAME, root)


def to_review_folder(name, root=None):
    base = to_review_root(root)
    return os.path.join(base, name) if base else None


def printables_root(root=None):
    return _join_root(PRINTABLES_NAME, root)


def canvas_uploads_root(root=None):
    return _join_root(CANVAS_UPLOADS_NAME, root)


def student_work_root(root=None):
    return _join_root(STUDENT_WORK_NAME, root)


def submissions_root(root=None):
    base = student_work_root(root)
    return os.path.join(base, SUBMISSIONS_NAME) if base else None


def student_work_reports_root(root=None):
    base = student_work_root(root)
    return os.path.join(base, STUDENT_WORK_REPORTS_NAME) if base else None


def grading_keys_root(root=None):
    base = student_work_root(root)
    return os.path.join(base, GRADING_KEYS_NAME) if base else None


def for_ai_root(root=None):
    return _join_root(FOR_AI_NAME, root)


def system_root(root=None):
    return _join_root(SYSTEM_NAME, root)


def system_folder(name: str | None = None, root=None):
    base = system_root(root)
    if not base:
        return None
    return os.path.join(base, name) if name else base


def identity_vault_dir(root=None):
    return system_folder("Identity Vault", root)


def powergrader_root(root=None):
    return system_folder("PowerGrader", root)


def powergrader_sessions_dir(root=None):
    base = powergrader_root(root)
    return os.path.join(base, "Sessions") if base else None


def powergrader_jobs_dir(root=None):
    base = powergrader_root(root)
    return os.path.join(base, "Jobs") if base else None


def audits_dir(root=None):
    return system_folder("Audits", root)


def archive_dir(root=None):
    return system_folder("Archive", root)


def canvas_catalog_root(root=None):
    """Return the durable, student-data-free Canvas Catalog root."""
    return system_folder(CANVAS_CATALOG_NAME, root)


def course_catalog_dir(course_id, root=None):
    """Return the stable per-course catalog directory owned by Canvas course ID."""
    base = canvas_catalog_root(root)
    return os.path.join(base, safe_id(course_id)) if base else None


def course_catalog_v3_path(course_id, root=None):
    directory = course_catalog_dir(course_id, root)
    return os.path.join(directory, "catalog.v3.json") if directory else None


def course_catalog_v3_previous_path(course_id, root=None):
    directory = course_catalog_dir(course_id, root)
    return os.path.join(directory, "catalog.v3.previous.json") if directory else None


def learning_objectives_path(root=None):
    """The sole canonical reviewed Learning Objectives document."""
    directory = library_folder(LEARNING_OBJECTIVES_SUBFOLDER, root)
    return os.path.join(directory, "Learning Objectives.json") if directory else None


def canvas_mirror_root(root=None):
    """Return the CanvasMirror root — the disposable local mirror of Canvas
    course facts. Everything under it is rebuildable by re-sync."""
    return system_folder(CANVAS_MIRROR_NAME, root)


def course_mirror_dir(course_id, root=None):
    """Return the stable per-course mirror directory owned by Canvas course ID."""
    base = canvas_mirror_root(root)
    return os.path.join(base, safe_id(course_id)) if base else None


def course_folder(course_name, course_id, root=None):
    base = _root_or_workspace(root)
    return bounded_join(base, STUDENT_WORK_NAME, SUBMISSIONS_NAME,
                       named_id_folder(course_name, course_id)) if base else None


def assignment_folder(course_name, course_id, assignment_name, assignment_id, root=None):
    """Downloaded-evidence home for one assignment (course-first, matches the
    download flow). Holds the evidence manifest and per-student attempt
    folders. Distinct from ``grading_keys_assignment_folder``, which holds
    PowerGrader's unscrubbed PRIVATE copy and who-is-who decoder for the same
    assignment -- the two are separate shelves under ``Student Work/``."""
    base = _root_or_workspace(root)
    if not base:
        return None
    return bounded_join(base, STUDENT_WORK_NAME, SUBMISSIONS_NAME, named_id_folder(course_name, course_id),
                       "Assignments", named_id_folder(assignment_name, assignment_id))


def student_folder(course_name, course_id, assignment_name, assignment_id,
                   student_name, user_id, root=None):
    base = _root_or_workspace(root)
    # sortable_name is already ``Last, First`` when Canvas provides it.  The
    # caller may pass either form; preserving the display text is intentional.
    return bounded_join(base, STUDENT_WORK_NAME, SUBMISSIONS_NAME, named_id_folder(course_name, course_id),
                        "Assignments", named_id_folder(assignment_name, assignment_id),
                        named_id_folder(student_name, user_id)) if base else None


def attempt_folder(course_name, course_id, assignment_name, assignment_id,
                   student_name, user_id, attempt=1, root=None):
    base = _root_or_workspace(root)
    attempt_text = safe_id(attempt, "1")
    return bounded_join(base, STUDENT_WORK_NAME, SUBMISSIONS_NAME, named_id_folder(course_name, course_id),
                        "Assignments", named_id_folder(assignment_name, assignment_id),
                        named_id_folder(student_name, user_id),
                        f"Attempt {attempt_text}") if base else None


def grading_keys_assignment_folder(course_name, course_id, assignment_name, assignment_id,
                                   root=None, *, reserve=0):
    """PowerGrader's unscrubbed PRIVATE bundle + who-is-who decoder for one
    assignment (course-first). Never enters ``For AI/``. Carries the hard
    230-char teacher-visible budget because PowerGrader writes several more
    child path segments (packet folder, batch folder, batch file) beneath it;
    ``reserve`` accounts for that projected depth."""
    base = _root_or_workspace(root)
    if not base:
        return None
    if reserve:
        return teacher_visible_path(
            base,
            STUDENT_WORK_NAME,
            GRADING_KEYS_NAME,
            (course_name, course_id),
            "Assignments",
            (assignment_name, assignment_id),
            reserve=reserve,
        )
    return bounded_join(base, STUDENT_WORK_NAME, GRADING_KEYS_NAME, named_id_folder(course_name, course_id),
                       "Assignments", named_id_folder(assignment_name, assignment_id))


# Leading underscore matches the app's other internal-bookkeeping sidecar
# files (``_manifest.json``, ``_source_manifest.json``): this file lives
# inside a folder the teacher browses (alongside per-student attempt
# folders) but isn't meant for them to open.
ASSIGNMENT_EVIDENCE_MANIFEST = "_assignment_evidence_manifest.json"


def assignment_evidence_manifest_path(course_name, course_id, assignment_name, assignment_id, root=None):
    """Return the private, canonical manifest path for one assignment."""
    folder = assignment_folder(course_name, course_id, assignment_name, assignment_id, root)
    return os.path.join(folder, ASSIGNMENT_EVIDENCE_MANIFEST) if folder else None


def managed_evidence_path(course_name, course_id, assignment_name, assignment_id,
                          student_name, user_id, attempt, evidence_id, filename, root=None):
    """Return a deterministic managed-original path; filenames are never identity.

    The stable evidence ID is a suffix after the readable filename --
    ``<name> — <id><ext>`` -- matching ``named_id_folder``'s "<display> — <id>"
    convention rather than leading with the ID. This also keeps
    ``bounded_join``'s length-shortening pass shrinking the display name, not
    the identity suffix, if the path ever needs to shrink.
    """
    base = attempt_folder(course_name, course_id, assignment_name, assignment_id,
                          student_name, user_id, attempt, root)
    if not base or not evidence_id:
        return None
    stem, ext = os.path.splitext(safe_component(filename, 150))
    return bounded_join(base, f"{stem} — {safe_id(evidence_id)}{ext}")


def assignment_evidence_conflicts(course_name, course_id, assignment_name, assignment_id, root=None):
    """Find OneDrive-style competing manifests without selecting or modifying either."""
    path = assignment_evidence_manifest_path(course_name, course_id, assignment_name, assignment_id, root)
    if not path:
        return []
    directory = os.path.dirname(path)
    if not os.path.isdir(extended_path(directory)):
        return []
    # os.listdir(extended) + fnmatch instead of glob: it enumerates a deep
    # (>260) directory correctly and keeps candidates as plain paths, so the
    # canonical-path comparison below is unaffected by any \\?\ prefix.
    canonical = os.path.normcase(os.path.abspath(path))
    conflicts = []
    for name in os.listdir(extended_path(directory)):
        if not fnmatch.fnmatch(name, "*assignment_evidence_manifest*.json"):
            continue
        candidate = os.path.join(directory, name)
        if os.path.normcase(os.path.abspath(candidate)) != canonical:
            conflicts.append(candidate)
    return conflicts


def read_assignment_evidence_manifest(course_name, course_id, assignment_name, assignment_id, root=None):
    path = assignment_evidence_manifest_path(course_name, course_id, assignment_name, assignment_id, root)
    if not path or assignment_evidence_conflicts(course_name, course_id, assignment_name, assignment_id, root):
        return None
    try:
        with open(extended_path(path), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or str(data.get("course_id")) != str(course_id) or str(data.get("assignment_id")) != str(assignment_id):
        return None
    return data


def write_assignment_evidence_manifest(manifest: dict, *, course_name, course_id, assignment_name, assignment_id, root=None):
    """Atomically replace a validated private manifest after evidence is finalized."""
    path = assignment_evidence_manifest_path(course_name, course_id, assignment_name, assignment_id, root)
    if not path or str(manifest.get("course_id")) != str(course_id) or str(manifest.get("assignment_id")) != str(assignment_id):
        return None
    if assignment_evidence_conflicts(course_name, course_id, assignment_name, assignment_id, root):
        return None
    # os-level via extended_path so a deep assignment folder survives Windows'
    # 260-char limit; mkstemp(dir=extended) yields an already-prefixed temporary.
    os.makedirs(extended_path(os.path.dirname(path)), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".assignment_evidence_", suffix=".partial", dir=extended_path(os.path.dirname(path)), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, extended_path(path))
        return path
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ai_assignment_root(course_name, course_id, assignment_name, assignment_id, root=None):
    base = _root_or_workspace(root)
    if not base:
        return None
    return bounded_join(base, FOR_AI_NAME, named_id_folder(course_name, course_id),
                        named_id_folder(assignment_name, assignment_id))


RUN_STAMP_FORMAT = "%Y%m%d-%H%M%S-%f"


def run_stamp() -> str:
    """Return a fresh, sortable, collision-safe local-time stamp.

    The one shared format for teacher-visible run/batch names -- PowerGrader
    "For AI/" run folders, late-catchup batch labels, vault backups -- so
    they read consistently instead of each caller picking its own.
    """
    return datetime.now().strftime(RUN_STAMP_FORMAT)


def ai_run_folder(course_name, course_id, assignment_name, assignment_id,
                  mode="assisted", *, run_timestamp=None, root=None, reserve=0):
    base = _root_or_workspace(root)
    if not base:
        return None
    stamp = run_timestamp or run_stamp()
    if reserve:
        return teacher_visible_path(
            base,
            FOR_AI_NAME,
            (course_name, course_id),
            (assignment_name, assignment_id),
            f"{safe_component(stamp, 32)} — {safe_component(mode, 32)}",
            reserve=reserve,
        )
    return bounded_join(base, FOR_AI_NAME, named_id_folder(course_name, course_id),
                        named_id_folder(assignment_name, assignment_id),
                        f"{safe_component(stamp, 32)} — {safe_component(mode, 32)}")


def ai_student_folder(course_name, course_id, assignment_name, assignment_id,
                      mode, pseudonym, *, run_timestamp=None, root=None):
    base = _root_or_workspace(root)
    if not base:
        return None
    stamp = run_timestamp or run_stamp()
    return bounded_join(base, FOR_AI_NAME, named_id_folder(course_name, course_id),
                        named_id_folder(assignment_name, assignment_id),
                        f"{safe_component(stamp, 32)} — {safe_component(mode, 32)}",
                        "Students", safe_component(pseudonym, 80))


def _ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def _seed_folder_if_missing(source_dir, target_dir):
    if not os.path.isdir(source_dir):
        return
    for root, _, files in os.walk(source_dir):
        rel_dir = os.path.relpath(root, source_dir)
        dest_root = target_dir if rel_dir == "." else os.path.join(target_dir, rel_dir)
        os.makedirs(dest_root, exist_ok=True)
        for name in files:
            source = os.path.join(root, name)
            target = os.path.join(dest_root, name)
            if os.path.exists(target):
                continue
            shutil.copy2(source, target)


def _seed_workspace_readme(root):
    path = os.path.join(root, "README (workspace privacy).txt")
    if os.path.exists(path):
        return path
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "Canvas Expert workspace\n"
            "=======================\n\n"
            "Real names live only in Student Work/ -- it is PRIVATE.\n"
            "  Submissions/  downloaded evidence, by course\n"
            "  Reports/      derived teacher reports, by student\n"
            "  Grading Keys/ who-is-who crosswalks + unscrubbed PowerGrader copies\n\n"
            "For AI/ is the pseudonymized counterpart -- safe to hand to an external AI.\n"
            "Review every file before sharing; pseudonyms do not guarantee anonymity and\n"
            "visible content may still identify a student.\n\n"
            "Assignments/ is the sole authored-assignment source. It contains\n"
            "_Shared/ plus one <course-id> - <nickname>/ folder per course.\n"
            "Library/ holds reusable non-assignment material (quizzes, pages,\n"
            "calendars, source materials, AI Authoring instructions).\n\n"
            "To Review/ holds pending assistant drafts. Forge drafts wait for Canvas review and push.\n\n"
            "Printables/ is for PDF/DOCX output to print or photocopy.\n"
            "Canvas Uploads/ holds QTI/.imscc import packages.\n\n"
            "_System/ contains the identity vault, PowerGrader state, and audit files; it is PRIVATE.\n"
        )
    return path


def ensure_workspace():
    """Create the v3 canonical tree and seed defaults."""
    root = workspace_root()
    if not root:
        return None
    os.makedirs(root, exist_ok=True)

    os.makedirs(os.path.join(root, LIBRARY_NAME), exist_ok=True)
    for subfolder in LIBRARY_SUBFOLDERS:
        target_dir = os.path.join(root, LIBRARY_NAME, subfolder)
        os.makedirs(target_dir, exist_ok=True)
        # Feedback Contracts are seeded by their marker-gated config owner.
        # The generic copy-on-every-ensure path would resurrect a teacher's
        # deliberate deletion after the one-time starter seed.
        if subfolder != FEEDBACK_CONTRACTS_SUBFOLDER:
            _seed_folder_if_missing(os.path.join(DEFAULT_DOCS_DIR, subfolder), target_dir)
    os.makedirs(shared_assignments_root(root), exist_ok=True)
    os.makedirs(assignments_root(root), exist_ok=True)
    os.makedirs(os.path.join(root, TO_REVIEW_NAME), exist_ok=True)
    for subfolder in TO_REVIEW_SUBFOLDERS:
        os.makedirs(os.path.join(root, TO_REVIEW_NAME, subfolder), exist_ok=True)

    for canonical in (PRINTABLES_NAME, CANVAS_UPLOADS_NAME, FOR_AI_NAME):
        os.makedirs(os.path.join(root, canonical), exist_ok=True)

    os.makedirs(os.path.join(root, STUDENT_WORK_NAME), exist_ok=True)
    for subfolder in (SUBMISSIONS_NAME, STUDENT_WORK_REPORTS_NAME, GRADING_KEYS_NAME):
        os.makedirs(os.path.join(root, STUDENT_WORK_NAME, subfolder), exist_ok=True)

    for sub in SYSTEM_SUBFOLDERS:
        os.makedirs(os.path.join(root, SYSTEM_NAME, sub), exist_ok=True)
    os.makedirs(os.path.join(root, SYSTEM_NAME, "PowerGrader", "Sessions"), exist_ok=True)
    os.makedirs(os.path.join(root, SYSTEM_NAME, "PowerGrader", "Jobs"), exist_ok=True)

    _seed_workspace_readme(root)
    return root


def migrate_legacy_panels_folder(root=None):
    """One-time cleanup of the old Library/Panels folder.

    Library/Panels was named for the classroom display, which is gone. It also
    held the canonical Learning Objectives document, which is not, so that
    document moves to Library/Learning Objectives and the rest of the folder
    is deleted. Nothing else in there outlived the display.

    The one thing this will not delete is an objectives document it could not
    move. If a document already exists at the destination (two machines
    syncing, or a half-finished earlier run) the old folder is left alone
    rather than taking an authored document down with it. Idempotent: once the
    folder is gone this is a permanent no-op.
    """
    base = _root_or_workspace(root)
    if not base:
        return
    legacy_dir = os.path.join(base, LIBRARY_NAME, "Panels")
    if not os.path.isdir(legacy_dir):
        return

    document = "Learning Objectives.json"
    source = os.path.join(legacy_dir, document)
    target_dir = os.path.join(base, LIBRARY_NAME, LEARNING_OBJECTIVES_SUBFOLDER)
    target = os.path.join(target_dir, document)
    if os.path.isfile(source):
        if os.path.exists(target):
            return
        try:
            os.makedirs(target_dir, exist_ok=True)
            shutil.move(source, target)
        except OSError:
            return

    try:
        shutil.rmtree(legacy_dir)
    except OSError:
        return


def path_within_workspace(path: str, root=None) -> bool:
    base = _root_or_workspace(root)
    if not base or not path:
        return False
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(base)]) == os.path.realpath(base)
    except ValueError:
        return False


# --- Explicit clean-slate workspace reset ---------------------------------

_RESET_HASH_ROOT = re.compile(r"^[0-9a-f]{8}$", re.IGNORECASE)
_RESET_SCORING_NAMES = ("packet", "session", "export", "scoring")
_RESET_DATED_EXPORT = re.compile(r"^(sage scores|scores?\s*[-_])", re.IGNORECASE)
_RESET_HASH_MARKERS = ("safe", "private", "evidence", "student", "grading", "packet")


def _tree_counts(path: str) -> tuple[int, int]:
    files = directories = 0
    if os.path.isdir(extended_path(path)):
        for _dir, dirnames, filenames in os.walk(extended_path(path)):
            directories += len(dirnames)
            files += len(filenames)
    elif os.path.exists(extended_path(path)):
        files = 1
    return files, directories


def _reset_report_item(path: str, category: str) -> dict:
    files, directories = _tree_counts(path)
    return {"category": category, "path": os.path.abspath(path),
            "files": files, "directories": directories}


def _reset_candidates(root: str) -> tuple[list[dict], list[dict]]:
    """Return deletable items and explicit refusals without mutating state."""
    candidates: list[dict] = []
    refused: list[dict] = []

    def add_children(parent: str, category: str) -> None:
        if not os.path.isdir(extended_path(parent)):
            return
        for name in os.listdir(extended_path(parent)):
            candidates.append(_reset_report_item(os.path.join(parent, name), category))

    add_children(os.path.join(root, LIBRARY_NAME, ASSIGNMENTS_NAME), "legacy_assignments")
    add_children(os.path.join(root, ASSIGNMENTS_NAME), "assignments")
    add_children(os.path.join(root, FOR_AI_NAME), "for_ai")

    student_root = os.path.join(root, STUDENT_WORK_NAME)
    for name in (SUBMISSIONS_NAME, GRADING_KEYS_NAME, STUDENT_WORK_REPORTS_NAME):
        add_children(os.path.join(student_root, name), f"student_work/{name}")
    if os.path.isdir(extended_path(student_root)):
        allowed = {SUBMISSIONS_NAME, GRADING_KEYS_NAME, STUDENT_WORK_REPORTS_NAME}
        for name in os.listdir(extended_path(student_root)):
            if name not in allowed:
                refused.append({"category": "student_work", "path": os.path.abspath(os.path.join(student_root, name)),
                                "reason": "unknown category"})

    scoring_root = os.path.join(root, "ScoringSession")
    if os.path.isdir(extended_path(scoring_root)):
        for name in os.listdir(extended_path(scoring_root)):
            path = os.path.join(scoring_root, name)
            if any(token in name.lower() for token in _RESET_SCORING_NAMES):
                candidates.append(_reset_report_item(path, "scoring_session"))
            else:
                refused.append({"category": "scoring_session", "path": os.path.abspath(path),
                                "reason": "unknown category"})

    for name in os.listdir(extended_path(root)) if os.path.isdir(extended_path(root)) else []:
        path = os.path.join(root, name)
        if _RESET_DATED_EXPORT.match(name):
            candidates.append(_reset_report_item(path, "dated_export"))
        elif _RESET_HASH_ROOT.match(name) and os.path.isdir(extended_path(path)):
            names = []
            for dirpath, _, filenames in os.walk(extended_path(path)):
                names.extend([dirpath.lower(), *[f.lower() for f in filenames]])
            if any(marker in " ".join(names) for marker in _RESET_HASH_MARKERS):
                candidates.append(_reset_report_item(path, "compact_hash_output"))
            else:
                refused.append({"category": "compact_hash_root", "path": os.path.abspath(path),
                                "reason": "contents do not identify SAFE/private/evidence output"})
    return candidates, refused


def reset_workspace(root=None, *, apply: bool = False) -> dict:
    """Plan or apply the authorized, local clean-slate output reset.

    Ordinary workspace initialization and mirror refresh never call this.  An
    apply is refused in its entirety when an item under a resettable category
    cannot be classified; settings, locks, credentials, and Canvas Mirror are
    outside the candidate set by construction.
    """
    base = _root_or_workspace(root)
    if not base:
        return {"mode": "apply" if apply else "dry_run", "status": "unavailable",
                "counts": {"items": 0, "files": 0, "directories": 0},
                "paths": [], "refused": []}
    base = os.path.abspath(base)
    candidates, refused = _reset_candidates(base)
    counts = {
        "items": len(candidates),
        "files": sum(item["files"] for item in candidates),
        "directories": sum(item["directories"] for item in candidates),
    }
    report = {"mode": "apply" if apply else "dry_run",
              "status": "refused" if refused else "planned",
              "counts": counts, "paths": [item["path"] for item in candidates],
              "items": candidates, "refused": refused}
    if not apply or refused:
        return report

    for item in candidates:
        path = item["path"]
        if os.path.isdir(extended_path(path)) and not os.path.islink(path):
            shutil.rmtree(extended_path(path))
        elif os.path.exists(extended_path(path)):
            os.unlink(extended_path(path))
    report["status"] = "applied"
    return report


def reconcile_workspace(root=None) -> dict:
    """Return the explicit reset reconciliation without changing files."""
    return reset_workspace(root, apply=False)
