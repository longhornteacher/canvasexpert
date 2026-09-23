"""Recovery — on restart, reconcile sent_unknown / claimed targets.

For every target in ``claimed`` or ``sent_unknown`` state:
1. Skip an unexpired active claim. If expired, mark it expired.
2. Call ``adapter.reconcile(payload, target, baseline)``.
3. If reconcile proves applied: persist returned IDs, set target applied.
4. If reconcile proves absent: set target back to pending (eligible for retry).
5. If reconcile cannot prove either: target stays sent_unknown (Attention).

Without this running, a crash between sending a Canvas write and recording its
result leaves the target's claim unreconciled — every later retry attempt then
raises ``ClaimConflictError`` (see ``claims.acquire_claim``) before ever
reaching Canvas again, since nothing else in the app calls
``claims.detect_expired_claims``. Call ``recover_pending_operations`` once at
startup, before any new operation work can claim the same targets.
"""
import time

from api import operational_log

from . import claims, models, operations, registry, storage
from .catalog_reconcile import reconcile_catalog_after_apply


def recover_pending_operations() -> dict:
    """Scan all operations for targets needing recovery and reconcile them.

    Returns a summary: ``{recovered: int, still_unknown: int, reset_to_pending: int}``.
    """
    started = time.monotonic()
    doc = storage.read_operations_document()
    summary = {"recovered": 0, "still_unknown": 0, "reset_to_pending": 0}

    for op in doc["operations"]:
        op_id = op["operation_id"]
        needs_recovery = False
        for target in op.get("targets", []):
            state = target.get("state")
            if state in ("claimed", "sent_unknown"):
                needs_recovery = True
                break

        if not needs_recovery:
            continue

        try:
            adapter = registry.get_adapter(op["kind"])
        except ValueError:
            continue  # unknown kind — skip

        payload = op.get("normalized_payload", {})

        for target in op.get("targets", []):
            state = target.get("state")
            if state not in ("claimed", "sent_unknown"):
                continue

            # An active lease blocks recovery regardless of PID.
            claim_id = f"{target['target_key']}:{target.get('attempt_id', '')}"
            claim = storage.find_claim(claim_id) if target.get("attempt_id") else None
            if claim and claim.get("state") == "claimed":
                if not claims._is_claim_expired(claim):
                    continue
                claims.detect_expired_claims()
                claim = storage.find_claim(claim_id)
                if claim and claim.get("state") == "claimed":
                    continue

            # Reconcile
            baseline = target.get("baseline", {})
            result = adapter.reconcile(payload, target, baseline)
            new_state = result.get("state", "sent_unknown")
            expired_claim_id = (
                claim_id if claim and claim.get("state") == "expired" else None
            )

            if new_state == "applied":
                _apply_recovered(op_id, target["target_key"], result,
                                 expired_claim_id)
                summary["recovered"] += 1
            elif new_state == "pending":
                if _has_outbound_marker(target):
                    # Target stays sent_unknown; reconcile the claim so a
                    # future retry can proceed, but do not mutate the target.
                    if expired_claim_id:
                        claims.mark_reconciled(expired_claim_id)
                    summary["still_unknown"] += 1
                else:
                    _reset_to_pending(op_id, target["target_key"],
                                      expired_claim_id)
                    summary["reset_to_pending"] += 1
            else:
                if expired_claim_id:
                    claims.mark_reconciled(expired_claim_id)
                summary["still_unknown"] += 1

    operational_log.emit(
        "operation_ledger.recovery", "ok",
        duration_ms=max(0, int(round((time.monotonic() - started) * 1000))),
        count=summary["recovered"] + summary["still_unknown"] + summary["reset_to_pending"],
    )
    return summary


def _apply_recovered(operation_id: str, target_key: str, result: dict,
                     expired_claim_id: str | None = None) -> None:
    """Atomically reconcile the expired claim and persist the recovered target.

    Both mutations occur under one ``modify_ledger`` transaction so a new
    attempt cannot acquire the target between claim reconciliation and the
    recovered target write.
    """
    reconcile_context: dict = {}

    def _transaction(operations_doc, claims_doc):
        if expired_claim_id:
            for item in claims_doc["claims"]:
                if (item.get("claim_id") == expired_claim_id
                        and item.get("state") == "expired"):
                    item["reconciled_at"] = models.now_iso()
                    break
        op = next((o for o in operations_doc["operations"]
                   if o.get("operation_id") == operation_id), None)
        if op is None:
            return operations_doc, claims_doc
        for t in op.get("targets", []):
            if t.get("target_key") == target_key:
                reconcile_context["kind"] = op.get("kind")
                reconcile_context["course_id"] = t.get("course_id")
                reconcile_context["payload"] = op.get("normalized_payload")
                t["state"] = "applied"
                if result.get("returned_object_id"):
                    t["returned_object_id"] = result["returned_object_id"]
                if result.get("returned_object_url"):
                    t["returned_object_url"] = result["returned_object_url"]
                if result.get("module_item_id"):
                    for step in t.get("steps", []):
                        if step.get("step_key") == "attach_module":
                            step["returned_object_id"] = result["module_item_id"]
                            step["state"] = "applied"
                            break
                for recovered_step in result.get("steps", []):
                    existing = next(
                        (step for step in t.get("steps", [])
                         if step.get("step_key") == recovered_step.get("step_key")),
                        None,
                    )
                    if existing is None:
                        continue
                    for key in (
                        "state", "returned_object_id", "returned_object_url",
                        "error_code",
                    ):
                        if key in recovered_step:
                            existing[key] = recovered_step.get(key)
                reconcile_context["target"] = dict(t)
                t["claim_owner"] = None
                t["claim_acquired_at"] = None
                t["claim_lease_expires_at"] = None
                t["updated_at"] = models.now_iso()
                break
        return operations_doc, claims_doc
    storage.modify_ledger(_transaction)
    if reconcile_context.get("kind") and reconcile_context.get("course_id"):
        reconcile_catalog_after_apply(
            reconcile_context["kind"], reconcile_context["course_id"],
            payload=reconcile_context.get("payload"),
            result=result, target=reconcile_context.get("target"),
            operation_id=operation_id,
        )


def _reset_to_pending(operation_id: str, target_key: str,
                      expired_claim_id: str | None = None) -> None:
    """Atomically reconcile the expired claim and reset the target to pending."""
    def _transaction(operations_doc, claims_doc):
        if expired_claim_id:
            for item in claims_doc["claims"]:
                if (item.get("claim_id") == expired_claim_id
                        and item.get("state") == "expired"):
                    item["reconciled_at"] = models.now_iso()
                    break
        op = next((o for o in operations_doc["operations"]
                   if o.get("operation_id") == operation_id), None)
        if op is None:
            return operations_doc, claims_doc
        for t in op.get("targets", []):
            if t.get("target_key") == target_key:
                t["state"] = "pending"
                t["attempt_id"] = None
                t["payload_digest"] = None
                t["claim_owner"] = None
                t["claim_acquired_at"] = None
                t["claim_lease_expires_at"] = None
                t["updated_at"] = models.now_iso()
                break
        return operations_doc, claims_doc
    storage.modify_ledger(_transaction)


def _has_outbound_marker(target: dict) -> bool:
    return any(step.get("outbound_started_at") for step in target.get("steps", []))
