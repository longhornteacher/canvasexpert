"""PowerGrader session storage under ``_System/PowerGrader/Sessions``.

This module is also the single lifecycle owner for assignment-scoped Scoring
Sessions: it resolves the deterministic current session for one exact
``(course_id, assignment_id)`` scope and performs activation/supersession.

Lock order is scope, then session. Any path that needs both acquires the
deterministic scope lock before ``session_lock(session_id)``. A scope lock is
never acquired while already holding a session lock, so the two orders cannot
deadlock. The scope lock filename is a hash of the exact scope, never the raw
Canvas ids.
"""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from api.storage_support import atomic_write_json, interprocess_lock

from api.platform_services import workspace

# Per-session lock registry for interactive auto-post serialization
_session_locks: dict[str, threading.RLock] = {}
_session_lock_guard = threading.Lock()

# Per-scope lock registry for Scoring Session activation/submission ordering
_scope_locks: dict[str, threading.RLock] = {}
_scope_lock_guard = threading.Lock()

SCORING_ASSIGNMENT_KIND = "scoring_assignment"
# A record is actionable when its stage still owes teacher work: a prepared
# packet, or the submit-stage teacher questions. The basis-stage
# ``needs_scoring_norms`` state never saves a session at all. Terminal
# ``completed``/``completed_with_holds`` records are history, not resumable.
ACTIONABLE_STATUSES = frozenset({"ready", "needs_teacher_input"})
SUPERSEDED_STATUS = "superseded"


def _stable_digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def submission_snapshot_digest(submissions) -> str:
    """Digest only the private submission identity/version fields used by a session."""
    rows = []
    for row in submissions or []:
        if not isinstance(row, dict):
            continue
        rows.append({key: row.get(key) for key in (
            "user_id", "id", "attempt", "submitted_at", "workflow_state", "score",
        )})
    rows.sort(key=lambda row: (str(row.get("user_id") or ""), str(row.get("id") or "")))
    return _stable_digest(rows)


def packet_health(session: dict) -> dict:
    """Return a non-sensitive packet health record for lifecycle gates."""
    raw = (session.get("privacy_artifacts") or {}).get("safe_bundle") or ""
    path = workspace.extended_path(raw) if raw else ""
    if not path or not os.path.isfile(path):
        return {"ok": False, "code": "packet_missing"}
    try:
        with open(path, encoding="utf-8") as handle:
            bundle = json.load(handle)
        from api.powergrader.scoring_packet import validate_safe_bundle
        verdict = validate_safe_bundle(bundle)
        return verdict if verdict.get("ok") else {"ok": False, "code": "packet_invalid",
                                                    "reason": verdict.get("reason")}
    except Exception:
        return {"ok": False, "code": "packet_invalid", "reason": "bundle_unreadable"}


def session_staleness(session: dict, *, mirror_revision=None,
                      submission_snapshot=None) -> dict:
    """Compare a session's frozen inputs with a newly usable local snapshot."""
    expected_revision = session.get("mirror_revision")
    if mirror_revision is not None and expected_revision not in (None, ""):
        if str(mirror_revision) != str(expected_revision):
            return {"stale": True, "code": "session_stale", "reason": "mirror_revision_changed"}
    expected_snapshot = session.get("submission_snapshot")
    if submission_snapshot is not None and expected_snapshot not in (None, ""):
        actual = (submission_snapshot if isinstance(submission_snapshot, str)
                  else submission_snapshot_digest(submission_snapshot))
        if str(actual) != str(expected_snapshot):
            return {"stale": True, "code": "submission_identity_mismatch",
                    "reason": "submission_snapshot_changed"}
    return {"stale": False}


