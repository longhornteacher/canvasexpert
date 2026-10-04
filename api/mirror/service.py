"""CanvasMirror scheduling service — heartbeat passes, write-through notify,
and the status payload for the mirror routes.

The heartbeat uses a daemon thread, launch delay, exception isolation, and a
credential gate, and is started from the server
lifespan. All real work lives in plain pass functions that tests drive
directly with injected legacy and complete-only collection clients plus
``now=`` — the thread is never started in tests.

Cadence (per Current course): a full pass when none has succeeded in
FULL_MAX_AGE_HOURS (this is both first-run backfill and the nightly
reconcile), otherwise a delta every tick plus a daily roster refresh.
"""
from __future__ import annotations

import os
import threading
import time
import requests

from api import operational_log
from api.mirror import coordinator, course_context, new_quizzes, store, sync
from api import course_catalog

from api.platform_services import config, workspace
from api.platform_services.canvas_client import (
    canvas_get as _platform_canvas_get,
    canvas_get_all as _platform_canvas_get_all,
    canvas_get_all_complete as _platform_canvas_get_all_complete,
    canvas_stream_get as _platform_canvas_stream_get,
    canvas_get_telemetry,
)
canvas_get = _platform_canvas_get
canvas_get_all = _platform_canvas_get_all
canvas_get_all_complete = _platform_canvas_get_all_complete
canvas_stream_get = _platform_canvas_stream_get
from api.platform_services.canvas_client import canvas_headers


LAUNCH_DELAY_SECONDS = 120        # let startup and the first agent reads settle
TICK_SECONDS = 900                # delta cadence while the app runs
FULL_MAX_AGE_HOURS = 24.0         # backfill + nightly reconcile
ROSTER_MAX_AGE_HOURS = 24.0
NOTIFY_DELAY_SECONDS = 15.0       # write-through settle delay


def due_passes(state: dict, now_iso: str, *,
               serve_max_age_hours: float | None = None) -> list[str]:
    """Which passes one course needs this tick.

    The roster refreshes before it can age past the serve window, not merely
    once a day: the roster file is rewritten only by a full or roster pass
    (never a delta), and reads serve it only while it is younger than the
    serve threshold (~6h). Left at a flat 24h cadence it went unservable for
    most of every day — roster reads refusing while deltas
    kept the gradebook fresh. The daily figure stays as a floor via ``min``,
    so an unusually large serve window still refreshes the roster at least
    once a day.
    """
    full_age = store.age_hours(state["passes"]["full"]["last_success_at"], now_iso)
    if full_age is None or full_age >= FULL_MAX_AGE_HOURS:
        return ["full"]  # covers roster and resets watermarks
    passes = ["delta"]
    if serve_max_age_hours is None:
        serve_max_age_hours = config.mirror_serve_max_age_hours()
    roster_due_age = min(ROSTER_MAX_AGE_HOURS, serve_max_age_hours)
    roster_age = store.age_hours(state["passes"]["roster"]["last_success_at"], now_iso)
    if roster_age is None or roster_age >= roster_due_age:
        passes.append("roster")
    return passes


_PASS_RUNNERS = {"full": sync.full_pass, "delta": sync.delta_pass,
                 "roster": sync.roster_pass}


def _telemetry(scope: str):
    context = coordinator.current_worker_context()
    return canvas_get_telemetry(scope, context.get("priority", "manual"),
                                queue_wait_ms=context.get("queue_wait_ms", 0))


def _run_course_context(course_id: str):
    with _telemetry("course_context"):
        result = course_context.refresh_course_context(
            course_id, canvas_get=canvas_get, canvas_get_all=canvas_get_all)
        return {"ok": result.get("state") == "current", "state": result.get("state", "failed")}


def _run_roster(course_id: str):
    with _telemetry("roster"):
        return sync.roster_pass(course_id, canvas_get_all=canvas_get_all,
                                canvas_get_all_complete=canvas_get_all_complete)


def _run_groups(course_id: str):
    with _telemetry("groups"):
        result = _refresh_groups_on_maintenance(course_id, load_groups=load_group_categories,
                                                now=store.now_iso())
        return {"ok": result.get("state") == "current", "state": result.get("state", "failed")}


def _run_submission_delta(course_id: str):
    with _telemetry("submissions.course_delta"):
        return sync.refresh_submissions_course_delta(
            course_id, canvas_get_all=canvas_get_all,
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base())


