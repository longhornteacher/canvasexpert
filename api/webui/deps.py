"""Shared stable paths, call-time workspace facades, and templates.

Sits BELOW the routers in the import graph: server.py and every future
routes/*.py import from here, and this module imports nothing from them. Stable
application paths remain available here; workspace-derived paths are delegated
to ``runtime_paths`` at call time.
"""
import glob as _glob
import json as _json
import os
import time


def _sse(lines):
    """Encode an iterable of strings as Server-Sent Events."""
    for line in lines:
        yield f"data: {_json.dumps(line)}\n\n"

from fastapi.templating import Jinja2Templates

from api import __version__, runtime_paths
from api.platform_services import workspace

WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
API_DIR   = os.path.dirname(WEBUI_DIR)
REPO_ROOT = os.path.dirname(API_DIR)

# Calendar CSVs live in the OneDrive workspace (seeded from api/default_docs/Calendars/
# on first run via workspace.ensure_workspace). Resolved lazily so this module stays
# importable even before the workspace is set up.
def _calendars_dir():
    from api.platform_services import workspace as _ws
    return _ws.library_folder("Calendars")


def _calendar_label(filename: str) -> str:
    """'Summer_Session_Sample.csv' → 'Summer Session Sample' (district-agnostic)."""
    stem = os.path.splitext(filename)[0]
    return stem.replace("_", " ").replace("-", " ").strip()


def _calendars_dir(root=None):
    """Resolve Calendars folder in the workspace Library."""
    from api.platform_services import workspace as _ws
    if root is None:
        return _ws.library_folder("Calendars")
    return _ws.library_folder("Calendars", root)


def _file_key(filename: str) -> str:
    """'Bell Schedule - A Day.csv' -> 'bell_schedule_a_day' (schedule_id)."""
    import re as _re
    stem = os.path.splitext(filename)[0]
    return _re.sub(r'[^a-z0-9]+', '_', stem.lower()).strip('_')


def _bell_schedule_conflict_stems(stem: str) -> list[str]:
    """Return canonical-stem candidates named by sync-copy suffix shapes."""
    import re as _re

    patterns = (
        r"^(?P<canonical>.+) \(\d+\)$",
        r"^(?P<canonical>.+?) \([^)]*conflicted copy[^)]*\)$",
        r"^(?P<canonical>.+?)\s+conflicted copy.*$",
    )
    for pattern in patterns:
        match = _re.match(pattern, stem, flags=_re.IGNORECASE)
        if match:
            return [match.group("canonical").rstrip()]

    candidates = []
    for match in reversed(list(_re.finditer("-", stem))):
        suffix = stem[match.end():]
        if suffix and not any(character.isspace() for character in suffix):
            candidates.append(stem[:match.start()].rstrip())
    return candidates


def _bell_schedule_conflicts(cal_dir: str) -> list[tuple[str, str]]:
    """Return ``(copy_path, canonical_path)`` pairs for neighboring copies."""
    paths = [
        path for path in _glob.glob(os.path.join(cal_dir, "*.csv"))
        if os.path.basename(path).lower().startswith("bell schedule")
    ]
    by_stem = {
        os.path.splitext(os.path.basename(path))[0].casefold(): path
        for path in paths
    }
    conflicts = []
    for path in paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        canonical_path = None
        for canonical_stem in _bell_schedule_conflict_stems(stem):
            canonical_path = by_stem.get(canonical_stem.casefold())
            if canonical_path and os.path.abspath(canonical_path) != os.path.abspath(path):
                break
        if canonical_path and os.path.abspath(canonical_path) != os.path.abspath(path):
            conflicts.append((path, canonical_path))
    return sorted(conflicts, key=lambda pair: os.path.basename(pair[0]).casefold())


def _bell_schedule_conflict_problems(cal_dir: str) -> list[str]:
    return [
        (
            f"Bell Schedule conflict copy '{os.path.basename(copy_path)}' matches "
            f"'{os.path.basename(canonical_path)}'; choose which duplicate to delete."
        )
        for copy_path, canonical_path in _bell_schedule_conflicts(cal_dir)
    ]


