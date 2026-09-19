"""Typed, runtime-only reads over existing CanvasMirror projections.

This module deliberately has no Canvas client import.  It adapts validated local documents
into a stable envelope while leaving fallback, refresh, and command authority to callers.
"""
from __future__ import annotations

import copy
from collections.abc import Callable

from api import course_catalog

from . import store


READ_SERVICE_VERSION = 1
GROUPS_MAX_AGE_HOURS = 24.0

LOCAL_DISPLAY = "local_display"
OFFLINE = "offline"
REFRESH_IF_STALE = "refresh_if_stale"
FOCUSED_CURRENT = "focused_current"
AUTHORITATIVE_LIVE = "authoritative_live"
EXPLICIT_DIAGNOSTIC = "explicit_diagnostic"

PRIVATE_ROSTER = "private.roster"
PRIVATE_GROUPS = "private.groups"
PRIVATE_ASSIGNMENTS = "private.assignments"
PRIVATE_SUBMISSIONS = "private.submissions"
PRIVATE_SUBMISSION_COMMENTS = "private.submission_comments"
CATALOG_ASSIGNMENTS = "catalog.assignments"
CATALOG_MODULES = "catalog.modules"
CATALOG_ASSIGNMENT_GROUPS = "catalog.assignment_groups"
CATALOG_PAGES = "catalog.pages"

_LOCAL_INTENTS = {LOCAL_DISPLAY, OFFLINE}


def _state_with_age(state: str, last_success_at: str, *, max_age_hours, now) -> str:
    if state != "current" or max_age_hours is None:
        return state
    age = store.age_hours(last_success_at, now)
    return "stale" if age is None or age >= max_age_hours else state


def _envelope(
    course_id,
    scope: str,
    *,
    state: str = "unavailable",
    source: str = "none",
    last_success_at: str = "",
    last_attempt_at: str = "",
    error_code: str = "",
    records=None,
    generation_version: str = "v1",
    max_age_hours=None,
    now=None,
    mirror_revision=0,
    snapshot_id="",
    refresh_state="",
) -> dict:
    state = _state_with_age(
        str(state or "unavailable"), str(last_success_at or ""),
        max_age_hours=max_age_hours, now=now,
    )
    last_success_at = str(last_success_at or "")
    last_attempt_at = str(last_attempt_at or "")
    return {
        "course_id": str(course_id),
        "scope": scope,
        "state": state,
        "capability": "supported" if last_success_at else "unknown",
        "source": source,
        "last_success_at": last_success_at,
        "last_attempt_at": last_attempt_at,
        "canvas_observed_at": "",
        "retry_after": "",
        "generation": f"{scope}:{generation_version}:{last_success_at}",
        "mirror_revision": int(mirror_revision or 0),
        "snapshot_id": str(snapshot_id or ""),
        "refresh_state": str(refresh_state or ""),
        "error_code": str(error_code or ""),
        "records": copy.deepcopy(records or []),
    }


def _private_document(course_id, scope: str, reader: Callable, records: Callable, *,
                      root=None, max_age_hours=None, now=None) -> dict:
    document = reader(course_id, root=root)
    if document is None:
        return _envelope(course_id, scope, max_age_hours=max_age_hours, now=now)
    refresh = store.read_refresh(course_id, root=root)
    refresh_active = refresh.get("revision", 0) > 0 or refresh.get("state") == "syncing"
    state = document.get("state", "unavailable")
    if refresh_active and refresh.get("state") != "synced":
        state = refresh.get("state") or "failed"
    return _envelope(
        course_id,
        scope,
        state=state,
        source="mirror",
        last_success_at=document.get("last_success_at", ""),
        last_attempt_at=document.get("last_attempt_at", ""),
        error_code=document.get("error_code", ""),
        records=records(document),
        generation_version=f"v{document.get('schema_version', 1)}",
        mirror_revision=refresh.get("revision", 0) if refresh_active else 0,
        snapshot_id=refresh.get("snapshot_id", "") if refresh_active else "",
        refresh_state=refresh.get("state", "") if refresh_active else "",
        max_age_hours=max_age_hours,
        now=now,
    )


def private_roster(course_id, *, root=None, max_age_hours=None, now=None) -> dict:
    return _private_document(
        course_id, PRIVATE_ROSTER, store.read_roster,
        lambda document: list(document["students"].values()), root=root,
        max_age_hours=max_age_hours, now=now,
    )


def private_groups(course_id, *, root=None, max_age_hours=None, now=None) -> dict:
    return _private_document(
        course_id, PRIVATE_GROUPS, store.read_groups, store.groups_for_roster, root=root,
        max_age_hours=max_age_hours, now=now,
    )


