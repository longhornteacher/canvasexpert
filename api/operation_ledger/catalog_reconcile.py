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


def reconcile_catalog_after_apply(
    kind: str,
    course_id: str,
    *,
    payload: dict | None = None,
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