def list_bell_schedule_files(root=None):
    """[{name, label, schedule_id, path}] for every 'Bell Schedule*'-prefixed
    CSV in the workspace Calendars folder, excluding recognized sync copies."""
    cal_dir = _calendars_dir() if root is None else _calendars_dir(root)
    if not cal_dir or not os.path.isdir(cal_dir):
        return []

    conflict_paths = {
        os.path.abspath(copy_path)
        for copy_path, _canonical_path in _bell_schedule_conflicts(cal_dir)
    }
    found = []
    for path in sorted(_glob.glob(os.path.join(cal_dir, "*.csv"))):
        name = os.path.basename(path)
        if not name.lower().startswith("bell schedule"):
            continue
        if os.path.abspath(path) in conflict_paths:
            continue
        found.append({
            "name": name,
            "label": _calendar_label(name),
            "schedule_id": _file_key(name),
            "path": os.path.abspath(path),
        })
    return found


def load_bell_schedules(root=None) -> tuple:
    """Load all bell schedules from workspace Calendars folder.

    Returns ({schedule_id: meetings_list}, problems_list):
    - First element is dict mapping schedule_id to ordered meeting lists from
      parse_bell_schedule
    - Second element is list of problem strings (prefixed with filename/schedule_id)
    """
    from . import day_schedule

    schedules = {}
    all_problems = []
    cal_dir = _calendars_dir() if root is None else _calendars_dir(root)
    if cal_dir and os.path.isdir(cal_dir):
        all_problems.extend(_bell_schedule_conflict_problems(cal_dir))

    files = list_bell_schedule_files() if root is None else list_bell_schedule_files(root)
    for file_info in files:
        path = file_info["path"]
        schedule_id = file_info["schedule_id"]

        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            all_problems.append(f"{schedule_id}: could not read file: {e}")
            continue

        meetings, problems = day_schedule.parse_bell_schedule(content)
        schedules[schedule_id] = meetings

        for problem in problems:
            all_problems.append(f"{schedule_id}: {problem}")

    return schedules, all_problems


def load_teacher_schedule() -> tuple:
    """Load teacher schedule from Calendars folder.

    Reads 'Teacher Schedule.json' (exact filename) from the Calendars folder.
    Returns (data_dict, problems_list).
    Missing file -> ({}, ["no teacher schedule found"]).
    """
    from . import day_schedule

    calendars = _calendars_dir()
    if not calendars or not os.path.isdir(calendars):
        return {}, ["no workspace Calendars folder found"]

    path = os.path.join(calendars, "Teacher Schedule.json")
    if not os.path.exists(path):
        return {}, ["no teacher schedule found"]

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return {}, [f"could not read teacher schedule: {e}"]

    return day_schedule.parse_teacher_schedule(content)


def resolve_schedule_for(date: str) -> dict:
    """Resolve blocks for a specific date.

    Gets the date's schedule_id from the canonical calendar service, loads
    Bell Schedules and the Teacher Schedule, then day_schedule.resolve_day().
    Merges problems from all stages.

    date: "YYYY-MM-DD" string
    Returns {"state": ..., "blocks": [...], "problems": [...]}. ``state`` is
    the canonical calendar's own resolution state for this date
    (unconfigured, invalid_calendar, outside_coverage, no_school,
    no_regular_classes, unknown_schedule, or ready) so a consumer can show
    the actual reason a day has no schedule instead of a generic message. A
    block that spans more than one consecutive meeting includes period_ids,
    segments, and seq; a non-contiguous block produces one entry per
    consecutive run.
    """
    from . import day_schedule, school_calendar

    bell_schedules, bell_problems = load_bell_schedules()
    teacher_schedule, teacher_problems = load_teacher_schedule()

    doc, calendar_problems = school_calendar.read()
    resolution = school_calendar.resolve_date(doc, date, bell_schedules)
    state = resolution["state"]
    schedule_id = resolution.get("schedule_id") if state == "ready" else None

    blocks, resolve_problems = day_schedule.resolve_day(
        schedule_id, bell_schedules, teacher_schedule
    )

    all_problems = calendar_problems + bell_problems + teacher_problems + resolve_problems
    return {"state": state, "blocks": blocks, "problems": all_problems}


def _workspace_folder(name: str):
    return runtime_paths.workspace_folder(name)


def _exports_dir():
    """Where printable (DOCX) versions land: the synced workspace Printables
    folder when OneDrive is present, else the repo-local Finished_Exports fallback."""
    return runtime_paths.printables_dir()


# Stable compatibility facade; workspace-derived paths remain call-time only.
TEMP_DIR = str(runtime_paths.temp_dir())

