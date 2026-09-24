"""Mirror-first reads for background/display consumers (routines, sweep preview).

Three thin helpers — one per course-scoped dataset the flipped routine/sweep
call sites request. Each serves from ``api/mirror/queries.py`` only when the
relevant collection is fresh (design law #4), else falls back to the existing
live Canvas call unchanged, preserving the exact params those call sites used
before. ``source`` is always ``"mirror"`` or ``"canvas"`` so callers that
surface it keep staleness visible; callers that only aggregate internally may
ignore it (locked decision 2).

Reads that feed a Canvas write do not use this compatibility module — new
write paths use typed mirror scopes directly and let their reviewed adapters
perform the live pre-write checks.

Compatibility shim (1.0-beta). Owner: the CanvasMirror read-spine migration
(spine §20 Maintainability — "compatibility shims have an owner and removal
condition"). Remaining consumers are the not-yet-migrated routine/sweep/curve
read call sites, pinned by
the remaining legacy routine-read compatibility tests.
Removal condition: once those call sites read ``api.mirror.read_service`` typed
scopes directly — as Home, Work, Course Info, and PowerGrader already do — delete
this module together with that test. Do not add new consumers; new code calls
``read_service`` directly.
"""
from __future__ import annotations

from api.mirror import queries as mirror_queries
from api.mirror import read_service

from api.platform_services.canvas_client import canvas_get_all


def students_or_live(course_id):
    """``(rows, error, source)`` — course roster, mirror-first."""
    state = read_service.private_roster(
        course_id, max_age_hours=mirror_queries._serve_max_age_hours())
    if state["state"] == "current":
        rows, error = mirror_queries.course_students(course_id)
        if not error and isinstance(rows, list):
            return rows, None, "mirror"
    rows, error = canvas_get_all(
        f"/api/v1/courses/{course_id}/users",
        {"enrollment_type[]": "student", "per_page": 100})
    return rows, error, "canvas"


def assignments_or_live(course_id):
    """``(rows, error, source)`` — course assignment index, mirror-first."""
    state = read_service.private_assignments(
        course_id, max_age_hours=mirror_queries._serve_max_age_hours())
    if state["state"] == "current":
        rows, error = mirror_queries.course_assignments(course_id)
        if not error and isinstance(rows, list):
            return rows, None, "mirror"
    rows, error = canvas_get_all(
        f"/api/v1/courses/{course_id}/assignments", {"per_page": 100})
    return rows, error, "canvas"


def submissions_or_live(course_id):
    """``(rows, error, source)`` — all course submissions, mirror-first."""
    state = read_service.private_submissions(
        course_id, max_age_hours=mirror_queries._serve_max_age_hours())
    if state["state"] == "current":
        rows, error = mirror_queries.course_submissions(course_id)
        if not error and isinstance(rows, list):
            return rows, None, "mirror"
    rows, error = canvas_get_all(
        f"/api/v1/courses/{course_id}/students/submissions",
        {"student_ids[]": "all", "per_page": 100}, timeout=60)
    return rows, error, "canvas"