def _sync_freshness(course_id, *, root=None) -> dict:
    """Freshness (state/last_success_at/last_attempt_at/error_code) derived
    from the full/delta sync-pass envelopes (``store.read_sync``), not from
    any particular mirror file's own envelope.

    ``full_pass``/``delta_pass`` (``api/mirror/sync.py``) early-return with a
    failed pass record whenever the assignment fetch itself errors (the
    ``_assignment_receipt_error`` guard), so a successful full/delta pass is
    an exact proxy for "assignments were confirmed this tick" — which is why
    ``private_assignments`` below can source its freshness from here instead
    of the assignments file's own envelope (whose timestamps get re-stamped,
    and the file rewritten, on every tick otherwise). ``private_submissions``
    has done this since it was introduced; this factors that shared logic out
    so both scopes stay identical.
    """
    passes = store.read_sync(course_id, root=root).get("passes", {})
    pass_values = [passes.get(name, {}) for name in ("full", "delta")]
    last_success_at = max((str(value.get("last_success_at") or "") for value in pass_values), default="")
    last_attempt_at = max((str(value.get("last_attempt_at") or "") for value in pass_values), default="")
    newest = max(pass_values, key=lambda value: str(value.get("last_attempt_at") or ""), default={})
    state = "current" if last_success_at else str(newest.get("state") or "unavailable")
    refresh = store.read_refresh(course_id, root=root)
    # Direct legacy pass callers predate the lifecycle sidecar.  Preserve
    # their pass-level behavior while exposing lifecycle identity whenever a
    # refresh has actually been recorded.
    lifecycle_active = refresh.get("revision", 0) > 0 or refresh.get("state") == "syncing"
    effective_state = state
    if lifecycle_active and refresh.get("state") != "synced":
        effective_state = refresh.get("state") or "failed"
    return {
        "state": effective_state,
        "last_success_at": last_success_at,
        "last_attempt_at": last_attempt_at,
        "error_code": str((refresh.get("error_code") if lifecycle_active else newest.get("error_code")) or ""),
        "mirror_revision": refresh.get("revision", 0) if lifecycle_active else 0,
        "snapshot_id": refresh.get("snapshot_id", "") if lifecycle_active else "",
        "refresh_state": refresh.get("state", "") if lifecycle_active else "",
    }


def private_assignments(course_id, *, root=None, max_age_hours=None, now=None) -> dict:
    """Records come from the assignments mirror file; freshness comes from
    the full/delta sync-pass envelopes (see ``_sync_freshness``), not from
    the assignments file's own envelope — so a no-op sync tick that leaves
    the file unwritten does not report stale records."""
    document = store.read_assignments(course_id, root=root)
    if document is None:
        return _envelope(course_id, PRIVATE_ASSIGNMENTS, max_age_hours=max_age_hours, now=now)
    freshness = _sync_freshness(course_id, root=root)
    return _envelope(
        course_id, PRIVATE_ASSIGNMENTS, state=freshness["state"], source="mirror",
        last_success_at=freshness["last_success_at"], last_attempt_at=freshness["last_attempt_at"],
        error_code=freshness["error_code"],
        mirror_revision=freshness["mirror_revision"], snapshot_id=freshness["snapshot_id"],
        refresh_state=freshness["refresh_state"],
        records=list(document["assignments"].values()),
        generation_version=f"v{document.get('schema_version', 1)}",
        max_age_hours=max_age_hours, now=now,
    )


def private_submissions(course_id, *, root=None, max_age_hours=None, now=None) -> dict:
    assignments = store.read_assignments(course_id, root=root)
    if assignments is None:
        return _envelope(course_id, PRIVATE_SUBMISSIONS,
                         max_age_hours=max_age_hours, now=now)
    valid_ids = set(assignments["assignments"])
    records = []
    for assignment_id in store.list_submission_assignment_ids(course_id, root=root):
        if assignment_id not in valid_ids:
            continue
        document = store.read_submissions(course_id, assignment_id, root=root)
        if document is not None:
            records.extend(entry["current"] for _, entry in sorted(document["submissions"].items()))

    freshness = _sync_freshness(course_id, root=root)
    return _envelope(
        course_id, PRIVATE_SUBMISSIONS, state=freshness["state"], source="mirror",
        last_success_at=freshness["last_success_at"], last_attempt_at=freshness["last_attempt_at"],
        error_code=freshness["error_code"], records=records,
        mirror_revision=freshness["mirror_revision"], snapshot_id=freshness["snapshot_id"],
        refresh_state=freshness["refresh_state"],
        generation_version="v1", max_age_hours=max_age_hours, now=now,
    )


