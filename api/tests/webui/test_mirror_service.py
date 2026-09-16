"""Offline tests for the mirror scheduling service and routes.

The heartbeat thread is never started — tests drive run_heartbeat_pass /
sync_now directly with a fake canvas_get_all and injected now= (house
pattern from test_powergrader_scheduled_autoscore).
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from api import course_catalog
from api.mirror import store
from api.platform_services import workspace
from api.webui import mirror_service
from api.webui.server import app

NOW = "2026-07-16T12:00:00Z"


class FakeCanvas:
    def __init__(self):
        self.calls = []

    def __call__(self, path, params=None, timeout=30):
        self.calls.append(path)
        if path == "/api/v1/courses/111":
            return {"workflow_state": "available", "concluded": False}, None
        if path.endswith("/enrollments"):
            return [{"enrollment_state": "active"}], None
        if path.endswith("/assignments"):
            return [{"id": 700010, "name": "Essay 1", "published": True}], None
        if path.endswith("/users"):
            return [{"id": 900001, "name": "Learner One"}], None
        if path.endswith("/sections"):
            return [], None
        if path.endswith("/students/submissions"):
            return [], None
        if path == "/api/quiz/v1/courses/111/quizzes":
            return [], None
        raise AssertionError(f"unexpected path {path}")

    def complete(self, path, params=None, timeout=30):
        """Explicit complete-collection receipt for assignment membership."""
        rows, error = self(path, params, timeout)
        return rows, error, error is None


# --- due_passes cadence -----------------------------------------------------

def test_due_passes_full_when_never_synced():
    assert mirror_service.due_passes(store.default_sync("111"), NOW) == ["full"]


def test_due_passes_delta_when_fresh():
    state = store.default_sync("111")
    state["passes"]["full"]["last_success_at"] = "2026-07-16T11:00:00Z"
    state["passes"]["roster"]["last_success_at"] = "2026-07-16T11:00:00Z"
    assert mirror_service.due_passes(state, NOW, serve_max_age_hours=6.0) == ["delta"]


def test_due_passes_nightly_full_and_daily_roster():
    state = store.default_sync("111")
    state["passes"]["full"]["last_success_at"] = "2026-07-15T11:00:00Z"  # >24h
    assert mirror_service.due_passes(state, NOW) == ["full"]
    state["passes"]["full"]["last_success_at"] = "2026-07-16T11:00:00Z"
    state["passes"]["roster"]["last_success_at"] = "2026-07-15T11:00:00Z"  # >24h
    assert mirror_service.due_passes(state, NOW, serve_max_age_hours=6.0) == ["delta", "roster"]


def test_due_passes_roster_refreshes_before_serve_window_expires():
    """The roster must refresh once it ages past the serve window (default 6h),
    not only at the old 24h cadence — otherwise it goes unservable for most of
    the day while deltas keep the gradebook fresh."""
    state = store.default_sync("111")
    state["passes"]["full"]["last_success_at"] = "2026-07-16T11:00:00Z"  # fresh
    # Roster 7h old: within the 24h floor but past the 6h serve window.
    state["passes"]["roster"]["last_success_at"] = "2026-07-16T05:00:00Z"
    assert mirror_service.due_passes(state, NOW, serve_max_age_hours=6.0) == ["delta", "roster"]
    # 1h old: still comfortably servable, no roster pass needed.
    state["passes"]["roster"]["last_success_at"] = "2026-07-16T11:00:00Z"
    assert mirror_service.due_passes(state, NOW, serve_max_age_hours=6.0) == ["delta"]


def test_due_passes_roster_cadence_capped_at_daily_floor():
    """A serve window larger than a day must not stop the roster refreshing at
    least daily (the ROSTER_MAX_AGE_HOURS floor via min())."""
    state = store.default_sync("111")
    state["passes"]["full"]["last_success_at"] = "2026-07-16T11:00:00Z"  # fresh
    state["passes"]["roster"]["last_success_at"] = "2026-07-15T11:00:00Z"  # >24h
    assert mirror_service.due_passes(state, NOW, serve_max_age_hours=72.0) == ["delta", "roster"]


# --- heartbeat pass -----------------------------------------------------------

def test_heartbeat_first_tick_backfills_active_courses(monkeypatch, tmp_path, _configure):
    _configure()
    canvas = FakeCanvas()
    summaries = mirror_service.run_heartbeat_pass(
        canvas_get=canvas, canvas_get_all=canvas, canvas_get_all_complete=canvas.complete, now=NOW)
    assert [(s["course_id"], s["pass"], s["ok"]) for s in summaries] == [("111", "full", True)]
    assert store.read_sync("111")["passes"]["full"]["state"] == "current"


def test_heartbeat_full_maintains_private_groups_with_its_timestamp(monkeypatch, tmp_path, _configure):
    _configure()
    calls = []
    categories = [{
        "category_id": "500001", "category_name": "Teams",
        "groups": [{"id": "600001", "name": "Blue",
                    "memberships": [{"id": "700001", "user_id": "900001"}]}],
    }]

    result = mirror_service.run_heartbeat_pass(
        canvas_get=FakeCanvas(), canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=FakeCanvas().complete,
        load_groups=lambda course_id: calls.append(course_id) or (categories, None, ""), now=NOW)

    assert calls == ["111"]
    assert result[0]["groups"] == {"state": "current", "error_code": ""}
    document = store.read_groups("111")
    assert document["state"] == "current"
    assert document["last_success_at"] == NOW
    assert document["categories"] == categories


def test_heartbeat_group_failure_preserves_last_good_snapshot_and_pass_success(monkeypatch, tmp_path, _configure):
    _configure()
    categories = [{"category_id": "500001", "category_name": "Teams", "groups": []}]
    store.write_groups("111", categories, attempted_at="2026-07-16T11:00:00Z")

    result = mirror_service.run_heartbeat_pass(
        canvas_get=FakeCanvas(), canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=FakeCanvas().complete,
        load_groups=lambda _course_id: ([], "Canvas returned 403 with private detail", ""), now=NOW)

    assert result[0]["ok"] is True
    assert result[0]["groups"] == {"state": "stale", "error_code": "refresh_failed"}
    document = store.read_groups("111")
    assert document["categories"] == categories
    assert document["last_success_at"] == "2026-07-16T11:00:00Z"
    assert document["last_attempt_at"] == NOW
    assert document["state"] == "stale"
    assert document["error_code"] == "refresh_failed"
    assert "private detail" not in str(result[0]["groups"])


def test_heartbeat_roster_maintenance_refreshes_groups(monkeypatch, tmp_path, _configure):
    _configure()
    store.record_pass("111", "full", ok=True, attempted_at=NOW)
    store.record_pass("111", "roster", ok=True, attempted_at="2026-07-15T11:00:00Z")
    calls = []

    result = mirror_service.run_heartbeat_pass(
        canvas_get=FakeCanvas(), canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=FakeCanvas().complete,
        load_groups=lambda course_id: calls.append(course_id) or ([], None, ""), now=NOW)

    assert [entry["pass"] for entry in result] == ["delta", "roster"]
    assert result[1]["groups"] == {"state": "current", "error_code": ""}
    assert calls == ["111"]


def test_heartbeat_group_snapshot_write_failure_keeps_core_pass_success(monkeypatch, tmp_path, _configure):
    _configure()
    categories = [{"category_id": "500001", "category_name": "Teams", "groups": []}]
    store.write_groups("111", categories, attempted_at="2026-07-16T11:00:00Z")
    monkeypatch.setattr(mirror_service.store, "write_groups",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk unavailable")))

    result = mirror_service.run_heartbeat_pass(
        canvas_get=FakeCanvas(), canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=FakeCanvas().complete,
        load_groups=lambda _course_id: (categories, None, ""), now=NOW)

    assert result[0]["ok"] is True
    assert result[0]["groups"] == {"state": "stale", "error_code": "refresh_failed"}
    document = store.read_groups("111")
    assert document["categories"] == categories
    assert document["last_success_at"] == "2026-07-16T11:00:00Z"
    assert document["state"] == "stale"


def test_heartbeat_skips_group_refresh_when_core_pass_fails_or_only_delta_is_due(monkeypatch, tmp_path, _configure):
    _configure()
    called = []
    monkeypatch.setitem(mirror_service._PASS_RUNNERS, "full", lambda *_a, **_k: {"ok": False})
    first = mirror_service.run_heartbeat_pass(
        canvas_get=FakeCanvas(), canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=FakeCanvas().complete,
        load_groups=lambda _course_id: called.append(1) or ([], None, ""), now=NOW)
    assert first[0]["ok"] is False
    assert called == []

    store.record_pass("111", "full", ok=True, attempted_at=NOW)
    store.record_pass("111", "roster", ok=True, attempted_at=NOW)
    second = mirror_service.run_heartbeat_pass(
        canvas_get=FakeCanvas(), canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=FakeCanvas().complete,
        load_groups=lambda _course_id: called.append(1) or ([], None, ""),
        now="2026-07-16T12:15:00Z")
    assert [entry["pass"] for entry in second] == ["delta"]
    assert called == []


def test_heartbeat_steady_state_runs_delta_only(monkeypatch, tmp_path, _configure):
    _configure()
    first = FakeCanvas()
    mirror_service.run_heartbeat_pass(
        canvas_get=first, canvas_get_all=first, canvas_get_all_complete=first.complete, now=NOW)
    second = FakeCanvas()
    summaries = mirror_service.run_heartbeat_pass(
        canvas_get=second, canvas_get_all=second, canvas_get_all_complete=second.complete,
        now="2026-07-16T12:15:00Z")
    assert [s["pass"] for s in summaries] == ["delta"]


def test_heartbeat_refreshes_context_first_and_then_daily_only(monkeypatch, tmp_path, _configure):
    _configure()
    first = FakeCanvas()
    mirror_service.run_heartbeat_pass(
        canvas_get=first, canvas_get_all=first, canvas_get_all_complete=first.complete, now=NOW)
    assert first.calls[:2] == ["/api/v1/courses/111", "/api/v1/courses/111/enrollments"]

    warm = FakeCanvas()
    mirror_service.run_heartbeat_pass(canvas_get=warm, canvas_get_all=warm,
                                      canvas_get_all_complete=warm.complete,
                                      now="2026-07-16T12:15:00Z")
    assert "/api/v1/courses/111" not in warm.calls
    assert "/api/v1/courses/111/enrollments" not in warm.calls

    daily = FakeCanvas()
    mirror_service.run_heartbeat_pass(canvas_get=daily, canvas_get_all=daily,
                                      canvas_get_all_complete=daily.complete,
                                      now="2026-07-17T12:01:00Z")
    assert daily.calls[:2] == ["/api/v1/courses/111", "/api/v1/courses/111/enrollments"]


def test_concluded_warm_heartbeat_suppresses_delta_roster_and_new_quiz(monkeypatch, tmp_path, _configure):
    _configure()
    store.record_course_context(
        "111", ok=True, attempted_at=NOW, lifecycle="concluded",
        course_workflow_state="completed", course_concluded=True,
        enrollment_states=["completed"])
    store.record_pass("111", "full", ok=True, attempted_at="2026-07-16T11:00:00Z")
    store.record_pass("111", "roster", ok=True, attempted_at="2026-07-16T11:00:00Z")

    def no_canvas_call(*args, **kwargs):
        raise AssertionError("concluded warm heartbeat must make no Canvas call")

    assert mirror_service.run_heartbeat_pass(
        canvas_get=no_canvas_call, canvas_get_all=no_canvas_call,
        canvas_get_all_complete=no_canvas_call, now=NOW) == []


def test_concluded_daily_full_keeps_core_reconcile_and_skips_new_quiz(monkeypatch, tmp_path, _configure):
    _configure()
    store.record_course_context(
        "111", ok=True, attempted_at=NOW, lifecycle="concluded",
        course_workflow_state="completed", course_concluded=True,
        enrollment_states=["completed"])
    store.record_pass("111", "full", ok=True, attempted_at="2026-07-15T11:00:00Z")
    calls = []

    def core_only(path, params=None, timeout=30):
        calls.append(path)
        if "/api/quiz/v1/" in path:
            raise AssertionError("concluded daily full must skip New Quiz metadata")
        if path.endswith("/assignments"):
            return [{"id": 700099, "name": "New Quiz", "published": True,
                     "is_quiz_lti_assignment": True}], None
        if path.endswith("/users"):
            return [{"id": 900001, "name": "Synthetic Learner"}], None
        if path.endswith("/sections") or path.endswith("/students/submissions"):
            return [], None
        raise AssertionError(path)

    def core_only_complete(path, params=None, timeout=30):
        rows, error = core_only(path, params, timeout)
        return rows, error, error is None

    result = mirror_service.run_heartbeat_pass(
        canvas_get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("context is fresh")),
        canvas_get_all=core_only, canvas_get_all_complete=core_only_complete, now=NOW)
    assert [(entry["pass"], entry["ok"]) for entry in result] == [("full", True)]
    assert result[0]["new_quizzes"]["state"] == "skipped_lifecycle"
    assert result[0]["new_quizzes"]["skipped_lifecycle"] is True
    assert all("/api/quiz/v1/" not in path for path in calls)


def test_unknown_context_keeps_current_heartbeat_cadence(monkeypatch, tmp_path, _configure):
    _configure()

    class UnknownLifecycleCanvas(FakeCanvas):
        def __call__(self, path, params=None, timeout=30):
            if path.endswith("/enrollments"):
                self.calls.append(path)
                return [], None
            return super().__call__(path, params, timeout)

    first = UnknownLifecycleCanvas()
    first_result = mirror_service.run_heartbeat_pass(
        canvas_get=first, canvas_get_all=first, canvas_get_all_complete=first.complete, now=NOW)
    assert [entry["pass"] for entry in first_result] == ["full"]
    assert store.read_course_context("111")["lifecycle"] == "unknown"
    warm = UnknownLifecycleCanvas()
    warm_result = mirror_service.run_heartbeat_pass(
        canvas_get=warm, canvas_get_all=warm, canvas_get_all_complete=warm.complete,
        now="2026-07-16T12:15:00Z")
    assert [entry["pass"] for entry in warm_result] == ["delta"]


def test_heartbeat_gates_on_token_flag_and_workspace(monkeypatch, tmp_path, _configure):
    _configure()
    monkeypatch.setattr(mirror_service.config, "token_is_set", lambda: False)
    first = FakeCanvas()
    assert mirror_service.run_heartbeat_pass(
        canvas_get=first, canvas_get_all=first, canvas_get_all_complete=first.complete, now=NOW) == []

    _configure()
    monkeypatch.setattr(mirror_service.config, "mirror_enabled", lambda: False)
    second = FakeCanvas()
    assert mirror_service.run_heartbeat_pass(
        canvas_get=second, canvas_get_all=second, canvas_get_all_complete=second.complete, now=NOW) == []

    _configure()
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    third = FakeCanvas()
    assert mirror_service.run_heartbeat_pass(
        canvas_get=third, canvas_get_all=third, canvas_get_all_complete=third.complete, now=NOW) == []


def test_heartbeat_survives_a_course_that_raises(monkeypatch, tmp_path, _configure):
    _configure(
               courses=({"id": "111"}, {"id": "222"}))

    def exploding(path, params=None, timeout=30):
        if "/222/" in path:
            raise RuntimeError("boom")
        return FakeCanvas()(path, params, timeout)

    def exploding_complete(path, params=None, timeout=30):
        rows, error = exploding(path, params, timeout)
        return rows, error, error is None

    summaries = mirror_service.run_heartbeat_pass(
        canvas_get=exploding, canvas_get_all=exploding,
        canvas_get_all_complete=exploding_complete, now=NOW)
    by_course = {s["course_id"]: s for s in summaries}
    assert by_course["111"]["ok"] is True
    assert by_course["222"]["ok"] is False


def test_heartbeat_and_manual_sync_thread_the_complete_assignment_seam(monkeypatch, tmp_path, _configure):
    _configure()
    canvas = FakeCanvas()
    receipt = lambda *args, **kwargs: ([], None, True)
    heartbeat_calls = []
    manual_calls = []
    monkeypatch.setitem(
        mirror_service._PASS_RUNNERS, "full",
        lambda cid, **kwargs: heartbeat_calls.append((cid, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        mirror_service.sync, "delta_pass",
        lambda cid, **kwargs: manual_calls.append((cid, kwargs)) or {"ok": True},
    )

    mirror_service.run_heartbeat_pass(
        canvas_get=canvas, canvas_get_all=canvas,
        canvas_get_all_complete=receipt, now=NOW)
    mirror_service.sync_now(
        "111", canvas_get=canvas, canvas_get_all=canvas,
        canvas_get_all_complete=receipt, now=NOW)

    assert heartbeat_calls[0][1]["canvas_get_all_complete"] is receipt
    assert manual_calls[0][1]["canvas_get_all_complete"] is receipt


# --- course_name threading to Course Catalog (1.0beta 02c) ------------------------

def test_heartbeat_never_threads_course_name_into_roster_pass_kwargs(monkeypatch, tmp_path, _configure):
    _configure(courses=({"id": "111", "name": "Course One"},))
    store.record_pass("111", "full", ok=True, attempted_at=NOW)
    store.record_pass("111", "roster", ok=True, attempted_at="2026-07-15T11:00:00Z")  # >24h -> due
    delta_calls, roster_calls = [], []
    monkeypatch.setitem(
        mirror_service._PASS_RUNNERS, "delta",
        lambda cid, **kwargs: delta_calls.append(kwargs) or {"ok": True},
    )
    monkeypatch.setitem(
        mirror_service._PASS_RUNNERS, "roster",
        lambda cid, **kwargs: roster_calls.append(kwargs) or {"ok": True},
    )

    mirror_service.run_heartbeat_pass(
        canvas_get=FakeCanvas(), canvas_get_all=FakeCanvas(),
        canvas_get_all_complete=FakeCanvas().complete, now=NOW)

    assert delta_calls and delta_calls[0]["course_name"] == "Course One"
    assert roster_calls and "course_name" not in roster_calls[0]


def test_heartbeat_forwards_course_name_to_catalog(monkeypatch, tmp_path, _configure):
    _configure(courses=({"id": "111", "name": "Course One"},))
    canvas = FakeCanvas()

    mirror_service.run_heartbeat_pass(
        canvas_get=canvas, canvas_get_all=canvas, canvas_get_all_complete=canvas.complete, now=NOW)

    catalog = course_catalog.read_catalog("111")["catalog"]
    assert catalog["course_name"] == "Course One"


def test_heartbeat_course_name_omission_falls_back_to_course_id(monkeypatch, tmp_path, _configure):
    _configure(courses=({"id": "111"},))  # no "name" key at all
    canvas = FakeCanvas()

    mirror_service.run_heartbeat_pass(
        canvas_get=canvas, canvas_get_all=canvas, canvas_get_all_complete=canvas.complete, now=NOW)

    catalog = course_catalog.read_catalog("111")["catalog"]
    assert catalog["course_name"] == "111"


def test_sync_now_forwards_course_name_to_catalog(monkeypatch, tmp_path, _configure):
    _configure(courses=({"id": "111", "name": "Course One"},))
    canvas = FakeCanvas()

    mirror_service.sync_now(
        "111", canvas_get=canvas, canvas_get_all=canvas,
        canvas_get_all_complete=canvas.complete, now=NOW)

    catalog = course_catalog.read_catalog("111")["catalog"]
    assert catalog["course_name"] == "Course One"


def test_scoring_refresh_runs_a_full_pass_without_comments(monkeypatch, _configure):
    _configure(courses=({"id": "111", "name": "Course One"},))
    captured = {}

    def full_pass(course_id, **kwargs):
        captured["course_id"] = course_id
        captured.update(kwargs)
        return {"ok": True, "assignments": 2}

    monkeypatch.setattr(mirror_service.sync, "full_pass", full_pass)

    result = mirror_service._run_scoring_course_refresh("111")

    assert result == {"ok": True, "assignments": 2}
    assert captured["course_id"] == "111"
    assert captured["course_name"] == "Course One"
    assert captured["with_comments"] is False
    assert captured["bypass_new_quiz_cooldown"] is True


# --- sync_now -------------------------------------------------------------------

def test_sync_now_scopes_to_saved_courses(monkeypatch, tmp_path, _configure):
    _configure(courses=(
        {"id": "111", "name": "Current", "active": True},
        {"id": "222", "name": "Previous", "active": False},
    ))
    ignored = FakeCanvas()
    results = mirror_service.sync_now(
        "999", canvas_get=ignored, canvas_get_all=ignored,
        canvas_get_all_complete=ignored.complete, now=NOW)
    assert results == [{"ok": False, "error": "Not a saved course."}]
    canvas = FakeCanvas()
    results = mirror_service.sync_now(
        "111", canvas_get=canvas, canvas_get_all=canvas,
        canvas_get_all_complete=canvas.complete, now=NOW)
    assert results[0]["ok"] is True
    assert results[0]["course_id"] == "111"


def test_sync_now_records_a_failed_course_context_refresh(monkeypatch, tmp_path, _configure):
    """B2 example: a swallowed course_context.refresh_course_context failure
    is now recorded (event=mirror.course_refresh, outcome=failed) instead of
    vanishing with zero signal -- sync_now's own result is unaffected, since
    B2 changes recording only, never control flow."""
    from api import operational_log

    _configure()
    monkeypatch.setattr(
        mirror_service.course_context, "refresh_course_context",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("canvas unreachable")))
    canvas = FakeCanvas()
    results = mirror_service.sync_now(
        "111", canvas_get=canvas, canvas_get_all=canvas,
        canvas_get_all_complete=canvas.complete, now=NOW)
    assert results[0]["ok"] is True  # unaffected: context refresh is best-effort
    records = [r for r in operational_log.tail() if r["event"] == "mirror.course_refresh"]
    assert len(records) == 1
    assert records[0]["outcome"] == "failed"
    assert records[0]["error_class"] == "RuntimeError"


def test_sync_now_records_privacy_minimized_refresh_outcome(monkeypatch, _configure):
    from api import operational_log

    _configure()
    records = []
    monkeypatch.setattr(operational_log, "emit", lambda *args, **kwargs: records.append((args, kwargs)))
    canvas = FakeCanvas()

    results = mirror_service.sync_now(
        "111", canvas_get=canvas, canvas_get_all=canvas,
        canvas_get_all_complete=canvas.complete, now=NOW)

    assert results[0]["ok"] is True
    refresh = [item for item in records if item[0] == ("mirror.refresh", "ok")]
    assert len(refresh) == 1
    assert refresh[0][1]["scope"] == "delta"
    assert set(refresh[0][1]) == {"scope", "duration_ms"}


def test_enqueue_sync_accepts_previous_but_heartbeat_stays_current_only(monkeypatch, tmp_path, _configure):
    _configure(courses=(
        {"id": "111", "name": "Current", "active": True},
        {"id": "222", "name": "Previous", "active": False},
    ))
    submitted = []

    class FakeCoordinator:
        def submit(self, course_ids, scopes, *, priority):
            submitted.append((tuple(course_ids), tuple(scopes or ("course.refresh",)), priority))
            return "plan"

    monkeypatch.setattr(mirror_service, "coordinator_instance", lambda: FakeCoordinator())
    monkeypatch.setattr(
        mirror_service.store, "read_course_context",
        lambda course_id: {"lifecycle": "current", "state": "current"},
    )

    assert mirror_service.enqueue_sync("222") == "plan"
    assert submitted == [(('222',), ("course.refresh",), "manual")]
    submitted.clear()

    assert mirror_service.enqueue_heartbeat_refreshes() == ["plan"]
    assert submitted == [(('111',), ("course.refresh",), "background")]


def test_sync_now_bypasses_new_quiz_capability_cooldown_the_heartbeat_never_does(monkeypatch, tmp_path, _configure):
    _configure()
    quiz_calls = []

    class NewQuizCanvas:
        def __call__(self, path, params=None, timeout=30):
            if path == "/api/v1/courses/111":
                return {"workflow_state": "available", "concluded": False}, None
            if path.endswith("/enrollments"):
                return [{"enrollment_state": "active"}], None
            if path.endswith("/assignments"):
                return [{"id": 700099, "name": "New Quiz",
                        "is_quiz_lti_assignment": True, "updated_at": ""}], None
            if path.endswith("/users"):
                return [{"id": 900001, "name": "Learner One"}], None
            if path.endswith("/sections") or path.endswith("/students/submissions"):
                return [], None
            if path.endswith("/quizzes"):
                quiz_calls.append(path)
                return [{"id": "700099", "title": "New Quiz"}], None
            if path.endswith("/quizzes/700099/items"):
                quiz_calls.append(path)
                return [], None
            raise AssertionError(f"unexpected path {path}")

        complete = FakeCanvas.complete

    # Seed the course as restricted, with a far-future cooldown, before any
    # sync has ever run — so the first heartbeat sees an already-open circuit.
    store.write_new_quiz_capability(
        "111", capability="restricted", last_probe_at=NOW,
        retry_after="2099-01-01T00:00:00Z", evidence_category="forbidden",
        consecutive_failures=3)

    # The heartbeat (first-run backfill) must still skip the New Quiz
    # fan-out entirely — it never bypasses the cooldown.
    heartbeat = NewQuizCanvas()
    mirror_service.run_heartbeat_pass(
        canvas_get=heartbeat, canvas_get_all=heartbeat,
        canvas_get_all_complete=heartbeat.complete, now=NOW)
    assert quiz_calls == []
    assert store.read_new_quiz_capability("111")["capability"] == "restricted"

    # Manual sync now ignores the cooldown and probes the metadata collection.
    manual = NewQuizCanvas()
    results = mirror_service.sync_now("111", canvas_get=manual, canvas_get_all=manual,
                                      canvas_get_all_complete=manual.complete,
                                      now="2026-07-16T12:20:00Z")
    assert results[0]["ok"] is True
    assert quiz_calls == ["/api/quiz/v1/courses/111/quizzes",
                          "/api/quiz/v1/courses/111/quizzes/700099/items"]
    assert store.read_new_quiz_capability("111")["capability"] == "supported"


def test_manual_sync_probes_new_quiz_even_when_lifecycle_is_concluded(monkeypatch, tmp_path, _configure):
    _configure()
    quiz_calls = []

    class ConcludedCanvas:
        def __call__(self, path, params=None, timeout=30):
            if path == "/api/v1/courses/111":
                return {"workflow_state": "completed", "concluded": True}, None
            if path.endswith("/enrollments"):
                return [{"enrollment_state": "completed"}], None
            if path.endswith("/assignments"):
                return [{"id": 700099, "name": "New Quiz", "published": True,
                         "is_quiz_lti_assignment": True, "updated_at": ""}], None
            if path.endswith("/users"):
                return [{"id": 900001, "name": "Synthetic Learner"}], None
            if path.endswith("/sections") or path.endswith("/students/submissions"):
                return [], None
            if path.endswith("/quizzes"):
                quiz_calls.append(path)
                return [{"id": "700099", "title": "New Quiz"}], None
            if path.endswith("/quizzes/700099/items"):
                quiz_calls.append(path)
                return [], None
            raise AssertionError(path)

        complete = FakeCanvas.complete

    canvas = ConcludedCanvas()
    results = mirror_service.sync_now(
        "111", canvas_get=canvas, canvas_get_all=canvas,
        canvas_get_all_complete=canvas.complete, now=NOW)
    assert results[0]["ok"] is True
    assert store.read_course_context("111")["lifecycle"] == "concluded"
    assert quiz_calls == ["/api/quiz/v1/courses/111/quizzes",
                          "/api/quiz/v1/courses/111/quizzes/700099/items"]


# --- routes ------------------------------------------------------------------------

def test_mirror_status_route(monkeypatch, tmp_path, _configure):
    _configure()
    canvas = FakeCanvas()
    mirror_service.run_heartbeat_pass(
        canvas_get=canvas, canvas_get_all=canvas, canvas_get_all_complete=canvas.complete, now=NOW)
    response = TestClient(app).get("/api/mirror/status")
    payload = response.json()
    assert payload["ok"] is True and payload["enabled"] is True
    course = payload["courses"][0]
    assert course["course_id"] == "111"
    assert course["passes"]["full"]["state"] == "current"
    assert course["watermarks"]["submitted_since"] == "2026-07-16T11:50:00Z"
    assert course["context"]["lifecycle"] == "current"
    assert set(course["context"]) == {
        "schema_version", "course_id", "state", "last_success_at", "last_attempt_at",
        "error_code", "lifecycle", "course_workflow_state", "course_concluded",
        "course_end_at", "term_end_at", "enrollment_states",
    }


def test_mirror_sync_now_route(monkeypatch, tmp_path, _configure):
    _configure()
    canvas = FakeCanvas()
    monkeypatch.setattr(mirror_service, "canvas_get_all", canvas)
    monkeypatch.setattr(mirror_service, "canvas_get_all_complete", canvas.complete)
    monkeypatch.setattr(mirror_service, "canvas_get", canvas)
    response = TestClient(app).post("/api/mirror/sync-now", data={"course_id": "111"})
    payload = response.json()
    assert response.status_code == 202
    assert payload["ok"] is True and isinstance(payload["plan_id"], str)
    client = TestClient(app)
    for _ in range(100):
        status = client.get("/api/mirror/status", params={"plan_id": payload["plan_id"]}).json()
        if status["plan"]["plans"][0]["state"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    assert status["plan"]["plans"][0]["plan_id"] == payload["plan_id"]


def test_coordinated_heartbeat_uses_background_and_concluded_plans_before_findings(monkeypatch, tmp_path, _configure):
    _configure(courses=(
        {"id": "111", "name": "Current"}, {"id": "222", "name": "Concluded"},
    ))
    monkeypatch.setattr(mirror_service.store, "read_course_context", lambda course_id: {
        "lifecycle": "concluded" if course_id == "222" else "current",
        "state": "current",
    })
    calls = []

    class FakeCoordinator:
        def submit(self, course_ids, scopes, *, priority):
            calls.append(("submit", tuple(course_ids), tuple(scopes), priority))
            return priority

    monkeypatch.setattr(mirror_service, "coordinator_instance", lambda: FakeCoordinator())
    monkeypatch.setattr(mirror_service, "wait_for_plan", lambda plan_id: calls.append(("wait", plan_id)))
    monkeypatch.setattr(mirror_service, "refresh_work_findings", lambda: calls.append(("findings",)))
    mirror_service.run_coordinated_heartbeat_tick()
    assert calls == [
        ("submit", ("111",), ("course.refresh",), "background"),
        ("submit", ("222",), ("course.refresh",), "concluded"),
        ("wait", "background"), ("wait", "concluded"), ("findings",),
    ]


def test_named_scope_runners_do_not_call_legacy_full_or_delta(monkeypatch, tmp_path, _configure):
    _configure()
    assignment_map = {"700": {"id": "700", "is_quiz_lti_assignment": True}}
    monkeypatch.setattr(mirror_service.store, "read_assignments", lambda _course: {"assignments": assignment_map})
    seen = []
    monkeypatch.setattr(mirror_service.new_quizzes, "sync_metadata", lambda *args, **kwargs: seen.append(args[1]) or {"ok": True})
    monkeypatch.setattr(mirror_service.sync, "delta_pass", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    assert mirror_service._run_new_quiz_metadata("111")["ok"] is True
    assert seen == [[{"id": "700", "is_quiz_lti_assignment": True}]]


def test_context_and_group_runner_failures_are_explicit_and_fail_plans(monkeypatch, tmp_path, _configure):
    _configure()
    monkeypatch.setattr(mirror_service.course_context, "refresh_course_context",
                        lambda *args, **kwargs: {"state": "stale"})
    monkeypatch.setattr(mirror_service, "_refresh_groups_on_maintenance",
                        lambda *args, **kwargs: {"state": "unavailable"})
    assert mirror_service._run_course_context("111") == {"ok": False, "state": "stale"}
    assert mirror_service._run_groups("111") == {"ok": False, "state": "unavailable"}
    from api.mirror.coordinator import MirrorCoordinator
    coordinator = MirrorCoordinator({"course_context": mirror_service._run_course_context,
                                     "groups": mirror_service._run_groups})
    context_plan = coordinator.submit(["111"], ["course_context"])
    group_plan = coordinator.submit(["222"], ["groups"])
    for _ in range(100):
        if coordinator.status(context_plan)["plans"][0]["state"] == "failed" and \
           coordinator.status(group_plan)["plans"][0]["state"] == "failed":
            break
        time.sleep(0.01)
    assert coordinator.status(context_plan)["plans"][0]["state"] == "failed"
    # Group depends on context, whose missing runner also fails instead of succeeding.
    assert coordinator.status(group_plan)["plans"][0]["state"] == "failed"


# --- background findings refresh ------------------------------------------------------

def test_refresh_work_findings_merges_when_configured(monkeypatch, tmp_path, _configure):
    _configure()
    from api.work_registry import discovery
    merged = []
    monkeypatch.setattr(discovery, "scan_active_courses", lambda: {"ok": True, "courses": {}})
    monkeypatch.setattr(discovery, "merge_into_registry", lambda result: merged.append(result) or {"ok": True})
    mirror_service.refresh_work_findings()
    assert merged == [{"ok": True, "courses": {}}]


def test_refresh_work_findings_skips_partial_scan(monkeypatch, tmp_path, _configure):
    _configure()
    from api.work_registry import discovery
    merged = []
    monkeypatch.setattr(discovery, "scan_active_courses", lambda: {"ok": False})
    monkeypatch.setattr(discovery, "merge_into_registry", lambda result: merged.append(result))
    mirror_service.refresh_work_findings()
    assert merged == []


def test_refresh_work_findings_gated_off_when_disabled(monkeypatch, tmp_path, _configure):
    _configure()
    monkeypatch.setattr(mirror_service.config, "mirror_enabled", lambda: False)
    from api.work_registry import discovery
    called = []
    monkeypatch.setattr(discovery, "scan_active_courses", lambda: called.append(1) or {"ok": True})
    mirror_service.refresh_work_findings()
    assert called == []


# --- write-through notify -------------------------------------------------------------

def _submission_row(*, assignment_id=700010, user_id=900001):
    return {
        "assignment_id": assignment_id, "user_id": user_id,
        "workflow_state": "submitted", "submitted_at": NOW,
        "graded_at": None, "score": None, "grade": None, "late": False,
        "missing": False, "excused": False, "attempt": 1,
        "grade_matches_current_submission": True,
        "submission_type": "online_text_entry", "body": "Draft.",
    }


def test_notify_course_changed_runs_targeted_submission_delta_after_delay(monkeypatch, tmp_path, _configure, _seed_delta_watermarks):
    _configure()
    before = store.read_sync("111")
    canvas = FakeCanvas()
    receipt = lambda *args, **kwargs: ([], None, True)
    monkeypatch.setattr(mirror_service, "canvas_get_all", canvas)
    timer = mirror_service.notify_course_changed(
        "111", delay_seconds=0.01, canvas_get_all_complete=receipt)
    timer.join(timeout=5)
    assert canvas.calls == [
        "/api/v1/courses/111/students/submissions",
        "/api/v1/courses/111/students/submissions",
    ]
    assert store.read_sync("111") == before


def test_notify_course_changed_submits_post_write_scope(monkeypatch, tmp_path, _configure):
    _configure()
    submitted = []

    class FakeCoordinator:
        def submit(self, course_ids, scopes, *, priority):
            submitted.append((tuple(course_ids), tuple(scopes), priority))
            return "post-write-plan"

    monkeypatch.setattr(mirror_service, "coordinator_instance", lambda: FakeCoordinator())
    monkeypatch.setattr(mirror_service, "wait_for_plan", lambda plan_id: {"state": "succeeded"})
    timer = mirror_service.notify_course_changed("111", delay_seconds=0.01)
    timer.join(timeout=5)
    assert submitted == [(('111',), ("submissions.course_delta",), "post_write")]


def test_targeted_submission_delta_returns_sanitized_summary_without_advancing_pass(monkeypatch, tmp_path, _configure, _seed_delta_watermarks):
    _configure()
    before = store.read_sync("111")
    calls = []

    def canvas(path, params=None, timeout=30):
        calls.append((path, dict(params or {})))
        assert path.endswith("/students/submissions")
        if "submitted_since" in params:
            return [_submission_row()], None
        assert "graded_since" in params
        return [], None

    result = mirror_service.sync.refresh_submissions_course_delta(
        "111", canvas_get_all=canvas, now=NOW)

    assert result == {
        "ok": True,
        "scope": "submissions.course_delta",
        "logical_requests": 2,
        "duration_ms": result["duration_ms"],
        "changed_rows": 1,
        "touched_assignments": ["700010"],
    }
    assert result["duration_ms"] >= 0
    assert [params for _path, params in calls] == [
        {"student_ids[]": "all", "per_page": 100,
         "include[]": ["submission_history"],
         "submitted_since": "2026-07-16T10:50:00Z"},
        {"student_ids[]": "all", "per_page": 100,
         "graded_since": "2026-07-16T10:50:00Z"},
    ]
    document = store.read_submissions("111", "700010")
    assert document["submissions"]["900001"]["current"]["body"] == "Draft."
    replay = mirror_service.sync.refresh_submissions_course_delta(
        "111", canvas_get_all=canvas, now=NOW)
    assert replay["ok"] is True
    assert store.read_submissions("111", "700010") == document
    assert store.read_sync("111") == before


def test_targeted_submission_delta_requires_full_without_canvas_requests(monkeypatch, tmp_path, _configure):
    _configure()
    called = []
    result = mirror_service.sync.refresh_submissions_course_delta(
        "111", canvas_get_all=lambda *args, **kwargs: called.append(1))

    assert result["ok"] is False
    assert result["scope"] == "submissions.course_delta"
    assert result["reason"] == "requires_full"
    assert result["logical_requests"] == 0
    assert result["changed_rows"] == 0
    assert result["touched_assignments"] == []
    assert result["duration_ms"] >= 0
    assert called == []


def test_targeted_submission_delta_second_request_failure_does_not_merge_or_advance(monkeypatch, tmp_path, _configure, _seed_delta_watermarks):
    _configure()
    before = store.read_sync("111")
    calls = []

    def canvas(path, params=None, timeout=30):
        calls.append(path)
        if "submitted_since" in params:
            return [_submission_row()], None
        return None, "HTTP 503: upstream"

    result = mirror_service.sync.refresh_submissions_course_delta(
        "111", canvas_get_all=canvas, now=NOW)

    assert result["ok"] is False
    assert result["logical_requests"] == 2
    assert result["error_code"] == "canvas_unavailable"
    assert store.read_submissions("111", "700010") is None
    assert store.read_sync("111") == before
    assert calls == [
        "/api/v1/courses/111/students/submissions",
        "/api/v1/courses/111/students/submissions",
    ]


def test_notify_is_a_no_op_when_disabled(monkeypatch, tmp_path, _configure):
    _configure()
    monkeypatch.setattr(mirror_service.config, "mirror_enabled", lambda: False)
    ran = []
    monkeypatch.setattr(mirror_service.sync, "refresh_submissions_course_delta",
                        lambda *a, **k: ran.append(1) or {"ok": True})
    timer = mirror_service.notify_course_changed("111", delay_seconds=0.01)
    timer.join(timeout=5)
    assert ran == []
