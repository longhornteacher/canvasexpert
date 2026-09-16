"""Gradebook route registration facade.

Keep this module stable for server registration and compatibility imports.
The implementation is split by feature under ``routes/gradebook_*.py``.
"""
from fastapi import APIRouter

from .gradebook_common import (
    _assignment,
    _assignment_submissions,
    _course_assignments,
    _course_students,
    _course_submissions,
)
from .gradebook_curves import (
    curve_apply,
    curve_assignments,
    curve_preview,
    list_curve_events,
    revert_curve,
    router as _curves_router,
)
from .gradebook_extra_time import (
    get_extra_time,
    get_tier_tags_route,
    list_students,
    router as _extra_time_router,
    save_extra_time,
    save_tier_tags,
)
from .gradebook_policy import (
    apply_late_policy,
    get_late_policy,
    router as _policy_router,
)
from .gradebook_snapshot import api_gradebook, router as _snapshot_router
from ..gradebook_service import (
    _apply_curve_model,
    _load_curve_events,
    _save_curve_events,
)

router = APIRouter(tags=["gradebook"])
router.include_router(_snapshot_router)
router.include_router(_policy_router)
router.include_router(_extra_time_router)
router.include_router(_curves_router)

__all__ = [
    "router",
    "api_gradebook",
    "get_late_policy",
    "apply_late_policy",
    "list_students",
    "get_extra_time",
    "save_extra_time",
    "get_tier_tags_route",
    "save_tier_tags",
    "curve_assignments",
    "curve_preview",
    "curve_apply",
    "list_curve_events",
    "revert_curve",
    "_assignment",
    "_assignment_submissions",
    "_course_assignments",
    "_course_students",
    "_course_submissions",
    "_load_curve_events",
    "_save_curve_events",
    "_apply_curve_model",
]
