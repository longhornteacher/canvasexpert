"""Central catalog-reconciliation hook for the operation ledger.

Called once per successfully-applied operation from both ledger success
seams — the normal apply path (``executor._execute_target``) and the
crash-recovery apply path (``recovery._apply_recovered``) — to mark every
CanvasMirror Course Catalog scope a given operation *kind* can affect
``stale`` via ``course_catalog.invalidate_scope``. Canvas is truth and the
catalog projection is disposable: a whole-scope stale-mark is the honest,
minimal fix; the next catalog read or refresh repairs it wholesale.

The kind -> scopes mapping is a conservative union (over-invalidating a
possibly-unaffected scope is accepted); a kind absent from the map
invalidates nothing. Two kinds are payload-sensitive:

- ``content.page`` always creates or verifies a Canvas page, so it always
  marks ``catalog.pages`` stale, but a bare page (no ``module_name``) never
  touches Canvas module structure at all (see ``PageAdapter.execute``), so
  it must not mark ``catalog.modules`` stale, while a page attached to a
  module does.
See ``docs/reference/mutation-reconciliation-map.md`` family 2.
"""
from api import course_catalog

# Conservative kind -> catalog scopes union for kinds whose affected scopes
# do not depend on payload contents. Any kind not present here and not
# payload-sensitive below invalidates nothing (e.g. dead/unregistered kinds).
_KIND_TO_CATALOG_SCOPES: dict[str, frozenset[str]] = {
    "content.assignment": frozenset({"assignments", "modules"}),
    "content.assignment_update": frozenset({"assignments"}),
    "content.quiz": frozenset({"assignments", "modules"}),
    "content.quick_assignment": frozenset({"assignments"}),
    "gradebook.sis_bridge": frozenset({"assignments"}),
}

_PAGE_KIND = "content.page"


def _scopes_for(kind: str, payload: dict | None) -> frozenset[str]:
    payload = payload or {}
    if kind == _PAGE_KIND:
        scopes = {"pages"}
        if payload.get("module_name"):
            scopes.add("modules")
        return frozenset(scopes)
    return _KIND_TO_CATALOG_SCOPES.get(kind, frozenset())


def _created_objects(kind: str, payload: dict | None, result: dict | None,
                     target: dict | None) -> list[dict]:
    payload = payload if isinstance(payload, dict) else {}
    result = result if isinstance(result, dict) else {}
    target = target if isinstance(target, dict) else {}
    steps = result.get("steps") or target.get("steps") or []
    by_key = {str(step.get("step_key") or ""): step for step in steps
              if isinstance(step, dict)}
    objects = []

    def add(object_id, object_kind, title):
        if object_id not in (None, ""):
            objects.append({"id": str(object_id), "kind": object_kind,
                            "title": str(title or object_id)})

    if kind == "content.page":
        page_step = by_key.get("create_page") or {}
        add(result.get("returned_object_id") or target.get("returned_object_id")
            or page_step.get("returned_object_id"), "page", payload.get("title"))
    elif kind == "content.assignment":
        tiers = payload.get("tiers") or []
        if tiers:
            for index, tier in enumerate(tiers):
                step = by_key.get(f"create_tier_assignment:{index}") or {}
                if step.get("state") in {"applied", "skipped"}:
                    add(step.get("returned_object_id"), "assignment", tier.get("title"))
            bridge = by_key.get("create_bridge") or {}
            if bridge.get("state") in {"applied", "skipped"}:
                add(bridge.get("returned_object_id"), "assignment",
                    payload.get("base_title"))
        else:
            assignment_step = by_key.get("create_assignment") or {}
            add(result.get("returned_object_id") or target.get("returned_object_id")
                or assignment_step.get("returned_object_id"), "assignment",
                payload.get("name"))
    elif kind == "content.quick_assignment":
        assignment_step = by_key.get("create_assignment") or {}
        add(result.get("returned_object_id") or target.get("returned_object_id")
            or assignment_step.get("returned_object_id"), "assignment",
            payload.get("name"))
    elif kind == "content.quiz":
        if payload.get("mode") == "differentiated":
            for index, variant in enumerate(payload.get("variants") or []):
                step = by_key.get(f"create_quiz:{index}") or {}
                if step.get("state") in {"applied", "skipped"}:
                    add(step.get("returned_object_id"), "quiz",
                        (variant.get("plan") or {}).get("title"))
            bridge = by_key.get("create_bridge") or {}
            if bridge.get("state") in {"applied", "skipped"}:
                add(bridge.get("returned_object_id"), "assignment",
                    payload.get("base_title"))
        else:
            plan = payload.get("plan") or {}
            quiz_step = by_key.get("create_quiz:0") or {}
            add(result.get("returned_object_id") or target.get("returned_object_id")
                or quiz_step.get("returned_object_id"), "quiz", plan.get("title"))
    return objects


def reconcile_catalog_after_apply(
    kind: str,
    course_id: str,
    *,
    payload: dict | None = None,
    result: dict | None = None,
    target: dict | None = None,
    operation_id: str = "",
    root=None,
    attempted_at: str | None = None,
) -> None:
    """Invalidate every catalog scope a successfully-applied ``kind`` can affect.

    Safe to call for any kind (an unmapped kind is a no-op) and for a course
    with no catalog document yet (``invalidate_scope`` no-ops per scope).
    Call exactly once per successfully-applied operation, never per adapter
    step and never for a failed/aborted operation.
    """
    for scope_key in sorted(_scopes_for(kind, payload)):
        course_catalog.invalidate_scope(
            course_id, scope_key, root=root, attempted_at=attempted_at,
        )
    for created in _created_objects(kind, payload, result, target):
        course_catalog.record_pending_write(
            course_id, created["kind"], created["id"], created["title"],
            operation_id, created_at=attempted_at, root=root,
        )
