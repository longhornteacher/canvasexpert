"""Crash-safe apply/retry execution for the operation ledger."""

import copy
import time

from api import operational_log

from . import claims, models, operations, registry, storage
from .catalog_reconcile import reconcile_catalog_after_apply
from .receipts import create_receipt, new_receipt


class LostClaimError(RuntimeError):
    """Raised when a recovered worker attempts to write after fencing."""


class ExecutionContext:
    """Fenced, durable step checkpointing owned by one target execution."""

    def __init__(self, *, operation_id: str, target_key: str, claim: dict):
        self.operation_id = operation_id
        self.target_key = target_key
        self.claim = copy.deepcopy(claim)
        self._outbound_started = False

    def _verify_and_mutate(self, mutator):
        def transaction(_operations_doc, _claims_doc):
            if not claims.is_current_claim(
                self.claim["claim_id"], self.target_key, self.operation_id
            ):
                raise LostClaimError("execution claim is no longer current")
            operation = next(
                (item for item in _operations_doc["operations"]
                 if item.get("operation_id") == self.operation_id), None)
            if operation is None:
                raise LostClaimError("operation no longer exists")
            target = next(
                (item for item in operation.get("targets", [])
                 if item.get("target_key") == self.target_key), None)
            if target is None:
                raise LostClaimError("target no longer exists")
            return mutator(target)

        return storage.modify_ledger(transaction)

    def before_send(self, step_key: str, payload_digest: str) -> dict:
        """Write the outbound marker before an individual Canvas mutation."""
        outbound_started_at = models.now_iso()

        def mutate(target):
            existing = next(
                (step for step in target.get("steps", [])
                 if step.get("step_key") == step_key), None)
            step = copy.deepcopy(existing) if existing else models.new_step(step_key)
            step["step_key"] = step_key
            step["state"] = "claimed"
            step["attempt_id"] = self.claim["attempt_id"]
            step["payload_digest"] = payload_digest
            step["outbound_started_at"] = outbound_started_at
            step["error_code"] = None
            step["private_diagnostic"] = None
            step["updated_at"] = outbound_started_at
            _replace_step(target, step)
            target["state"] = "claimed"
            target["updated_at"] = outbound_started_at
            return copy.deepcopy(step)

        self._outbound_started = True
        return self._verify_and_mutate(mutate)

    def checkpoint_step(
        self,
        step: dict,
        *,
        returned_object_id: str | None = None,
        returned_object_url: str | None = None,
    ) -> dict:
        """Persist the response for a step before another Canvas request."""
        candidate = copy.deepcopy(step)
        step_key = candidate["step_key"]

        def mutate(target):
            previous = next(
                (item for item in target.get("steps", [])
                 if item.get("step_key") == step_key), None)
            if not candidate.get("outbound_started_at") and previous:
                candidate["outbound_started_at"] = previous.get("outbound_started_at")
            candidate["attempt_id"] = self.claim["attempt_id"]
            if returned_object_id is not None:
                candidate["returned_object_id"] = returned_object_id
            if returned_object_url is not None:
                candidate["returned_object_url"] = returned_object_url
            candidate["updated_at"] = models.now_iso()
            _replace_step(target, candidate)

            if step_key == "create_page" and returned_object_id is not None:
                target["returned_object_id"] = returned_object_id
                if returned_object_url is not None:
                    target["returned_object_url"] = returned_object_url
            if candidate.get("state") in ("sent_unknown", "failed", "blocked"):
                target["state"] = candidate["state"]
            target["updated_at"] = candidate["updated_at"]
            return copy.deepcopy(candidate)

        result = self._verify_and_mutate(mutate)
        self._outbound_started = self._outbound_started or bool(
            result.get("outbound_started_at"))
        return result

    def has_outbound_started(self) -> bool:
        return self._outbound_started


def _replace_step(target: dict, step: dict) -> None:
    steps = target.setdefault("steps", [])
    for index, existing in enumerate(steps):
        if existing.get("step_key") == step.get("step_key"):
            steps[index] = step
            return
    steps.append(step)


