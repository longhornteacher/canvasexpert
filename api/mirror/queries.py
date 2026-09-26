"""Mirror-backed reads implementing the ``gradebook_queries`` interface.

Same five functions, same ``(data, error)`` tuple contract, but served from
the CanvasMirror store instead of live Canvas — a read costs milliseconds and
works offline. Consumers decide *whether* to serve from the mirror via the
freshness helpers here: a collection older than the serve threshold (config
``mirror_serve_max_age_hours``, default 6 h) is not served, and callers fall
back to live Canvas — always labeling results with ``source`` + ``synced_at``
so staleness is visible, never silent (design law #4).
"""
from __future__ import annotations

from . import read_service, store

MIRROR_UNAVAILABLE = "mirror unavailable for this course"


def _serve_max_age_hours() -> float:
    try:
        from api.platform_services import config
        return config.mirror_serve_max_age_hours()
    except Exception:
        return 6.0


def data_freshness(course_id, *, root=None, max_age_hours=None, now=None) -> str:
    """``synced_at`` of the newest successful data pass (full or delta) when
    within the serve threshold, else ''. ISO-Z strings compare lexically."""
    result = read_service.private_submissions(
        course_id, root=root,
        max_age_hours=max_age_hours if max_age_hours is not None else _serve_max_age_hours(),
        now=now,
    )
    return result["last_success_at"] if result["state"] == "current" else ""


# --- the gradebook_queries interface, mirror-backed ---------------------------

def course_students(course_id, *, root=None):
    result = read_service.private_roster(
        course_id, root=root, max_age_hours=_serve_max_age_hours())
    if result["state"] != "current":
        return None, MIRROR_UNAVAILABLE
    return result["records"], None


def course_assignments(course_id, *, root=None):
    result = read_service.private_assignments(course_id, root=root)
    if result["source"] == "none":
        return None, MIRROR_UNAVAILABLE
    return result["records"], None


def course_submissions(course_id, *, root=None):
    """Enumerate submission files, then drop any ``assignment_id`` not
    present in the committed assignment index (1.0beta slice 01b, locked
    design item 1) — deletion is invisible to this read the moment the next
    delta commits a smaller index, without waiting for orphan files to be
    pruned from disk. A missing/corrupt index already returns unavailable
    above (last-good rules unchanged); filtering only ever narrows the
    directory listing, never invents rows for ids the index doesn't have."""
    result = read_service.private_submissions(course_id, root=root)
    if result["source"] == "none":
        return None, MIRROR_UNAVAILABLE
    return result["records"], None


def assignment_submissions(course_id, assignment_id, *, root=None):
    """Same membership filtering as ``course_submissions`` (item 1), applied
    to a single assignment: a missing/corrupt assignment index behaves as
    today (last-good rules — the index simply doesn't gate this read), but a
    present index that no longer lists ``assignment_id`` reports unavailable
    even if an orphan submission file is still on disk."""
    assignments = read_service.private_assignments(course_id, root=root)
    if assignments["source"] != "none" and not any(
        str(entry.get("id")) == str(assignment_id) for entry in assignments["records"]
    ):
        return None, MIRROR_UNAVAILABLE
    # The established single-assignment compatibility read remains useful when
    # a missing/corrupt assignment index cannot prove membership either way.
    # It deliberately does not turn a local read into a Canvas call.
    if assignments["source"] == "none":
        document = store.read_submissions(course_id, assignment_id, root=root)
        if document is None:
            return None, MIRROR_UNAVAILABLE
        return [entry["current"]
                for _, entry in sorted(document["submissions"].items())], None
    submissions = read_service.private_submissions(course_id, root=root)
    if submissions["source"] == "none":
        return None, MIRROR_UNAVAILABLE
    return [entry for entry in submissions["records"]
            if str(entry.get("assignment_id")) == str(assignment_id)], None
