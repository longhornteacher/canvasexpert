"""Single-current Scoring Session lifecycle laws.

Everything here is fabricated and confined to ``tmp_path``: sessions are real
on-disk records written through the real ``session_store``, but the workspace
root is the temp directory, so no teacher workspace or live pilot state is
touched.
"""
from __future__ import annotations

import threading

import pytest

from api.powergrader import session_store


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Point the real session store at a temporary workspace."""
    monkeypatch.setattr(session_store.workspace, "workspace_root", lambda: str(tmp_path))
    return session_store


def _session(session_id, *, course="c1", assignment="a1", status="ready",
             created="2026-01-01T08:00:00", students=None):
    return {
        "session_id": session_id,
        "session_kind": "scoring_assignment",
        "course_id": course,
        "assignment_id": assignment,
        "assignment_name": "Essay",
        "created": created,
        "status": status,
        "mode": "packet",
        "students": students if students is not None else [
            {"user_id": "u1", "status": "pending", "posted": False},
        ],
    }


# --- Law: at most one actionable session per exact scope --------------------

def test_activating_a_new_preparation_supersedes_the_earlier_actionable_one(store):
    store.save_session(_session("old", created="2026-01-01T08:00:00"))

    superseded = store.activate_scoring_session(_session("new", created="2026-01-02T08:00:00"))

    assert superseded == ["old"]
    assert store.load_session("new")["status"] == "ready"
    older = store.load_session("old")
    assert older["status"] == "superseded"
    assert older["superseded_by_session_id"] == "new"
    assert older["superseded_at"]
    # Supersede, never delete: the older record and its bundle stay on disk.
    assert store.session_path("old") is not None
    assert store.is_current_session("new") is True
    assert store.is_current_session("old") is False


def test_terminal_and_already_superseded_records_survive_a_new_activation(store):
    store.save_session(_session("done", status="completed"))
    store.save_session(_session("held", status="completed_with_holds"))
    store.save_session(_session("stale", status="superseded"))

    superseded = store.activate_scoring_session(_session("new"))

    assert superseded == []
    assert store.load_session("done")["status"] == "completed"
    assert store.load_session("held")["status"] == "completed_with_holds"
    assert store.load_session("stale")["status"] == "superseded"


def test_submit_stage_teacher_input_is_actionable_and_gets_superseded(store):
    store.save_session(_session("asking", status="needs_teacher_input"))

    superseded = store.activate_scoring_session(_session("new"))

    assert superseded == ["asking"]
    assert store.load_session("asking")["status"] == "superseded"


def test_different_assignment_or_course_never_supersedes_another_scope(store):
    store.save_session(_session("other-assignment", assignment="a2"))
    store.save_session(_session("other-course", course="c2"))

    superseded = store.activate_scoring_session(_session("new"))

    assert superseded == []
    assert store.load_session("other-assignment")["status"] == "ready"
    assert store.load_session("other-course")["status"] == "ready"


def test_concurrent_activations_for_one_scope_leave_exactly_one_current_session(store):
    store.save_session(_session("seed", created="2026-01-01T08:00:00"))
    barrier = threading.Barrier(2)
    errors = []

    def activate(session_id, created):
        try:
            barrier.wait(timeout=5)
            store.activate_scoring_session(_session(session_id, created=created))
        except Exception as exc:  # pragma: no cover - surfaced via assertion
            errors.append(exc)

    threads = [
        threading.Thread(target=activate, args=("first", "2026-01-02T08:00:00")),
        threading.Thread(target=activate, args=("second", "2026-01-03T08:00:00")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert store.is_current_session("seed") is False
    # The activation with the greatest generation is the sole current record.
    currents = [sid for sid in ("seed", "first", "second")
                if store.is_current_session(sid)]
    assert len(currents) == 1
    generations = {
        sid: store.load_session(sid).get("scope_generation")
        for sid in ("seed", "first", "second")
    }
    assert generations[currents[0]] == max(value for value in generations.values()
                                           if value is not None)
    rows = store.current_actionable_sessions()
    assert len(rows) == 1
    assert rows[0]["session_id"] == currents[0]


# --- Law: existing duplicates resolve deterministically ---------------------

def test_existing_duplicates_resolve_to_newest_by_created_then_session_id(store):
    # Written as if by a version that predates this lifecycle: no supersession
    # metadata, two actionable records for one exact scope.
    store.save_session(_session("dup-a", created="2026-01-01T08:00:00"))
    store.save_session(_session("dup-b", created="2026-01-05T08:00:00"))

    assert store.current_session_id("dup-a") == "dup-b"
    assert store.current_session_id("dup-b") == "dup-b"
    assert store.is_current_session("dup-a") is False


def test_equal_timestamps_break_the_tie_on_session_id(store):
    store.save_session(_session("aaa", created="2026-01-01T08:00:00"))
    store.save_session(_session("bbb", created="2026-01-01T08:00:00"))

    assert store.current_session_id("aaa") == "bbb"


def test_activation_generation_keeps_the_new_record_with_same_timestamp(store):
    store.activate_scoring_session(_session("zzz", created="2026-01-01T08:00:00"))

    store.activate_scoring_session(_session("aaa", created="2026-01-01T08:00:00"))

    assert store.load_session("aaa")["scope_generation"] == 2
    assert store.load_session("zzz")["status"] == "superseded"
    assert store.current_session_id("aaa") == "aaa"
    assert store.current_session_id("zzz") is None


def test_newer_terminal_pre_lifecycle_duplicate_suppresses_older_ready(store):
    store.save_session(_session("old-ready", created="2026-01-01T08:00:00"))
    store.save_session(_session("new-done", status="completed",
                               created="2026-01-02T08:00:00"))

    assert store.current_session_id("old-ready") == "new-done"
    assert store.current_actionable_sessions() == []


def test_generated_record_outranks_a_newer_legacy_record(store):
    store.save_session({**_session("activated", created="2026-01-01T08:00:00"),
                         "scope_generation": 1})
    store.save_session(_session("legacy-done", status="completed",
                               created="2026-01-02T08:00:00"))

    assert store.current_session_id("activated") == "activated"
    assert store.current_session_id("legacy-done") == "activated"


def test_activation_after_terminal_duplicate_gets_next_generation(store):
    store.save_session(_session("old-ready", created="2026-01-01T08:00:00"))
    store.save_session(_session("new-done", status="completed",
                               created="2026-01-02T08:00:00"))

    store.activate_scoring_session(_session("fresh", created="2026-01-03T08:00:00"))

    assert store.load_session("fresh")["scope_generation"] == 1
    assert store.current_session_id("fresh") == "fresh"
    assert store.load_session("old-ready")["status"] == "superseded"
    assert store.load_session("new-done")["status"] == "completed"


def test_unknown_and_non_scoring_records_are_never_current(store):
    store.save_session({**_session("root"), "session_kind": "scoring_session"})

    assert store.current_session_id("root") is None
    assert store.current_session_id("missing") is None
    assert store.current_session_id("") is None


# --- Law/contract: the resume list is identity-free and current-only --------

def test_current_actionable_sessions_returns_one_row_per_scope(store):
    store.save_session(_session("old", created="2026-01-01T08:00:00"))
    store.save_session(_session("new", created="2026-01-02T08:00:00"))
    store.save_session(_session("done", assignment="a2", status="completed"))
    store.save_session(_session("elsewhere", course="c9"))

    rows = store.current_actionable_sessions(course_ids=["c1"])

    assert [row["session_id"] for row in rows] == ["new"]
    assert set(rows[0]) == {
        "session_id", "session_kind", "assignment_name", "course_id",
        "assignment_id", "created", "status", "mode", "mode_label",
        "total", "approved", "posted",
    }


def test_current_actionable_sessions_scopes_to_the_supplied_courses(store):
    store.save_session(_session("c1-session", course="c1"))
    store.save_session(_session("c2-session", course="c2"))

    rows = store.current_actionable_sessions(course_ids=["c2"])

    assert [row["session_id"] for row in rows] == ["c2-session"]


# --- Law: lock order is scope, then session, and cannot deadlock ------------

def test_scope_lock_key_is_a_hash_and_never_the_raw_canvas_ids(store):
    key = store.scope_digest("course-123", "assignment-456")

    assert "course-123" not in key and "assignment-456" not in key
    assert store.scope_digest("course-123", "assignment-456") == key
    assert store.scope_digest("course-123", "assignment-457") != key


def test_scope_lock_is_reentrant_so_activation_can_nest_session_work(store):
    with store.scope_lock("c1", "a1") as outer:
        assert outer._is_owned() is True
        with store.scope_lock("c1", "a1"):
            with store.session_lock("s1"):
                store.save_session(_session("s1"))
        assert outer._is_owned() is True
    assert store.load_session("s1")["status"] == "ready"


def test_scope_then_session_order_is_held_by_both_entry_points(store, monkeypatch):
    """Activation and submission acquire the scope lock before any session lock."""
    order = []
    real_scope_lock = store.scope_lock
    real_session_lock = store.session_lock

    def tracked_scope_lock(course_id, assignment_id):
        order.append("scope")
        return real_scope_lock(course_id, assignment_id)

    def tracked_session_lock(session_id):
        order.append("session")
        return real_session_lock(session_id)

    monkeypatch.setattr(store, "scope_lock", tracked_scope_lock)
    monkeypatch.setattr(store, "session_lock", tracked_session_lock)

    store.activate_scoring_session(_session("new"))

    assert order[0] == "scope"
    assert "session" in order[1:]


def test_activation_waits_while_a_submission_holds_the_scope(store):
    """If submission holds the scope first, activation cannot interleave."""
    entered = threading.Event()
    release = threading.Event()
    activated = threading.Event()

    def hold_scope():
        with store.scope_lock("c1", "a1"):
            entered.set()
            release.wait(timeout=5)

    holder = threading.Thread(target=hold_scope)
    holder.start()
    assert entered.wait(timeout=5)

    def activate():
        store.activate_scoring_session(_session("new"))
        activated.set()

    worker = threading.Thread(target=activate)
    worker.start()
    # The activation is blocked on the scope lock the holder owns.
    assert activated.wait(timeout=0.2) is False
    release.set()
    holder.join(timeout=5)
    worker.join(timeout=5)

    assert activated.is_set() is True
    assert store.is_current_session("new") is True


# --- Example: preparing the same assignment twice ---------------------------

def test_preparing_the_same_assignment_twice_leaves_the_second_resumable(store):
    """The one happy path for this lifecycle, end to end at the store boundary."""
    store.activate_scoring_session(_session("first", created="2026-01-01T08:00:00"))
    store.activate_scoring_session(_session("second", created="2026-01-02T08:00:00"))

    rows = store.current_actionable_sessions()
    assert [row["session_id"] for row in rows] == ["second"]
    assert store.load_session("first")["status"] == "superseded"
    assert store.load_session("first")["superseded_by_session_id"] == "second"
    assert store.load_session("second")["status"] == "ready"
    # Both records remain on disk: supersede, never delete.
    assert store.load_session("first") is not None
    assert store.load_session("second") is not None


def test_current_actionable_session_returns_only_the_current_record(store):
    store.activate_scoring_session(_session("first", created="2026-01-01T08:00:00"))
    store.activate_scoring_session(_session("second", created="2026-01-02T08:00:00"))

    current = store.current_actionable_session("c1", "a1")

    assert current["session_id"] == "second"
    assert current["status"] == "ready"