def apply_operation(operation_id: str, batch_id: str, review_digest: str) -> dict:
    started = time.monotonic()
    op = None
    try:
        op = operations.get_operation(operation_id)
        if op is None:
            raise ValueError(f"operation {operation_id} not found")
        from . import batches
        if not batches.validate_apply(op, batch_id, review_digest):
            raise ValueError("review batch or digest does not match stored review")
        adapter = registry.get_adapter(op["kind"])
        old_status = op.get("status", "reviewed")
        if not models.validate_operation_status_transition(old_status, "applying"):
            raise ValueError(f"operation is in status '{old_status}', cannot apply")
        operations.set_operation_status(operation_id, "applying")

        target_results = []
        for target in op.get("targets", []):
            target_results.append(_execute_target(adapter, op, op["normalized_payload"], target))
        result = _finish_operation(operation_id, target_results)
    except Exception as exc:
        _emit_apply("failed", op, started, error_class=type(exc))
        raise
    _emit_apply(_apply_outcome(result["status"]), op, started)
    return result


def retry_operation(operation_id: str) -> dict:
    started = time.monotonic()
    op = None
    try:
        op = operations.get_operation(operation_id)
        if op is None:
            raise ValueError(f"operation {operation_id} not found")
        adapter = registry.get_adapter(op["kind"])
        old_status = op.get("status", "attention")
        if not models.validate_operation_status_transition(old_status, "applying"):
            raise ValueError(f"operation is in status '{old_status}', cannot retry")
        operations.set_operation_status(operation_id, "applying")

        unresolved_keys = {t["target_key"] for t in adapter.retry_selector(op)}
        target_results = []
        for target in op.get("targets", []):
            if target["target_key"] not in unresolved_keys:
                target_results.append({
                    "target_key": target["target_key"],
                    "state": target.get("state"),
                    "returned_object_id": target.get("returned_object_id"),
                    "returned_object_url": target.get("returned_object_url"),
                    "error_code": target.get("error_code"),
                    "skipped_retry": True,
                })
            else:
                target_results.append(_execute_target(
                    adapter, op, op["normalized_payload"], target))
        result = _finish_operation(operation_id, target_results)
    except Exception as exc:
        _emit_apply("failed", op, started, error_class=type(exc))
        raise
    _emit_apply(_apply_outcome(result["status"]), op, started)
    return result


def _apply_outcome(status: str) -> str:
    if status == "applied":
        return "ok"
    if status in {"partial", "attention"}:
        return "blocked"
    return "failed"


def _emit_apply(outcome: str, operation: dict | None, started: float,
                *, error_class=None) -> None:
    operational_log.emit(
        "operation_ledger.apply", outcome,
        duration_ms=max(0, int(round((time.monotonic() - started) * 1000))),
        count=len((operation or {}).get("targets") or []),
        error_class=error_class,
    )


def _finish_operation(operation_id: str, target_results: list[dict]) -> dict:
    op = operations.get_operation(operation_id)
    final_status = models.compute_operation_status(op.get("targets", []))
    variants = _assignment_variants(op)
    operations.set_operation_status(operation_id, final_status)
    receipt = new_receipt(
        subject_type="operation",
        subject_id=operation_id,
        kind=op["kind"],
        status=_receipt_status(final_status),
        targets=_receipt_targets(op.get("targets", [])),
        **({"variants": variants} if variants else {}),
    )
    create_receipt(receipt)
    return {
        "ok": final_status == "applied",
        "operation_id": operation_id,
        "status": final_status,
        "target_results": _project_target_results(target_results),
    }


