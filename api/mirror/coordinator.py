"""Small, process-local coordinator for read-only CanvasMirror work.

The coordinator deliberately knows nothing about Canvas transport or projection
formats.  Its caller supplies the (read-only) scope runners.  This keeps the
scheduler on the acquisition side of the live-command boundary.
"""
from __future__ import annotations

import contextlib
import contextvars
import heapq
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable


PRIORITIES = ("post_write", "manual", "background", "concluded")
_PRIORITY_VALUE = {name: index for index, name in enumerate(PRIORITIES)}
PRODUCTION_SCOPES = (
    "course.refresh", "course_context", "roster", "groups",
    "course.scoring_refresh", "course.scoring_discovery_refresh",
    "submissions.course_delta", "new_quizzes.metadata",
    "course.structure_refresh",
)

_WORKER = contextvars.ContextVar("canvasmirror_worker", default=None)


@dataclass
class _Job:
    job_id: str
    plan_id: str
    course_id: str
    scope: str
    priority: str
    sequence: int
    state: str = "queued"
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    error_class: str = ""
    error_code: str = ""
    yields: int = 0
    queue_wait_ms: int = 0
    mirror_revision: int = 0
    snapshot_id: str = ""


@dataclass
class _Plan:
    plan_id: str
    state: str = "queued"
    jobs: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.monotonic)
    operation_id: str = ""