def _run_new_quiz_metadata(course_id: str):
    """Metadata-only refresh from the existing local assignment projection."""
    with _telemetry("new_quizzes.metadata"):
        assignment_map = (store.read_assignments(course_id) or {}).get("assignments")
        if not isinstance(assignment_map, dict):
            return {"ok": False, "error_class": "assignment_projection_unavailable"}
        return new_quizzes.sync_metadata(
            course_id, list(assignment_map.values()), canvas_get_all=canvas_get_all,
            bypass_cooldown=coordinator.current_worker_context().get("priority") == "manual")


def _run_course_refresh(course_id: str):
    """Run one durable, revision-producing course refresh."""
    context = coordinator.current_worker_context()
    with _telemetry("course.refresh"):
        if context.get("priority") in {"background", "concluded"}:
            course = next((item for item in config.active_courses()
                           if str(item.get("id")) == str(course_id)), None)
            return (_run_heartbeat_course(
                course, stream_get=canvas_stream_get,
                canvas_origin=config.get_canvas_base()) if course else
                {"ok": False, "error_class": "course_unavailable"})
        course = next((item for item in config.saved_courses()
                       if str(item.get("id")) == str(course_id)), None)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"),
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
            force=True,
        )


def _run_structure_refresh(course_id: str):
    """Refresh the student-free v3 Course Catalog, including full modules.

    Any saved course (Current or Previous): the catalog is student-free and
    read-only, and the catalog reads accept saved courses, so the repair they
    name must work for the same set.
    """
    with _telemetry("course.structure_refresh"):
        course = next((item for item in config.active_courses()
                       if str(item.get("id")) == str(course_id)), None)
        if not course:
            course = next((item for item in config.saved_courses()
                           if str(item.get("id")) == str(course_id)), None)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        result = course_catalog.refresh_catalog(
            course_id,
            course.get("name") or course_id,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
        )
        return {
            "ok": True,
            "state": result.get("result", "partial"),
            "source": result.get("source"),
            "sections": result.get("sections", {}),
            "oldest_section": result.get("oldest_section", ""),
            "oldest_last_success_at": result.get("oldest_last_success_at", ""),
        }


def _run_scoring_course_refresh(course_id: str):
    """Rebuild the course projections used by a scoring session.

    Scoring cannot rely on the ordinary delta watermark: a teacher may have
    changed an assignment or submission in Canvas Live after the last mirror
    pass without changing the delta window in a way that repairs an incomplete
    local projection.  Comments are deliberately excluded because scoring
    preparation does not read them and they are an independent mirror concern.
    """
    with _telemetry("course.scoring_refresh"):
        course = next((item for item in config.saved_courses()
                       if str(item.get("id")) == str(course_id)), None)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"),
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
            force=True, full=True, with_comments=False,
        )


def _run_feedback_course_refresh(course_id: str):
    """Acquire complete comment identities/staff proof only on teacher opt-in."""
    with _telemetry("course.feedback_refresh"):
        course = next((item for item in config.saved_courses()
                       if str(item.get("id")) == str(course_id)), None)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id, canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"), force=True, full=True, with_comments=True)


def _run_scoring_discovery_refresh(course_id: str):
    """Run the discovery-owned full refresh scope.

    The coordinator reuses this scope's recent successful job for discovery
    continuations. The runner stays a real full refresh when a new job is
    needed, while assignment preparation keeps its stricter separate scope.
    """
    with _telemetry("course.scoring_discovery_refresh"):
        course = next((item for item in config.saved_courses()
                       if str(item.get("id")) == str(course_id)), None)
        if not course:
            return {"ok": False, "error_class": "course_unavailable"}
        return sync.refresh(
            course_id,
            canvas_get_all=canvas_get_all,
            canvas_get_all_complete=canvas_get_all_complete,
            course_name=course.get("name"),
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base(),
            force=False, full=True, with_comments=False,
        )


def coordinator_instance() -> coordinator.MirrorCoordinator:
    """The sole production registry.  Every runner above is read-only."""
    return coordinator.configure_default({
        "course.refresh": _run_course_refresh,
        "course_context": _run_course_context,
        "roster": _run_roster,
        "groups": _run_groups,
        "course.scoring_refresh": _run_scoring_course_refresh,
        "course.feedback_refresh": _run_feedback_course_refresh,
        "course.scoring_discovery_refresh": _run_scoring_discovery_refresh,
        "submissions.course_delta": _run_submission_delta,
        "new_quizzes.metadata": _run_new_quiz_metadata,
        "course.structure_refresh": _run_structure_refresh,
    })