def _assignment_variants(operation: dict) -> list[dict]:
    """Expose exact differentiated-family source results in the receipt."""
    if operation.get("kind") != "content.assignment":
        return []
    tiers = (operation.get("normalized_payload") or {}).get("tiers") or []
    output = []
    for index, tier in enumerate(tiers):
        step_key = f"create_tier_assignment:{index}"
        for target in operation.get("targets") or []:
            step = next((row for row in target.get("steps") or []
                         if row.get("step_key") == step_key
                         and row.get("state") in ("applied", "skipped")
                         and row.get("returned_object_id")), None)
            if step:
                output.append({
                    "label": tier.get("label"),
                    "assignment_id": step.get("returned_object_id"),
                    "name": tier.get("title"),
                    "html_url": step.get("returned_object_url"),
                })
                break
    return output


def _execute_target(adapter, operation: dict, payload: dict, target: dict) -> dict:
    target_key = target["target_key"]
    stored_baseline = target.get("baseline", {})
    try:
        fresh_baseline = adapter.capture_baseline(payload, target)
        if adapter.check_drift(payload, target, stored_baseline):
            drift_fields = _diff_field_names(stored_baseline, fresh_baseline)
            next_step = _drift_next(target)
            _update_target_state(operation["operation_id"], target_key, "blocked",
                                 error_code="drift_detected",
                                 drift_fields=drift_fields, next_step=next_step)
            return {"target_key": target_key, "state": "blocked",
                    "error_code": "drift_detected",
                    "drift_fields": drift_fields, "next": next_step}
    except Exception as exc:
        _update_target_state(operation["operation_id"], target_key, "failed",
                             error_code="adapter_exception",
                             private_diagnostic=type(exc).__name__)
        return {"target_key": target_key, "state": "failed",
                "error_code": "adapter_exception"}

    payload_digest = models.sha256_dict(payload)
    try:
        claim = claims.acquire_claim(
            target_key=target_key,
            operation_id=operation["operation_id"],
            payload_digest=payload_digest,
        )
    except claims.ClaimConflictError:
        return {"target_key": target_key, "state": "blocked",
                "error_code": "claim_conflict"}

    context = ExecutionContext(
        operation_id=operation["operation_id"], target_key=target_key, claim=claim)
    try:
        _update_target_claimed(operation["operation_id"], target_key, claim, fresh_baseline)
        try:
            result = adapter.execute(payload, target, fresh_baseline, claim, context)
        except LostClaimError:
            return {"target_key": target_key, "state": "sent_unknown",
                    "error_code": "lost_claim"}
        except Exception as exc:
            after_send = context.has_outbound_started()
            result = {
                "state": "sent_unknown" if after_send else "failed",
                "error_code": (
                    "adapter_exception_after_send" if after_send
                    else "adapter_exception"),
                "private_diagnostic": type(exc).__name__,
            }
        try:
            _update_target_result(operation["operation_id"], target_key, result, claim)
        except LostClaimError:
            return {"target_key": target_key, "state": "sent_unknown",
                    "error_code": "lost_claim"}
        if result.get("state") == "applied":
            reconcile_catalog_after_apply(
                operation["kind"], target["course_id"], payload=payload,
                result=result, target=target,
                operation_id=operation["operation_id"],
            )
        return {
            "target_key": target_key,
            "state": result.get("state", "failed"),
            "returned_object_id": result.get("returned_object_id"),
            "returned_object_url": result.get("returned_object_url"),
            "error_code": result.get("error_code"),
            "private_diagnostic": result.get("private_diagnostic"),
            "failed_items": result.get("failed_items"),
            "cleanup_required": result.get("cleanup_required"),
            "rollback_state": result.get("rollback_state"),
            "rollback_error_code": result.get("rollback_error_code"),
            "canvas_message": result.get("canvas_message"),
        }
    finally:
        claims.release_claim(claim["claim_id"])


def _update_target_state(operation_id: str, target_key: str, state: str,
                         error_code: str | None = None,
                         private_diagnostic: str | None = None,
                         drift_fields: list[str] | None = None,
                         next_step: str | None = None) -> None:
    def mutate(target):
        target["state"] = state
        if error_code:
            target["error_code"] = error_code
        if private_diagnostic:
            target["private_diagnostic"] = private_diagnostic
        if drift_fields is not None:
            target["drift_fields"] = drift_fields
        if next_step is not None:
            target["next"] = next_step
        target["updated_at"] = models.now_iso()
        return target
    operations.update_target(operation_id, target_key, mutate)


