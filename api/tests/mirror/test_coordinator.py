"""Offline contract tests for the fixed CanvasMirror coordinator."""
from __future__ import annotations

import threading
import time

from api.mirror.coordinator import MirrorCoordinator


def _wait(coordinator, plan_id):
    for _ in range(100):
        plan = coordinator.status(plan_id)["plans"][0]
        if plan["state"] in {"succeeded", "failed"}:
            return plan
        time.sleep(0.01)
    raise AssertionError("coordinator did not settle")


def test_coalescing_and_priority_promotion():
    calls = []
    started = threading.Event()
    release = threading.Event()

    def run(course_id):
        started.set()
        release.wait(1)
        calls.append(course_id)

    coordinator = MirrorCoordinator({
        "course_context": lambda _course: None, "roster": lambda _course: None,
        "groups": run, "submissions.course_delta": lambda _course: None,
        "new_quizzes.metadata": lambda _course: None,
    })
    first = coordinator.submit(["local-course"], ["groups"], priority="background")
    assert started.wait(1)
    second = coordinator.submit(["local-course"], ["groups"], priority="manual")
    assert coordinator.status(second)["plans"][0]["jobs"][0]["priority"] == "manual"
    release.set()
    first_plan = _wait(coordinator, first)
    second_plan = _wait(coordinator, second)
    assert first_plan["state"] == second_plan["state"] == "succeeded"
    assert first_plan["jobs"][0]["job_id"] == second_plan["jobs"][0]["job_id"]
    assert calls == ["local-course"]
    assert all(set(job) <= {"job_id", "course_id", "scope", "priority", "state",
                            "error_class", "error_code", "queue_wait_ms", "yield_count"} for job in first_plan["jobs"])
    assert first_plan["operation_id"] == second_plan["operation_id"]


def test_recent_successful_discovery_job_is_reused_with_stable_operation_id():
    calls = []
    coordinator = MirrorCoordinator({
        "course.scoring_discovery_refresh": lambda course: calls.append(course) or {
            "ok": True, "mirror_revision": 7, "snapshot_id": "snap-7",
        },
    })

    first = coordinator.submit(["course"], ["course.scoring_discovery_refresh"])
    first_plan = _wait(coordinator, first)
    second = coordinator.submit(
        ["course"], ["course.scoring_discovery_refresh"],
        reuse_completed_within_seconds=300,
    )
    second_plan = _wait(coordinator, second)

    assert calls == ["course"]
    assert second_plan["operation_id"] == first_plan["operation_id"]
    assert second_plan["jobs"][0]["job_id"] == first_plan["jobs"][0]["job_id"]


def test_discovery_reuse_window_does_not_reuse_an_old_success():
    calls = []
    coordinator = MirrorCoordinator({
        "course.scoring_discovery_refresh": lambda course: calls.append(course) or {
            "ok": True, "mirror_revision": len(calls),
        },
    })

    first = coordinator.submit(["course"], ["course.scoring_discovery_refresh"])
    _wait(coordinator, first)
    with coordinator._lock:
        job = next(iter(coordinator._jobs.values()))
        job.finished_at = time.monotonic() - 301
    second = coordinator.submit(
        ["course"], ["course.scoring_discovery_refresh"],
        reuse_completed_within_seconds=300,
    )
    _wait(coordinator, second)

    assert calls == ["course", "course"]


def test_independent_scopes_fail_and_succeed_independently():
    def fail(_course):
        raise RuntimeError("private detail must not escape")

    coordinator = MirrorCoordinator({
        "course_context": fail, "roster": lambda _course: None, "groups": lambda _course: None,
        "submissions.course_delta": lambda _course: None, "new_quizzes.metadata": lambda _course: None,
    })
    failed = coordinator.submit(["course"], ["course_context", "roster"], priority="manual")
    failed_plan = _wait(coordinator, failed)
    assert failed_plan["state"] == "failed"
    assert {job["state"] for job in failed_plan["jobs"]} == {"failed", "succeeded"}
    course_context_job = next(job for job in failed_plan["jobs"] if job["scope"] == "course_context")
    assert course_context_job["error_class"] == "RuntimeError"


def test_background_get_yields_to_foreground():
    gate = threading.Event()
    observed = []
    holder = {}

    def run(_course):
        gate.set()
        observed.append(holder["coordinator"].before_physical_get())

    coordinator = MirrorCoordinator({
        "course_context": run, "roster": run,
        "groups": run, "submissions.course_delta": run, "new_quizzes.metadata": run,
    })
    holder["coordinator"] = coordinator
    with coordinator.foreground_interval():
        plan_id = coordinator.submit(["course"], ["course_context"], priority="background")
        assert gate.wait(1)
        time.sleep(0.03)
        assert observed == []
    plan = _wait(coordinator, plan_id)
    assert plan["state"] == "succeeded"
    assert observed and observed[0][0] >= 20 and observed[0][1] is False


def test_course_refresh_is_one_compatibility_job_and_history_is_bounded():
    calls = []
    coordinator = MirrorCoordinator({"course.refresh": lambda course: calls.append(course) or {"ok": True}},
                                    history_limit=2)
    first = coordinator.submit(["one"])
    assert _wait(coordinator, first)["jobs"][0]["scope"] == "course.refresh"
    for course in ("two", "three", "four"):
        assert _wait(coordinator, coordinator.submit([course]))["state"] == "succeeded"
    # The next submit trims atomically: stale plans, jobs, and coalescing keys
    # disappear together while the two newest completed plans remain observable.
    assert len(coordinator._plans) == 2
    assert len(coordinator._jobs) == 2
    assert len(coordinator._by_key) == 2
    assert calls == ["one", "two", "three", "four"]


def test_failed_job_does_not_block_unrelated_course_or_scope():
    ran = []
    coordinator = MirrorCoordinator({
        "course_context": lambda course: {"ok": False} if course == "bad" else {"ok": True},
        "submissions.course_delta": lambda course: ran.append(("delta", course)) or {"ok": True},
        "roster": lambda course: ran.append(("roster", course)) or {"ok": True},
        "groups": lambda course: ran.append(("groups", course)) or {"ok": True},
        "new_quizzes.metadata": lambda course: {"ok": True},
        "course.refresh": lambda course: {"ok": True},
    })
    failed = coordinator.submit(["bad"], ["course_context"])
    unrelated = coordinator.submit(["good"], ["submissions.course_delta"])
    failed_plan = _wait(coordinator, failed)
    assert failed_plan["state"] == "failed"
    assert failed_plan["jobs"][0]["state"] == "failed"
    assert _wait(coordinator, unrelated)["state"] == "succeeded"
    assert ran == [("delta", "good")]


def test_concluded_get_yields_to_foreground():
    entered = threading.Event()
    observed = []
    holder = {}

    def run(_course):
        entered.set()
        observed.append(holder["coordinator"].before_physical_get())

    coordinator = MirrorCoordinator({"course.refresh": run})
    holder["coordinator"] = coordinator
    with coordinator.foreground_interval():
        plan_id = coordinator.submit(["concluded"], priority="concluded")
        assert entered.wait(1)
        time.sleep(0.03)
        assert observed == []
    assert _wait(coordinator, plan_id)["state"] == "succeeded"
    assert observed[0][0] >= 20