def enqueue_sync(course_id: str | None = None, scopes: list[str] | None = None,
                 *, reuse_completed_within_seconds: float = 0) -> str:
    """Queue manual read-only work; HTTP callers receive the opaque plan ID."""
    courses = [course for course in config.saved_courses()
               if not course_id or str(course.get("id")) == str(course_id)]
    if not courses:
        raise ValueError("Not a saved course.")
    kwargs = {"priority": "manual"}
    if reuse_completed_within_seconds:
        kwargs["reuse_completed_within_seconds"] = reuse_completed_within_seconds
    return coordinator_instance().submit(
        (str(course.get("id")) for course in courses), scopes, **kwargs
    )


def refresh_course_structure(course_id: str, *, timeout_seconds: float = 30.0) -> dict:
    """Refresh only through the coordinator and expose no Canvas rows.

    Accepts any saved course (Current or Previous) so the catalog reads'
    ``refresh_mirror(structure_only=true)`` repair is executable for exactly
    the set of courses those reads accept.
    """
    if not any(str(course.get("id")) == str(course_id)
               for course in config.saved_courses()):
        raise ValueError("Not a saved course.")
    plan_id = coordinator_instance().submit(
        [str(course_id)], ["course.structure_refresh"], priority="manual",
    )
    plan = wait_for_plan(plan_id, timeout_seconds=timeout_seconds)
    catalog = course_catalog.read_catalog(str(course_id)).get("catalog") or {}
    module_scope = catalog.get("modules") if isinstance(catalog, dict) else {}
    summary = course_catalog.catalog_status_summary(catalog)
    return {
        "ok": plan.get("state") == "succeeded",
        "status": plan.get("status") or plan.get("state"),
        "operation_id": plan_id,
        "revision": str((module_scope or {}).get("last_success_at") or ""),
        "state": (module_scope or {}).get("state", "unavailable"),
        "result": summary["result"],
        "sections": summary["sections"],
        "oldest_section": summary["oldest_section"],
        "oldest_last_success_at": summary["oldest_last_success_at"],
        "error_code": next((job.get("error_code") for job in plan.get("jobs", []) if job.get("error_code")), ""),
    }


def enqueue_heartbeat_refreshes() -> list[str]:
    """Queue one compatibility refresh job per configured course, never direct GET work."""
    if not config.token_is_set() or not config.mirror_enabled() or workspace.workspace_root() is None:
        return []
    instance = coordinator_instance()
    plans = []
    for course in config.active_courses():
        course_id = str(course.get("id") or "")
        if not course_id:
            continue
        context = store.read_course_context(course_id)
        priority = ("concluded" if context["lifecycle"] == "concluded" and
                    context["state"] in {"current", "stale"} else "background")
        plans.append(instance.submit([course_id], ["course.refresh"], priority=priority))
    return plans


def wait_for_plan(plan_id: str, *, poll_seconds: float = 0.05,
                  timeout_seconds: float | None = None) -> dict:
    """Heartbeat/timer helper: workers own I/O while this helper only observes state.

    ``timeout_seconds`` bounds the wait for callers that must not block a
    request indefinitely (e.g. the MCP refresh tool); the default of None
    preserves the original unbounded behavior used by the heartbeat."""
    instance = coordinator_instance()
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while True:
        plans = instance.status(plan_id).get("plans", [])
        if not plans or plans[0]["state"] in {"succeeded", "failed", "cancelled"}:
            return plans[0] if plans else {"state": "failed"}
        if deadline is not None and time.monotonic() >= deadline:
            return plans[0]
        time.sleep(poll_seconds)


def run_coordinated_heartbeat_tick() -> None:
    """One daemon tick: workers acquire and refresh configured courses."""
    for plan_id in enqueue_heartbeat_refreshes():
        wait_for_plan(plan_id)


def _refresh_groups_on_maintenance(course_id: str, *, load_groups, now: str) -> dict:
    """Best-effort private group refresh nested under roster/full maintenance."""
    try:
        categories, error, _message = load_groups(course_id)
        if error:
            document = store.mark_groups_stale(course_id, attempted_at=now)
            return {"state": document["state"] if document else "unavailable",
                    "error_code": "refresh_failed"}
        store.write_groups(course_id, categories, attempted_at=now)
        return {"state": "current", "error_code": ""}
    except Exception:
        try:
            document = store.mark_groups_stale(course_id, attempted_at=now)
        except Exception:
            document = None
        return {"state": document["state"] if document else "unavailable",
                "error_code": "refresh_failed"}


