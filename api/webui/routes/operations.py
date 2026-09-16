"""Operation-ledger routes — prepare, review, apply, retry, list.

All mutation routes require ``require_local_mutation`` (CSRF + loopback +
same-origin). The browser never chooses Canvas paths, endpoints, or method
names — the adapter owns all Canvas interaction.
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..local_request_guard import require_local_mutation

from api.operation_ledger import (
    batches, executor, models, operations, registry,
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


@router.post("/api/operations/{kind}/prepare")
async def prepare_operation(kind: str, request: Request):
    """Prepare an operation for later review and apply.

    Body: kind-specific prepare request (e.g. ``{path, published, module_name?}``
    for ``content.page``).
    """
    require_local_mutation(request)

    try:
        adapter = registry.get_adapter(kind)
    except ValueError:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"unknown operation kind: {kind}"},
        )

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "invalid JSON body"},
        )

    if not isinstance(body, dict):
        return _bad_request("request body must be an object")
    payload_in = body.get("payload")
    targets_in = body.get("targets")
    if not isinstance(payload_in, dict):
        return _bad_request("payload must be an object")
    target_error = _validate_target_envelope(targets_in)
    if target_error:
        return _bad_request(target_error)

    # Build payload (validates source)
    try:
        payload = adapter.build_payload(payload_in)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(exc)},
        )

    source_digest = adapter.source_digest(payload)

    try:
        targets = adapter.verify_targets(payload, targets_in)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(exc)},
        )

    # Build target records with baselines
    target_records = []
    for t in targets:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"],
            idempotency_key=t["idempotency_key"],
            course_id=t["course_id"],
            baseline=baseline,
        ))

    operation_id = models.new_operation_id()
    source_ref = {"type": "workspace_relative", "value": payload.get("source_path", "")}
    op = models.new_operation(
        operation_id=operation_id,
        kind=kind,
        source_ref=source_ref,
        source_digest=source_digest,
        normalized_payload=payload,
        targets=target_records,
    )

    operations.create_operation(op)

    # Build review summary for the response
    review_summary = _build_review_summary(adapter, payload, target_records)

    return {
        "ok": True,
        "operation_id": operation_id,
        "review_summary": review_summary,
    }


@router.post("/api/operation-batches/review")
async def review_batch(request: Request):
    """Freeze a review snapshot for a set of operations.

    Body: ``{operation_ids: ["op-...", ...]}``.
    Returns ``{ok, batch_id, review_digest, frozen_reviews}``.
    """
    require_local_mutation(request)

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "invalid JSON body"},
        )

    op_ids = body.get("operation_ids") if isinstance(body, dict) else None
    if not isinstance(op_ids, list) or not op_ids:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "operation_ids must be a non-empty list"},
        )

    frozen_by_op = {}
    for op_id in op_ids:
        op = operations.get_operation(op_id)
        if op is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "error": f"operation {op_id} not found"},
            )
        adapter = registry.get_adapter(op["kind"])
        payload = op["normalized_payload"]
        frozen = []
        for target in op.get("targets", []):
            baseline = target.get("baseline", {})
            frozen.append(adapter.freeze_review(payload, target, baseline))
        frozen_by_op[op_id] = frozen

    batch = batches.freeze_batch(op_ids, frozen_by_op)

    # Persist the batch on each operation
    for op_id in op_ids:
        operations.set_operation_review(op_id, batch)

    return {
        "ok": True,
        "batch_id": batch["batch_id"],
        "review_digest": batch["review_digest"],
        "frozen_reviews": batch["frozen_reviews"],
    }


@router.post("/api/operation-batches/{batch_id}/apply")
async def apply_batch(batch_id: str, request: Request):
    """Apply a reviewed batch to Canvas.

    Body: ``{review_digest: "..."}``.
    """
    require_local_mutation(request)

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "invalid JSON body"},
        )

    review_digest = body.get("review_digest") if isinstance(body, dict) else None
    if not review_digest:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "review_digest is required"},
        )

    # Find the operation that has this batch_id
    all_ops = operations.list_operations()
    target_op = None
    for op in all_ops:
        review = op.get("review")
        if review and review.get("batch_id") == batch_id:
            target_op = op
            break

    if target_op is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": f"batch {batch_id} not found"},
        )

    try:
        result = executor.apply_operation(
            target_op["operation_id"], batch_id, review_digest)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(exc)},
        )

    return result


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


# ── Helpers ─────────────────────────────────────────────────────────────

def _bad_request(error: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "error": error})


def _validate_target_envelope(targets) -> str | None:
    if not isinstance(targets, list) or not targets:
        return "targets must be a non-empty list"
    seen = set()
    for target in targets:
        if not isinstance(target, dict):
            return "each target must be an object"
        course_id = target.get("course_id")
        if course_id is None or not str(course_id).strip():
            return "target course_id must be non-blank"
        normalized = str(course_id).strip()
        if normalized in seen:
            return "target course_id values must be unique"
        seen.add(normalized)
        target.clear()
        target["course_id"] = normalized
    return None

def _build_review_summary(adapter, payload: dict, targets: list[dict]) -> dict:
    """Build a PII-minimized review summary for the prepare response."""
    frozen = []
    for target in targets:
        baseline = target.get("baseline", {})
        frozen.append(adapter.freeze_review(payload, target, baseline))

    return {
        "kind": adapter.kind,
        "target_count": len(targets),
        "frozen_reviews": frozen,
        "reversal_supported": False,
    }
