"""PowerGrader session storage under ``_System/PowerGrader/Sessions``."""

import json
import os
import threading
from pathlib import Path

from api.storage_support import atomic_write_json, interprocess_lock

from api.platform_services import workspace

# Per-session lock registry for interactive auto-post serialization
_session_locks: dict[str, threading.RLock] = {}
_session_lock_guard = threading.Lock()


def safe_session_id(session_id: str) -> str:
    return "".join(c for c in session_id if c.isalnum() or c == "-")


class _SessionLock:
    """Context-manager view over a stable per-session RLock.

    The object deliberately delegates ``_is_owned`` so existing diagnostic and
    test seams can inspect the process-local lock without acquiring it. The
    interprocess lock is still held for the complete context-manager lifetime.
    """

    def __init__(self, session_id: str):
        key = safe_session_id(session_id) or "_"
        with _session_lock_guard:
            if key not in _session_locks:
                _session_locks[key] = threading.RLock()
            self._local_lock = _session_locks[key]
        self._session_id = session_id
        self._interprocess = None

    def _is_owned(self):
        return self._local_lock._is_owned()

    def __enter__(self):
        self._local_lock.acquire()
        path = session_path(self._session_id)
        if path:
            self._interprocess = interprocess_lock(Path(path + ".lock"))
            self._interprocess.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self._interprocess is not None:
                self._interprocess.__exit__(exc_type, exc_value, traceback)
        finally:
            self._interprocess = None
            self._local_lock.release()
        return False


def session_lock(session_id: str):
    """Serialize one session within and across local processes."""
    return _SessionLock(session_id)


def pg_dir() -> str | None:
    d = workspace.powergrader_sessions_dir()
    if not d:
        return None
    os.makedirs(d, exist_ok=True)
    return d


def session_path(session_id: str) -> str | None:
    d = pg_dir()
    if not d:
        return None
    # Guard against path traversal
    safe_id = "".join(c for c in session_id if c.isalnum() or c == "-")
    return os.path.join(d, f"{safe_id}_session.json")


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def mode_label(mode: str) -> str:
    return {
        "fast": "Score myself",
        "packet": "Score with AI chat",
        "assisted": "Auto-score with AI",
    }.get(mode or "", mode or "Score myself")


def load_session(session_id: str) -> dict | None:
    with session_lock(session_id):
        return _load_session_unlocked(session_id)


def _load_session_unlocked(session_id: str) -> dict | None:
    path = session_path(session_id)
    if path and os.path.isfile(path):
        return _read_json(path)
    return None


def save_session(session: dict):
    session_id = session["session_id"]
    with session_lock(session_id):
        path = session_path(session_id)
        if not path:
            return
        atomic_write_json(Path(path), session)


def list_session_summaries() -> list[dict]:
    """Build a summary list of all saved sessions, newest first."""
    d = pg_dir()
    if not d:
        return []
    sessions = []
    seen_ids = set()
    candidate_paths = []
    if os.path.isdir(d):
        candidate_paths.extend(os.path.join(d, fname) for fname in sorted(os.listdir(d))
                               if fname.endswith("_session.json"))
    for path in candidate_paths:
        s = _read_json(path)
        if not s:
            continue
        sid = str(s.get("session_id") or "")
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        students = s.get("students", [])
        sessions.append({
            "session_id":      s.get("session_id"),
            "session_kind":    s.get("session_kind", ""),
            "parent_scoring_session_id": s.get("parent_scoring_session_id", ""),
            "assignment_name": s.get("assignment_name"),
            "course_id":       s.get("course_id"),
            "assignment_id":   s.get("assignment_id"),
            "created":         s.get("created"),
            "status":          s.get("status", ""),
            "mode":            s.get("mode"),
            "mode_label":      mode_label(s.get("mode", "fast")),
            "total":           len(students),
            "approved":        sum(1 for st in students if st.get("status") == "approved"),
            "posted":          sum(1 for st in students if st.get("posted")),
        })
    sessions.sort(key=lambda x: x.get("created") or "", reverse=True)
    return sessions