def _run_heartbeat_course(course: dict, *, canvas_get=None, canvas_get_all=None,
                          canvas_get_all_complete=None, load_groups=None, now=None,
                          stream_get=None, canvas_origin="") -> dict:
    """One course's legacy cadence, called inside a background coordinator job."""
    canvas_get = canvas_get or globals()["canvas_get"]
    canvas_get_all = canvas_get_all or globals()["canvas_get_all"]
    canvas_get_all_complete = canvas_get_all_complete or globals()["canvas_get_all_complete"]
    load_groups = load_groups or load_group_categories
    now_iso = now or store.now_iso()
    course_id = str((course or {}).get("id") or "")
    if not course_id:
        return {"ok": False, "error_class": "course_unavailable", "results": []}
    try:
        context = course_context.ensure_course_context(
            course_id, canvas_get=canvas_get, canvas_get_all=canvas_get_all, now=now_iso)
    except Exception:
        context = store.read_course_context(course_id)
    state = store.read_sync(course_id)
    pass_names = due_passes(state, now_iso)
    concluded = (context["lifecycle"] == "concluded" and context["state"] in {"current", "stale"})
    if concluded and "full" not in pass_names:
        return {"ok": True, "results": []}
    summaries = []
    for pass_name in pass_names:
        pass_started = time.monotonic()
        try:
            kwargs = {"canvas_get_all": canvas_get_all, "now": now_iso}
            if pass_name in {"full", "delta", "roster"}:
                kwargs["canvas_get_all_complete"] = canvas_get_all_complete
            if pass_name in {"full", "delta"}:
                kwargs["course_name"] = course.get("name")
                kwargs["stream_get"] = stream_get
                kwargs["canvas_origin"] = canvas_origin
            if concluded and pass_name == "full":
                kwargs["skip_new_quiz_metadata"] = True
            result = _PASS_RUNNERS[pass_name](course_id, **kwargs)
        except Exception as error:
            result = {"ok": False, "error_class": type(error).__name__}
        if result.get("ok") and pass_name in {"full", "roster"}:
            result = {**result, "groups": _refresh_groups_on_maintenance(
                course_id, load_groups=load_groups, now=now_iso)}
        _emit_refresh_outcome(
            pass_name, result,
            duration_ms=max(0, int(round((time.monotonic() - pass_started) * 1000))),
        )
        summaries.append({"course_id": course_id, "pass": pass_name, **result})
    return {"ok": all(item.get("ok") for item in summaries), "results": summaries}


def _emit_refresh_outcome(scope: str, result: dict, *, duration_ms: int = 0) -> None:
    if duration_ms is None:
        duration_ms = 0
    outcome = "ok" if result.get("ok") else (
        "unconfigured" if result.get("state") == "unconfigured" else "failed")
    operational_log.emit("mirror.refresh", outcome, scope=scope,
                         duration_ms=duration_ms)


def notify_course_changed(course_id, *, delay_seconds: float = NOTIFY_DELAY_SECONDS):
    """Write-through hook for a narrow post-write submission refresh.

    The ordinary heartbeat still owns the full delta pass.  This delayed,
    fire-and-forget hook must not imply structure or New Quiz freshness.
    """

    def _run():
        try:
            if not config.token_is_set() or not config.mirror_enabled():
                return
            if workspace.workspace_root() is None:
                return
            plan_id = coordinator_instance().submit(
                [str(course_id)], ["submissions.course_delta"], priority="post_write")
            wait_for_plan(plan_id)
        except Exception as exc:
            operational_log.emit("mirror.notify_course_changed", "failed", error_class=type(exc))

    timer = threading.Timer(delay_seconds, _run)
    timer.daemon = True
    timer.start()
    return timer


def status(plan_id: str | None = None) -> dict:
    courses = []
    for course in config.active_courses():
        course_id = str(course.get("id") or "")
        if not course_id:
            continue
        state = store.read_sync(course_id)
        courses.append({
            "course_id": course_id,
            "course_name": config.course_display_name(course_id),
            "passes": state["passes"],
            "watermarks": state["watermarks"],
            "context": store.read_course_context(course_id),
        })
    payload = {
        "ok": True,
        "enabled": config.mirror_enabled(),
        "workspace_configured": workspace.workspace_root() is not None,
        "serve_max_age_hours": config.mirror_serve_max_age_hours(),
        "courses": courses,
        "vault_conflict": _vault_conflict_files(),
    }
    if plan_id:
        payload["plan"] = coordinator_instance().status(plan_id)
    return payload


def _vault_conflict_files() -> list[str]:
    """Basenames of conflict copies anywhere in _Shared.

    This status path must work even when the Identity Vault seed itself cannot
    be opened, so it scans filenames without constructing the vault or reading
    any file content.
    """
    try:
        from api.shared_storage import scan_conflicts
        return [os.path.basename(item["path"]) for item in scan_conflicts()]
    except Exception:
        return []