def _diff_field_names(old: dict, new: dict, prefix: str = "") -> list[str]:
    """Field names (never values) that differ between two baseline dicts (AC4).

    Generic and adapter-agnostic: it walks the same two dicts every
    ``check_drift`` implementation already compares (the stored baseline and
    a freshly captured one), so naming drifted fields needs no per-adapter
    change.
    """
    names: set[str] = set()
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    for key in sorted(set(old) | set(new)):
        old_value = old.get(key)
        new_value = new.get(key)
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            names |= set(_diff_field_names(old_value, new_value, path))
        elif old_value != new_value:
            names.add(path)
    return sorted(names)


def _drift_next(target: dict) -> str:
    """AC4's named next step, and the fix for Issue #11's endless drift loop.

    A target that already carries ``drift_detected`` from a previous attempt
    is the exact loop the brief calls out (tier 0 created, then drift
    forever): the second time around, the named next step is
    ``abandon_operation`` instead of looping back through preview. Otherwise
    a target with any recorded progress should be resumed, and a target with
    none should simply be re-previewed.
    """
    if target.get("error_code") == "drift_detected":
        return "abandon_operation"
    has_progress = bool(target.get("returned_object_id")) or any(
        step.get("returned_object_id") for step in target.get("steps", []) or []
    )
    return "resume_operation" if has_progress else "re-preview"


def _update_target_claimed(operation_id: str, target_key: str, claim: dict,
                            apply_baseline: dict) -> None:
    def transaction(_operations_doc, _claims_doc):
        if not claims.is_current_claim(claim["claim_id"], target_key, operation_id):
            raise LostClaimError("execution claim is no longer current")
        operation = next(o for o in _operations_doc["operations"]
                          if o.get("operation_id") == operation_id)
        target = next(t for t in operation["targets"]
                      if t.get("target_key") == target_key)
        target.update({
            "state": "claimed",
            "attempt_id": claim["attempt_id"],
            "payload_digest": claim["payload_digest"],
            "claim_owner": claim["owner_pid"],
            "claim_acquired_at": claim["acquired_at"],
            "claim_lease_expires_at": claim["lease_expires_at"],
            "apply_baseline": copy.deepcopy(apply_baseline),
            "updated_at": models.now_iso(),
        })
        return target
    storage.modify_ledger(transaction)


def _update_target_result(operation_id: str, target_key: str, result: dict,
                          claim: dict) -> None:
    def transaction(_operations_doc, _claims_doc):
        if not claims.is_current_claim(claim["claim_id"], target_key, operation_id):
            raise LostClaimError("execution claim is no longer current")
        operation = next(o for o in _operations_doc["operations"]
                          if o.get("operation_id") == operation_id)
        target = next(t for t in operation["targets"]
                      if t.get("target_key") == target_key)
        target["state"] = result.get("state", "failed")
        if result.get("returned_object_id"):
            target["returned_object_id"] = result["returned_object_id"]
        if result.get("returned_object_url"):
            target["returned_object_url"] = result["returned_object_url"]
        target["error_code"] = result.get("error_code")
        target["private_diagnostic"] = result.get("private_diagnostic")
        if result.get("canvas_message") is not None:
            target["canvas_message"] = result["canvas_message"]
        target["failed_items"] = copy.deepcopy(result.get("failed_items"))
        target["cleanup_required"] = result.get("cleanup_required")
        target["rollback_state"] = result.get("rollback_state")
        target["rollback_error_code"] = result.get("rollback_error_code")
        if result.get("steps"):
            target["steps"] = copy.deepcopy(result["steps"])
        target["updated_at"] = models.now_iso()
        return target
    storage.modify_ledger(transaction)