class MirrorCoordinator:
    """Bounded two-worker coordinator with coalescing and foreground yielding."""

    def __init__(self, runners: dict[str, Callable[[str], object]] | None = None, *,
                 workers: int = 2, history_limit: int = 100):
        if workers != 2:
            raise ValueError("CanvasMirror uses exactly two workers")
        if history_limit < 1:
            raise ValueError("history limit must be positive")
        self._runners = dict(runners or {})
        self._lock = threading.Condition(threading.RLock())
        self._queue: list[tuple[int, int, str]] = []
        self._jobs: dict[str, _Job] = {}
        self._plans: dict[str, _Plan] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._sequence = 0
        self._foreground = 0
        self._history_limit = history_limit
        self._closed = False
        self._threads = [threading.Thread(target=self._worker, daemon=True,
                                          name=f"canvasmirror-{index + 1}")
                         for index in range(workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, course_ids: Iterable[str], scopes: Iterable[str] | None = None,
               *, priority: str = "manual",
               reuse_completed_within_seconds: float = 0) -> str:
        if priority not in _PRIORITY_VALUE:
            raise ValueError("unsupported coordinator priority")
        if reuse_completed_within_seconds < 0:
            raise ValueError("reuse window must not be negative")
        requested = tuple(scopes or ("course.refresh",))
        if not requested or any(scope not in PRODUCTION_SCOPES for scope in requested):
            raise ValueError("unsupported mirror scope")
        course_ids = tuple(str(course_id) for course_id in course_ids if str(course_id))
        if not course_ids:
            raise ValueError("at least one course is required")
        with self._lock:
            plan_id = uuid.uuid4().hex
            plan = _Plan(plan_id)
            self._plans[plan_id] = plan
            reused_plan_ids = set()
            reused_all = True
            for course_id in course_ids:
                for scope in requested:
                    key = (course_id, scope)
                    existing_id = self._by_key.get(key)
                    existing = self._jobs.get(existing_id) if existing_id else None
                    if existing and existing.state in {"queued", "running"}:
                        plan.jobs.append(existing.job_id)
                        reused_plan_ids.add(existing.plan_id)
                        if _PRIORITY_VALUE[priority] < _PRIORITY_VALUE[existing.priority]:
                            existing.priority = priority
                            if existing.state == "queued":
                                self._sequence += 1
                                existing.sequence = self._sequence
                                heapq.heappush(self._queue, (_PRIORITY_VALUE[priority], existing.sequence,
                                                            existing.job_id))
                        continue
                    if (
                        existing
                        and existing.state == "succeeded"
                        and reuse_completed_within_seconds
                        and existing.finished_at is not None
                        and time.monotonic() - existing.finished_at
                        <= reuse_completed_within_seconds
                    ):
                        plan.jobs.append(existing.job_id)
                        reused_plan_ids.add(existing.plan_id)
                        continue
                    reused_all = False
                    self._sequence += 1
                    job = _Job(uuid.uuid4().hex, plan_id, course_id, scope, priority,
                               self._sequence)
                    self._jobs[job.job_id] = job
                    self._by_key[key] = job.job_id
                    plan.jobs.append(job.job_id)
                    heapq.heappush(self._queue, (_PRIORITY_VALUE[priority], job.sequence, job.job_id))
            if reused_all and len(reused_plan_ids) == 1:
                plan.operation_id = next(iter(reused_plan_ids))
            self._trim_locked()
            self._refresh_plan_locked(plan)
            self._lock.notify_all()
            return plan_id

    @contextlib.contextmanager
    def foreground_interval(self):
        with self._lock:
            self._foreground += 1
            self._lock.notify_all()
        try:
            yield
        finally:
            with self._lock:
                self._foreground = max(0, self._foreground - 1)
                self._lock.notify_all()

    def before_physical_get(self) -> tuple[int, bool]:
        """Yield background work before a physical GET; returns (wait_ms, cancelled)."""
        job_id = _WORKER.get()
        if not job_id:
            return 0, False
        started = time.monotonic()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return 0, True
            while job.priority in {"background", "concluded"} and self._foreground > 0:
                job.yields += 1
                self._lock.wait(timeout=0.1)
        return int((time.monotonic() - started) * 1000), False

    def current_worker_context(self) -> dict:
        """Safe scheduling facts for a runner/telemetry context."""
        job_id = _WORKER.get()
        with self._lock:
            job = self._jobs.get(job_id) if job_id else None
            if not job:
                return {}
            return {"scope": job.scope, "priority": job.priority,
                    "queue_wait_ms": job.queue_wait_ms}

    def bind_current_worker(self, callback):
        """Bind the active worker marker into helper threads owned by a runner."""
        job_id = _WORKER.get()
        if not job_id:
            return callback
        def bound(*args, **kwargs):
            token = _WORKER.set(job_id)
            try:
                return callback(*args, **kwargs)
            finally:
                _WORKER.reset(token)
        return bound

    def status(self, plan_id: str | None = None) -> dict:
        with self._lock:
            plans = [self._plans[plan_id]] if plan_id in self._plans else ([] if plan_id else list(self._plans.values()))
            return {"ok": bool(plans) or plan_id is None, "plans": [self._plan_view(plan) for plan in plans]}

    def _plan_view(self, plan: _Plan) -> dict:
        self._refresh_plan_locked(plan)
        return {"plan_id": plan.plan_id, "operation_id": plan.operation_id or plan.plan_id,
                "state": plan.state,
                "status": "syncing" if plan.state in {"queued", "running"}
                          else ("synced" if plan.state == "succeeded" else "failed"),
                "jobs": [self._job_view(self._jobs[job_id]) for job_id in plan.jobs if job_id in self._jobs]}

    @staticmethod
    def _job_view(job: _Job) -> dict:
        view = {"job_id": job.job_id, "course_id": job.course_id, "scope": job.scope,
                "priority": job.priority, "state": job.state, "error_class": job.error_class,
                "error_code": job.error_code,
                "queue_wait_ms": job.queue_wait_ms, "yield_count": job.yields}
        if job.mirror_revision or job.snapshot_id:
            view.update({"mirror_revision": job.mirror_revision,
                         "snapshot_id": job.snapshot_id})
        return view

    def _refresh_plan_locked(self, plan: _Plan) -> None:
        states = [self._jobs[job_id].state for job_id in plan.jobs if job_id in self._jobs]
        if any(state == "running" for state in states): plan.state = "running"
        elif any(state == "queued" for state in states): plan.state = "queued"
        elif any(state == "failed" for state in states): plan.state = "failed"
        else: plan.state = "succeeded"

    def _worker(self) -> None:
        while True:
            with self._lock:
                while not self._closed and not self._queue:
                    self._lock.wait()
                if self._closed:
                    return
                _priority, sequence, job_id = heapq.heappop(self._queue)
                job = self._jobs.get(job_id)
                if not job or job.state != "queued" or job.sequence != sequence:
                    continue
                job.state, job.started_at = "running", time.monotonic()
                job.queue_wait_ms = max(0, int((job.started_at - job.created_at) * 1000))
            token = _WORKER.set(job_id)
            try:
                runner = self._runners.get(job.scope)
                if runner is None:
                    raise RuntimeError("runner_unavailable")
                outcome = runner(job.course_id)
                with self._lock:
                    if isinstance(outcome, dict) and (outcome.get("ok") is False or
                                                       outcome.get("state") == "failed"):
                        job.state = "failed"
                        job.error_class = str(outcome.get("error_class") or "acquisition_failed")
                        job.error_code = str(outcome.get("error_code") or "")
                    else:
                        job.state = "succeeded"
                    if isinstance(outcome, dict):
                        job.mirror_revision = int(outcome.get("mirror_revision") or 0)
                        job.snapshot_id = str(outcome.get("snapshot_id") or "")
            except Exception as error:
                with self._lock:
                    job.state = "failed"
                    job.error_class = type(error).__name__
            finally:
                _WORKER.reset(token)
                with self._lock:
                    job.finished_at = time.monotonic()
                    self._refresh_plan_locked(self._plans[job.plan_id])
                    self._trim_locked()
                    self._lock.notify_all()

    def _trim_locked(self) -> None:
        """Keep a bounded completed history without dropping live plans/jobs."""
        completed = [plan for plan in self._plans.values()
                     if self._refresh_and_return_completed(plan)]
        for plan in sorted(completed, key=lambda item: item.created_at)[:-self._history_limit]:
            self._plans.pop(plan.plan_id, None)
        referenced = {job_id for plan in self._plans.values() for job_id in plan.jobs}
        for job_id, job in list(self._jobs.items()):
            if job_id not in referenced and job.state in {"succeeded", "failed"}:
                self._jobs.pop(job_id, None)
                if self._by_key.get((job.course_id, job.scope)) == job_id:
                    self._by_key.pop((job.course_id, job.scope), None)

    def _refresh_and_return_completed(self, plan: _Plan) -> bool:
        self._refresh_plan_locked(plan)
        return plan.state in {"succeeded", "failed"}


_DEFAULT: MirrorCoordinator | None = None
_DEFAULT_LOCK = threading.Lock()


def configure_default(runners: dict[str, Callable[[str], object]]) -> MirrorCoordinator:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = MirrorCoordinator(runners)
        return _DEFAULT


def default() -> MirrorCoordinator:
    if _DEFAULT is None:
        raise RuntimeError("CanvasMirror coordinator has not been configured")
    return _DEFAULT


def foreground_interval():
    return default().foreground_interval() if _DEFAULT is not None else contextlib.nullcontext()


def before_physical_get() -> tuple[int, bool]:
    return default().before_physical_get() if _DEFAULT is not None else (0, False)


def current_worker_context() -> dict:
    return default().current_worker_context() if _DEFAULT is not None else {}


def bind_current_worker(callback):
    return default().bind_current_worker(callback) if _DEFAULT is not None else callback