def mirror_heartbeat_worker(stop_event):
    """Run the mirror cadence until the runtime asks this worker to stop."""
    if stop_event.wait(LAUNCH_DELAY_SECONDS):
        return
    while not stop_event.is_set():
        try:
            run_coordinated_heartbeat_tick()
        except Exception as exc:
            operational_log.emit("mirror.heartbeat_tick", "failed", error_class=type(exc))
        if stop_event.wait(TICK_SECONDS):
            return


def fetch_group_category_groups(course_id: str, category_id: str) -> tuple[list[dict] | None, str | None]:
    """Fetch one group category's groups + memberships live from Canvas.

    Factored out of ``load_group_categories``'s whole-course loop so both the
    full course refresh and a single-category targeted reconciliation share
    one Canvas-shape-normalization path (id/name/student_ids/memberships per
    group). Returns ``(groups_out, None)`` on success, ``(None, err)`` on any
    non-200/transport failure — never raises.
    """
    hdrs, base = canvas_headers()
    if not hdrs:
        return None, "No token saved."

    def get(path, params=None):
        try:
            r = requests.get(f"{base}{path}", headers=hdrs,
                             params=params or {}, timeout=20)
            return (r.status_code, r.json() if r.status_code == 200 else None)
        except Exception:
            return (0, None)

    def memberships(group_id):
        """Return list of membership dicts for a group."""
        st, members = get(f"/api/v1/groups/{group_id}/memberships", {"per_page": 200})
        return members or []

    st, groups_raw = get(f"/api/v1/group_categories/{category_id}/groups", {"per_page": 100})
    if st != 200:
        return None, f"Canvas returned {st} for group_categories/{category_id}/groups."

    groups_out = []
    for grp in (groups_raw or []):
        grp_id = str(grp["id"])
        mems = memberships(grp_id)
        groups_out.append({
            "id":          grp_id,
            "name":        grp["name"],
            "student_ids": [m["user_id"] for m in mems],
            "memberships": mems,
        })
    return groups_out, None


def load_group_categories(course_id: str) -> tuple[list[dict], str | None, str]:
    """Return (categories, error, message) for a course's group sets.

    Canvas note (confirmed live 2026-06): teacher PATs may get 403 on every
    /group_categories endpoint (district permission), while
    /courses/:id/groups still returns the same groups with their
    group_category_id. So: try group_categories for proper set names, fall
    back to bucketing /courses/:id/groups by category id.

    V3: Also returns membership IDs for Canvas write operations.
    """
    hdrs, base = canvas_headers()
    if not hdrs:
        return [], "No token saved.", ""

    def get(path, params=None):
        try:
            r = requests.get(f"{base}{path}", headers=hdrs,
                             params=params or {}, timeout=20)
            return (r.status_code, r.json() if r.status_code == 200 else None)
        except Exception as e:
            return (0, None)

    def memberships(group_id):
        """Return list of membership dicts for a group."""
        st, members = get(f"/api/v1/groups/{group_id}/memberships", {"per_page": 200})
        return members or []

    # Preferred path: real group sets with names.
    st, cats = get(f"/api/v1/courses/{course_id}/group_categories", {"per_page": 50})
    if st == 200 and cats:
        result = []
        for cat in cats:
            # Status intentionally ignored here (unchanged from prior
            # behavior): a per-category fetch failure degrades to an empty
            # groups list for that one category rather than failing the
            # whole-course load.
            groups_out, _err = fetch_group_category_groups(course_id, cat["id"])
            result.append({"category_id":   str(cat["id"]),
                           "category_name": cat["name"],
                           "groups":        groups_out or []})
        return result, None, ""

    # Fallback: course groups bucketed by category id (category names 403-gated).
    st2, groups_raw = get(f"/api/v1/courses/{course_id}/groups", {"per_page": 100})
    if st2 != 200:
        return [], (f"Canvas returned {st or st2} for course groups "
                    f"(group_categories: {st}; groups: {st2})."), ""

    if not groups_raw:
        return [], None, "No group sets found in this course."

    buckets = {}
    for grp in groups_raw:
        buckets.setdefault(str(grp.get("group_category_id") or "0"), []).append(grp)
    result = []
    for i, (cat_id, grps) in enumerate(sorted(buckets.items()), start=1):
        result.append({
            "category_id":   cat_id,
            "category_name": "Group set" if len(buckets) == 1 else f"Group set {i}",
            "groups": [{
                "id":          str(grp["id"]),
                "name":        grp["name"],
                "student_ids": [m["user_id"] for m in (memberships(grp["id"]) or [])],
                "memberships": memberships(grp["id"]),
            } for grp in grps],
        })
    return result, None, ""