def mark_session_stale(session: dict, *, code="session_stale", replacement_session_id="") -> dict:
    """Record stale state without deleting the private packet/history."""
    session["status"] = SUPERSEDED_STATUS
    session["stale_code"] = str(code)
    session["stale_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if replacement_session_id:
        session["superseded_by_session_id"] = str(replacement_session_id)
    return session


def result_idempotency_key(session: dict, pseudonym: str, item_id: str,
                           caller_key: str = "") -> str:
    return _stable_digest({"session": session.get("session_id"),
                           "pseudonym": str(pseudonym), "item_id": str(item_id),
                           "caller_key": str(caller_key or "")})


def remember_result(session: dict, pseudonym: str, item_id: str, result: dict,
                    *, caller_key: str = "") -> str:
    key = result_idempotency_key(session, pseudonym, item_id, caller_key)
    session.setdefault("result_idempotency", {})[key] = dict(result)
    return key


def remembered_result(session: dict, pseudonym: str, item_id: str,
                      *, caller_key: str = "") -> dict | None:
    value = session.get("result_idempotency", {}).get(
        result_idempotency_key(session, pseudonym, item_id, caller_key))
    return dict(value) if isinstance(value, dict) else None


def _preparation_state_path(course_id, assignment_id) -> str | None:
    d = pg_dir()
    if not d:
        return None
    return os.path.join(d, f"prep-{scope_digest(course_id, assignment_id)}.json")


def save_preparation_state(course_id, assignment_id, *, scoring_guidance: str) -> None:
    """Keep teacher guidance private while a refresh is retrying."""
    path = _preparation_state_path(course_id, assignment_id)
    if path and str(scoring_guidance or "").strip():
        atomic_write_json(Path(path), {"scoring_guidance": str(scoring_guidance).strip()})


def load_preparation_state(course_id, assignment_id) -> dict:
    path = _preparation_state_path(course_id, assignment_id)
    value = _read_json(path) if path else None
    return value if isinstance(value, dict) else {}


def clear_preparation_state(course_id, assignment_id) -> None:
    path = _preparation_state_path(course_id, assignment_id)
    if path:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


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


def scope_digest(course_id, assignment_id) -> str:
    """Hash one exact course/assignment scope into a filesystem-safe key."""
    payload = f"{str(course_id or '')}\x00{str(assignment_id or '')}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


class _ScopeLock:
    """Context-manager view over one stable per-scope RLock.

    The scope lock serializes activation against final submission for the same
    exact course/assignment. Like the session lock it pairs a process-local
    RLock with a re-entrant interprocess lock, so nesting the same scope inside
    an already-held scope is safe.
    """

    def __init__(self, course_id, assignment_id):
        self._key = scope_digest(course_id, assignment_id)
        with _scope_lock_guard:
            if self._key not in _scope_locks:
                _scope_locks[self._key] = threading.RLock()
            self._local_lock = _scope_locks[self._key]
        self._interprocess = None

    def _is_owned(self):
        return self._local_lock._is_owned()

    def __enter__(self):
        self._local_lock.acquire()
        d = pg_dir()
        if d:
            self._interprocess = interprocess_lock(Path(d) / f"scope-{self._key}.lock")
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


def scope_lock(course_id, assignment_id):
    """Serialize one exact course/assignment scope across threads and processes."""
    return _ScopeLock(course_id, assignment_id)


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
    sessions = []
    seen_ids = set()
    for path in _session_paths():
        s = _read_json(path)
        if not s:
            continue
        sid = str(s.get("session_id") or "")
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        sessions.append(_summary(s))
    sessions.sort(key=lambda x: x.get("created") or "", reverse=True)
    return sessions


def _session_paths() -> list[str]:
    d = pg_dir()
    if not d or not os.path.isdir(d):
        return []
    return [os.path.join(d, name) for name in sorted(os.listdir(d))
            if name.endswith("_session.json")]


def _same_scope(session: dict, course_id, assignment_id) -> bool:
    return (
        str(session.get("course_id") or "") == str(course_id or "")
        and str(session.get("assignment_id") or "") == str(assignment_id or "")
    )


def _scope_summaries(course_id, assignment_id) -> list[dict]:
    """Every assignment-scoped summary on disk for one exact scope."""
    return [
        summary for summary in list_session_summaries()
        if summary.get("session_kind") == SCORING_ASSIGNMENT_KIND
        and _same_scope(summary, course_id, assignment_id)
    ]


def _newest_summary(summaries: list[dict]) -> dict | None:
    if not summaries:
        return None
    return max(summaries, key=lambda s: (str(s.get("created") or ""),
                                         str(s.get("session_id") or "")))


def _scope_generation(value) -> int | None:
    """Return a valid private lifecycle generation, if one is stored."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _current_summary(summaries: list[dict]) -> dict | None:
    """The deterministic current record for one exact scope.

    Activated records carry a private positive scope generation. Those records
    always outrank records without a generation, and the greatest generation
    wins (with ``(created, session_id)`` as deterministic tie-breakers). For
    pre-lifecycle records with no generation, the newest by ``(created,
    session_id)`` wins regardless of status. An already-superseded record is
    never current.
    """
    candidates = [s for s in summaries
                  if str(s.get("status") or "") != SUPERSEDED_STATUS]
    generated = [s for s in candidates
                 if _scope_generation(s.get("scope_generation")) is not None]
    if generated:
        return max(
            generated,
            key=lambda s: (
                _scope_generation(s.get("scope_generation")),
                str(s.get("created") or ""),
                str(s.get("session_id") or ""),
            ),
        )
    return _newest_summary(candidates)


def activate_scoring_session(session: dict, *, save_session=None) -> list[dict]:
    """Save one prepared scoring session, then make it the one current record.

    Called under the scope lock by ``prepare_scoring_session``. The new record
    receives the next private scope generation while the lock is held, is
    persisted first, and then every other actionable record for the exact scope
    is superseded. Keeping the exact newly activated id makes two successful
    preparations in the same second deterministic. Returns the superseded ids
    so the caller can report a count without leaking a private identifier.
    ``save_session`` remains the ordinary exact-record persistence primitive;
    this is the one lifecycle-aware save.
    """
    save_session = save_session or globals()["save_session"]
    course_id = session.get("course_id")
    assignment_id = session.get("assignment_id")
    with scope_lock(course_id, assignment_id):
        scoped = _scope_summaries(course_id, assignment_id)
        generations = [
            generation for generation in
            (_scope_generation(summary.get("scope_generation"))
             for summary in scoped)
            if generation is not None
        ]
        session["scope_generation"] = (max(generations, default=0) + 1)
        save_session(session)
        superseded = supersede_scoped_sessions(
            course_id, assignment_id, keep_session_id=session.get("session_id"),
            save_session=save_session)
    return superseded


def supersede_scoped_sessions(course_id, assignment_id, *,
                              keep_session_id=None, save_session=None) -> list[dict]:
    """Mark every non-current actionable record for one exact scope superseded.

    Activation passes its exact newly prepared id as the keeper. For a direct
    call without one, the deterministic current record is retained. Terminal
    and already-superseded records are left untouched: they are teacher
    history, not resumable work. A record whose stored status predates this
    lifecycle is still superseded here, which is how existing on-disk
    duplicates are made read-compatible without a migration. Returns the
    superseded session ids.
    """
    save_session = save_session or globals()["save_session"]
    scoped = _scope_summaries(course_id, assignment_id)
    actionable = [summary for summary in scoped
                  if str(summary.get("status") or "") in ACTIONABLE_STATUSES]
    keep = str(keep_session_id or "")
    if not keep:
        current = _current_summary(scoped)
        keep = str((current or {}).get("session_id") or "")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    superseded = []
    for summary in actionable:
        session_id = str(summary.get("session_id") or "")
        if not session_id or session_id == keep:
            continue
        record = load_session(session_id)
        if not record:
            continue
        record["status"] = SUPERSEDED_STATUS
        record["superseded_by_session_id"] = keep
        record["superseded_at"] = stamp
        save_session(record)
        superseded.append(session_id)
    return superseded


def current_session_id(session_id: str) -> str | None:
    """Return the deterministic current session id of the record's exact scope.

    Returns ``None`` when the id names no assignment-scoped record. Existing
    duplicates resolve to the newest record by ``(created, session_id)``; any
    other record in that scope is not current. A record explicitly marked
    superseded is never current. Terminal ``completed`` records can still be
    the current record of their scope: they are history, not resumable work,
    and reading their packet is unchanged behavior. This is a read-only
    resolution used at the packet/submit authorization boundary, so it never
    rewrites the teacher workspace.
    """
    session_id = str(session_id or "")
    if not session_id:
        return None
    record = load_session(session_id)
    if not record or record.get("session_kind") != SCORING_ASSIGNMENT_KIND:
        return None
    if str(record.get("status") or "") == SUPERSEDED_STATUS:
        return None
    current = _current_summary(_scope_summaries(record.get("course_id"),
                                                record.get("assignment_id")))
    if not current:
        return None
    return str(current.get("session_id") or "") or None


def is_current_session(session_id: str) -> bool:
    """True only for the deterministic current record of its exact scope."""
    return current_session_id(session_id) == str(session_id or "")


def current_actionable_sessions(*, course_ids=None) -> list[dict]:
    """One current, actionable summary row per exact course/assignment scope.

    Identity-free resume aid: no superseded, terminal, or duplicate record is
    returned, and every summary carries only the fields ``list_session_summaries``
    already exposes.
    """
    allowed = None if course_ids is None else {str(c or "") for c in course_ids}
    scopes: dict[tuple[str, str], list[dict]] = {}
    for summary in list_session_summaries():
        if summary.get("session_kind") != SCORING_ASSIGNMENT_KIND:
            continue
        course_id = str(summary.get("course_id") or "")
        if allowed is not None and course_id not in allowed:
            continue
        scopes.setdefault((course_id, str(summary.get("assignment_id") or "")),
                          []).append(summary)
    summaries = [
        {key: value for key, value in current.items()
         if key != "scope_generation"}
        for current in (_current_summary(records) for records in scopes.values())
        if current and str(current.get("status") or "") in ACTIONABLE_STATUSES
    ]
    summaries.sort(key=lambda row: str(row.get("created") or ""), reverse=True)
    return summaries


def _summary(session: dict) -> dict:
    students = session.get("students", [])
    return {
        "session_id":      session.get("session_id"),
        "session_kind":    session.get("session_kind", ""),
        "assignment_name": session.get("assignment_name"),
        "course_id":       session.get("course_id"),
        "assignment_id":   session.get("assignment_id"),
        "created":         session.get("created"),
        # Private lifecycle metadata used only by current-session resolution;
        # MCP list rows select their public columns explicitly.
        "scope_generation": session.get("scope_generation"),
        "status":          session.get("status", ""),
        "mode":            session.get("mode"),
        "mode_label":      mode_label(session.get("mode", "fast")),
        "total":           len(students),
        "approved":        sum(1 for st in students if st.get("status") == "approved"),
        "posted":          sum(1 for st in students if st.get("posted")),
    }