templates = Jinja2Templates(directory=os.path.join(WEBUI_DIR, "templates"))
# Cache-bust static assets on every server restart so UI updates land without
# a hard refresh.
templates.env.globals["asset_v"] = str(int(time.time()))
templates.env.globals["app_version"] = __version__


# --------------------------------------------------------------------------
# Path constant — custom routines directory (used by pages and routines API)
# --------------------------------------------------------------------------
_CUSTOM_DIR = os.path.join(WEBUI_DIR, "..", "custom_routines")


# --------------------------------------------------------------------------
# File-listing helpers (pure; resolve workspace folders at call time)
# --------------------------------------------------------------------------

def _list_txt_files(folders):
    """One entry per unique file across `folders`, labeled by its own file
    name. A file's folder is a repo-relative path when its folder happens to
    be inside the repo and the teacher's synced workspace otherwise, so a
    path-based label reads as noise (or worse, a raw local folder path) for
    the common case of an out-of-repo Library folder. Disambiguate with the
    parent folder name only when two files share a basename (feature-freeze
    hardening initiative, D1)."""
    found = []
    seen = set()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for path in sorted(_glob.glob(os.path.join(folder, "*.txt"))):
            abspath = os.path.abspath(path)
            if abspath in seen:
                continue
            seen.add(abspath)
            found.append({
                "path": abspath,
                "_name": os.path.basename(path),
                "_parent": os.path.basename(os.path.dirname(abspath)),
            })
    name_counts: dict[str, int] = {}
    for entry in found:
        name_counts[entry["_name"]] = name_counts.get(entry["_name"], 0) + 1
    for entry in found:
        name = entry.pop("_name")
        parent = entry.pop("_parent")
        entry["label"] = f"{name} ({parent})" if name_counts[name] > 1 else name
    return found


def list_quiz_files():
    return _list_txt_files(runtime_paths.content_folders("quiz"))


def list_assignment_files():
    return _list_txt_files(runtime_paths.content_folders("assignment"))


def list_page_files():
    return _list_txt_files(runtime_paths.content_folders("page"))


def _inbox_marker_size(marker_path: str):
    """Parse a `<name>.txt.done` marker's decimal byte-length payload.

    Returns None (skip) for a missing file, unreadable file, or any content
    that isn't a plain non-negative integer.
    """
    try:
        with open(marker_path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    return int(text)


def list_inbox_files(kind: str):
    """Assistant-staged drafts from the per-kind Inbox, marker-gated.

    A dropped `<name>.txt` is only listed once its sibling `<name>.txt.done`
    marker exists and the decimal byte count parsed from it equals the
    actual size of `<name>.txt` -- this guards against listing a draft that
    is still half-synced by OneDrive. The `.done` markers themselves are
    never returned.

    Same {label, path} shape as the other list_*_files helpers, plus
    "source": "inbox" so the push tabs can badge these distinctly from the
    teacher's own library files (Slice D).
    """
    folder = runtime_paths.inbox_folder(kind)
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    for path in sorted(_glob.glob(os.path.join(str(folder), "*.txt"))):
        expected = _inbox_marker_size(path + ".done")
        if expected is None:
            continue
        try:
            actual = os.path.getsize(path)
        except OSError:
            continue
        if expected != actual:
            continue
        abspath = os.path.abspath(path)
        # The Inbox lives in the teacher's synced workspace, not the repo, so
        # (like _list_txt_files) a repo-relative label would climb out through
        # "..\..\Documents\OneDrive - ..." and show the teacher a path instead
        # of a draft name. The panel heading and the push tab already say where
        # these came from, so the file name is the whole useful label.
        found.append({
            "label": os.path.basename(path),
            "path": abspath,
            "source": "inbox",
        })
    return found


def list_ai_ta_files():
    found = []
    ai_ta_dir = runtime_paths.ai_ta_dir()
    canonical_dir = os.path.join(REPO_ROOT, "api", "default_docs", "AI Authoring")
    if not os.path.isdir(ai_ta_dir):
        return found
    for path in sorted(_glob.glob(os.path.join(ai_ta_dir, "*.txt"))):
        if not os.path.isfile(os.path.join(canonical_dir, os.path.basename(path))):
            continue
        found.append({
            "label": os.path.basename(path),
            "path": os.path.abspath(path),
        })
    return found
