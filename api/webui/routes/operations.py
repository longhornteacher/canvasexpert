"""Operation-ledger console routes — retry, list, status.

All mutation routes require ``require_local_mutation`` (CSRF + loopback +
same-origin). The browser never chooses Canvas paths, endpoints, or method
names — the adapter owns all Canvas interaction.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..local_request_guard import require_local_mutation

from api.operation_ledger import (
    executor, operations, registry,
)
from api.operation_ledger.adapters import PageAdapter, QuizAdapter

# Ensure the adapter is registered (idempotent — __init__ also registers)
if not registry.is_registered(PageAdapter.kind):
    registry.register(PageAdapter())
if not registry.is_registered(QuizAdapter.kind):
    registry.register(QuizAdapter())


router = APIRouter(tags=["operations"])


@router.get("/api/operations")
def list_operations_route():
    """PII-minimized list of all operations. No course IDs or payload."""
    return {"ok": True, "operations": operations.list_operations_pii_minimized()}


@router.get("/api/operations/{operation_id}/status")
def operation_status_route(operation_id: str):
    """PII-minimized status snapshot for one operation.

    Returns current target/step states from the durable ledger.
    No course IDs, no returned object IDs, no URLs, no diagnostics, no payloads.
    """
    op = operations.get_operation(operation_id)
    if op is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"operation {operation_id} not found"},
        )

    targets_out = []
    for target in (op.get("targets") or []):
        step_out = []
        for step in (target.get("steps") or []):
            step_out.append({
                "step_key": step.get("step_key"),
                "state": step.get("state"),
            })
        targets_out.append({
            "target_key": target.get("target_key"),
            "state": target.get("state"),
            "steps": step_out,
        })

    return {
        "ok": True,
        "operation_id": op.get("operation_id"),
        "kind": op.get("kind"),
        "status": op.get("status"),
        "targets": targets_out,
    }


@router.post("/api/operations/{operation_id}/retry")
def retry_operation(operation_id: str, request: Request):
    """Retry only unresolved targets in an operation."""
    require_local_mutation(request)

    try:
        result = executor.retry_operation(operation_id)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(exc)},
        )

    return result