def private_submission_comments(course_id, *, root=None, max_age_hours=None, now=None) -> dict:
    """Reuse normalized submission records, but derive freshness only from the
    comment sidecar (never from the full/delta pass envelopes and never from Canvas)."""
    submissions = private_submissions(course_id, root=root, max_age_hours=None, now=now)
    sidecar = store.read_submission_comments_state(course_id, root=root)
    return _envelope(
        course_id, PRIVATE_SUBMISSION_COMMENTS,
        state=sidecar.get("state", "unavailable"), source="mirror",
        last_success_at=sidecar.get("last_success_at", ""),
        last_attempt_at=sidecar.get("last_attempt_at", ""),
        error_code=sidecar.get("error_code", ""),
        records=submissions["records"],
        generation_version=f"v{sidecar.get('schema_version', 1)}",
        max_age_hours=max_age_hours, now=now,
    )


def _catalog_scope(course_id, scope: str, scope_key: str, *, catalog_reader=None, root=None,
                   max_age_hours=None, now=None) -> dict:
    if catalog_reader is None:
        read_result = (course_catalog.read_catalog(course_id)
                       if root is None else course_catalog.read_catalog(course_id, root=root))
    else:
        read_result = catalog_reader(course_id)
    document = read_result.get("catalog") if isinstance(read_result, dict) else None
    if not isinstance(document, dict):
        return _envelope(course_id, scope, max_age_hours=max_age_hours, now=now)
    collection = document.get(scope_key)
    if not isinstance(collection, dict):
        return _envelope(course_id, scope, source="catalog",
                         generation_version=f"v{document.get('version', 1)}",
                         max_age_hours=max_age_hours, now=now)
    raw_records = collection.get("records")
    records = list(raw_records.values()) if isinstance(raw_records, dict) else raw_records
    return _envelope(
        course_id, scope, state=collection.get("state", "unavailable"), source="catalog",
        last_success_at=collection.get("last_success_at", ""),
        last_attempt_at=collection.get("last_attempt_at", ""),
        error_code=collection.get("error_code", ""), records=records if isinstance(records, list) else [],
        generation_version=f"v{document.get('version', 1)}", max_age_hours=max_age_hours, now=now,
    )


def catalog_assignments(course_id, *, catalog_reader=None, root=None, max_age_hours=None, now=None) -> dict:
    return _catalog_scope(course_id, CATALOG_ASSIGNMENTS, "assignments",
                          catalog_reader=catalog_reader, root=root,
                          max_age_hours=max_age_hours, now=now)


def catalog_modules(course_id, *, catalog_reader=None, root=None, max_age_hours=None, now=None) -> dict:
    return _catalog_scope(course_id, CATALOG_MODULES, "modules",
                          catalog_reader=catalog_reader, root=root,
                          max_age_hours=max_age_hours, now=now)


def catalog_assignment_groups(course_id, *, catalog_reader=None, root=None, max_age_hours=None, now=None) -> dict:
    return _catalog_scope(course_id, CATALOG_ASSIGNMENT_GROUPS, "assignment_groups",
                          catalog_reader=catalog_reader, root=root,
                          max_age_hours=max_age_hours, now=now)


def catalog_pages(course_id, *, catalog_reader=None, root=None, max_age_hours=None, now=None) -> dict:
    """Typed disk-only published-page projection from the v3 Catalog."""
    return _catalog_scope(course_id, CATALOG_PAGES, "pages",
                          catalog_reader=catalog_reader, root=root,
                          max_age_hours=max_age_hours, now=now)


def read(scope: str, course_id, *, intent: str = LOCAL_DISPLAY, root=None,
         max_age_hours=None, now=None, catalog_reader=None) -> dict:
    """Read one typed local scope; unsupported intents never fall through to Canvas."""
    if intent not in _LOCAL_INTENTS:
        raise ValueError(f"read intent is not local: {intent}")
    readers = {
        PRIVATE_ROSTER: private_roster,
        PRIVATE_GROUPS: private_groups,
        PRIVATE_ASSIGNMENTS: private_assignments,
        PRIVATE_SUBMISSIONS: private_submissions,
        PRIVATE_SUBMISSION_COMMENTS: private_submission_comments,
    }
    if scope in readers:
        return readers[scope](course_id, root=root, max_age_hours=max_age_hours, now=now)
    catalog_readers = {
        CATALOG_ASSIGNMENTS: catalog_assignments,
        CATALOG_MODULES: catalog_modules,
        CATALOG_ASSIGNMENT_GROUPS: catalog_assignment_groups,
        CATALOG_PAGES: catalog_pages,
    }
    if scope in catalog_readers:
        return catalog_readers[scope](course_id, catalog_reader=catalog_reader,
                                      root=root, max_age_hours=max_age_hours, now=now)
    raise ValueError(f"unknown canvas read scope: {scope}")