def _receipt_targets(targets: list[dict]) -> list[dict]:
    rows = []
    for t in targets:
        row = {
            "target_key": t.get("target_key"),
            "state": t.get("state"),
            "returned_object_id": t.get("returned_object_id"),
            "returned_object_url": t.get("returned_object_url"),
            "error_code": t.get("error_code"),
            "failed_items": copy.deepcopy(t.get("failed_items")),
            "cleanup_required": t.get("cleanup_required"),
            "rollback_state": t.get("rollback_state"),
            "rollback_error_code": t.get("rollback_error_code"),
            "steps": _safe_steps(t.get("steps", [])),
        }
        if t.get("drift_fields") is not None:
            row["drift_fields"] = t["drift_fields"]
        if t.get("next") is not None:
            row["next"] = t["next"]
        if t.get("canvas_message") is not None:
            row["canvas_message"] = t["canvas_message"]
        rows.append(row)
    return rows


def _receipt_status(operation_status: str) -> str:
    return {
        "applied": "applied",
        "partial": "partial",
        "failed": "failed",
        "attention": "blocked",
    }.get(operation_status, "failed")


def _project_target_results(results: list[dict]) -> list[dict]:
    projected = []
    for r in results:
        row = {
            "target_key": r.get("target_key"),
            "state": r.get("state"),
            "returned_object_id": r.get("returned_object_id"),
            "returned_object_url": r.get("returned_object_url"),
            "error_code": r.get("error_code"),
            "failed_items": copy.deepcopy(r.get("failed_items")),
            "cleanup_required": r.get("cleanup_required"),
            "rollback_state": r.get("rollback_state"),
            "rollback_error_code": r.get("rollback_error_code"),
            "steps": _safe_steps(r.get("steps", [])),
        }
        if r.get("drift_fields") is not None:
            row["drift_fields"] = r["drift_fields"]
        if r.get("next") is not None:
            row["next"] = r["next"]
        if r.get("canvas_message") is not None:
            row["canvas_message"] = r["canvas_message"]
        projected.append(row)
    return projected


def _safe_steps(steps: list[dict]) -> list[dict]:
    allowed = (
        "step_key", "state", "returned_object_id",
        "returned_object_url", "error_code",
    )
    return [{key: step.get(key) for key in allowed} for step in steps]


def build_repair_plan(operation: dict) -> list[dict]:
    """AC7: what a mid-family stop already created, from recorded steps alone.

    Nothing here reads Canvas or deletes anything -- it is a projection of
    the ledger's own checkpoints, so a teacher (or a senior) knows exactly
    which objects exist and need manual attention.
    """
    plan: list[dict] = []
    for target in operation.get("targets", []) or []:
        found_step = False
        for step in target.get("steps", []) or []:
            if step.get("returned_object_id"):
                found_step = True
                plan.append({
                    "step": step.get("step_key"),
                    "created_id": step.get("returned_object_id"),
                    "state": step.get("state"),
                })
        if not found_step and target.get("returned_object_id"):
            plan.append({
                "step": target.get("target_key"),
                "created_id": target.get("returned_object_id"),
                "state": target.get("state"),
            })
    return plan


def abandon_operation(operation_id: str) -> dict:
    """AC6: mark one existing, teacher-approved operation abandoned.

    Makes no Canvas call and runs no adapter code -- it only flips the
    operation's own status and returns what the ledger already knows was
    created, so a later ``resume_operation`` or ``apply`` on this exact
    operation is refused rather than continuing a family the teacher gave
    up on.
    """
    op = operations.get_operation(operation_id)
    if op is None:
        raise ValueError(f"operation {operation_id} not found")
    old_status = op.get("status", "attention")
    if not models.validate_operation_status_transition(old_status, "abandoned"):
        raise ValueError(f"operation is in status '{old_status}', cannot be abandoned")
    operations.set_operation_status(operation_id, "abandoned")
    updated = operations.get_operation(operation_id) or op
    return {
        "ok": True,
        "operation_id": operation_id,
        "status": "abandoned",
        "repair_plan": build_repair_plan(updated),
    }
