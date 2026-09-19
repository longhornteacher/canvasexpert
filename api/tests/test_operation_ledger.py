"""Operation-ledger schema, atomicity, concurrency, and corruption tests.

Mirrors the patterns from test_receipt_store.py: path redirect via monkeypatch,
atomic-failure preservation, concurrent claim acquisition, corrupt-file quarantine,
lease expiry detection, and stale-claim recovery.
"""
import copy
import json
import multiprocessing
import os
import threading
import time

import pytest

from api import course_catalog
from api.operation_ledger import (
    batches, claims, executor, models, operations, paths, registry, storage,
)
from api.operation_ledger import catalog_reconcile as _catalog_reconcile
from api.operation_ledger.catalog_reconcile import reconcile_catalog_after_apply


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(paths, "private_root", lambda: root)
    return root


def test_assignment_tier_operation_receipt_describes_complete_family_sources(monkeypatch):
    operation = {
        "operation_id": "op-tiered",
        "kind": "content.assignment",
        "normalized_payload": {"tiers": [
            {"label": "Support", "title": "Practice - Red"},
            {"label": "Core", "title": "Practice - Blue"},
        ]},
        "targets": [{
            "target_key": "target-1", "state": "applied",
            "steps": [
                {"step_key": "create_tier_assignment:0", "state": "applied",
                 "returned_object_id": "101", "returned_object_url": "https://canvas.invalid/a/101"},
                {"step_key": "create_tier_assignment:1", "state": "applied",
                 "returned_object_id": "102", "returned_object_url": "https://canvas.invalid/a/102"},
            ],
        }],
    }
    captured = {}
    monkeypatch.setattr(executor.operations, "get_operation", lambda _id: operation)
    monkeypatch.setattr(executor.operations, "set_operation_status", lambda _id, status: None)
    monkeypatch.setattr(executor, "create_receipt", lambda receipt: captured.setdefault("receipt", receipt))

    result = executor._finish_operation("op-tiered", [])
    receipt = captured["receipt"]
    assert result["status"] == "applied"
    assert "teacher_action" not in receipt
    assert receipt["variants"] == [
        {"label": "Support", "assignment_id": "101", "name": "Practice - Red",
         "html_url": "https://canvas.invalid/a/101"},
        {"label": "Core", "assignment_id": "102", "name": "Practice - Blue",
         "html_url": "https://canvas.invalid/a/102"},
    ]
    serialized = json.dumps(receipt)
    assert not any(field in serialized for field in (
        '"group"', '"group_name"', '"student_count"', '"member_ids"',
        '"student_ids"', '"bridge"', '"family"',
    ))


def _make_operation(operation_id="op-test1", targets=None):
    """Build a minimal valid operation for testing."""
    if targets is None:
        targets = [models.new_target(
            target_key="tk-1", idempotency_key="ik-1", course_id="101")]
    return models.new_operation(
        operation_id=operation_id,
        kind="content.page",
        source_ref={"type": "workspace_relative", "value": "Pages/test.pageforge.json"},
        source_digest="abc123",
        normalized_payload={"title": "Test", "body": "<p>Hi</p>", "published": False},
        targets=targets,
    )


def _spawn_claim_worker(root, barrier, result_queue):
    """Top-level worker for portable spawn-based claim contention evidence."""
    os.environ["LOCALAPPDATA"] = root
    from api.operation_ledger import claims
    barrier.wait()
    try:
        claim = claims.acquire_claim(
            target_key="spawn-target",
            operation_id="op-spawn",
            payload_digest="spawn-digest",
        )
        result_queue.put(("acquired", claim["claim_id"]))
    except claims.ClaimConflictError:
        result_queue.put(("conflicted", None))


# ── Operation round-trip ────────────────────────────────────────────────

def test_operation_round_trip_preserves_all_fields(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    op = _make_operation()
    created = operations.create_operation(op)
    assert created["operation_id"] == "op-test1"
    assert created["kind"] == "content.page"
    assert created["status"] == "prepared"
    assert created["source_digest"] == "abc123"
    assert len(created["targets"]) == 1

    fetched = operations.get_operation("op-test1")
    assert fetched["operation_id"] == "op-test1"
    assert fetched["normalized_payload"]["title"] == "Test"
    assert fetched["targets"][0]["target_key"] == "tk-1"


def test_list_operations_returns_all(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    operations.create_operation(_make_operation("op-a"))
    operations.create_operation(_make_operation("op-b"))
    ops = operations.list_operations()
    assert len(ops) == 2
    assert {o["operation_id"] for o in ops} == {"op-a", "op-b"}


def test_list_operations_pii_minimized(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    operations.create_operation(_make_operation("op-pii"))
    minimized = operations.list_operations_pii_minimized()
    assert len(minimized) == 1
    m = minimized[0]
    assert m["operation_id"] == "op-pii"
    assert m["kind"] == "content.page"
    assert m["status"] == "prepared"
    assert m["target_count"] == 1
    # No course IDs, no payload, no target details
    text = json.dumps(minimized)
    assert "101" not in text  # course_id should not appear
    assert "normalized_payload" not in text
    assert "targets" not in text


# ── Target state transitions ────────────────────────────────────────────

def test_valid_target_state_transitions():
    assert models.validate_target_state_transition("pending", "claimed")
    assert models.validate_target_state_transition("claimed", "applied")
    assert models.validate_target_state_transition("claimed", "sent_unknown")
    assert models.validate_target_state_transition("claimed", "failed")
    assert models.validate_target_state_transition("claimed", "partial")
    assert models.validate_target_state_transition("sent_unknown", "applied")
    assert models.validate_target_state_transition("sent_unknown", "pending")


def test_invalid_target_state_transitions():
    assert not models.validate_target_state_transition("pending", "applied")
    assert not models.validate_target_state_transition("applied", "pending")
    assert not models.validate_target_state_transition("skipped", "pending")
    assert not models.validate_target_state_transition("applied", "failed")
    assert models.is_unresolved_target_state("partial")
    assert models.compute_operation_status([{"state": "partial"}]) == "partial"


def test_operation_status_transitions():
    assert models.validate_operation_status_transition("working", "prepared")
    assert models.validate_operation_status_transition("prepared", "reviewed")
    assert models.validate_operation_status_transition("reviewed", "applying")
    assert models.validate_operation_status_transition("applying", "applied")
    assert models.validate_operation_status_transition("attention", "applying")
    assert not models.validate_operation_status_transition("applied", "applying")
    assert not models.validate_operation_status_transition("prepared", "applied")


# ── Invalid kind rejected ───────────────────────────────────────────────

def test_unknown_kind_rejected():
    from api.operation_ledger import registry
    with pytest.raises(ValueError, match="unknown operation kind"):
        registry.get_adapter("content.nonexistent")


# ── Concurrent claim acquisition ────────────────────────────────────────

def test_concurrent_claim_acquisition(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    # Create an operation with a target
    operations.create_operation(_make_operation("op-concurrent"))

    results = {"acquired": [], "conflicted": []}

    def try_acquire(index):
        try:
            claim = claims.acquire_claim(
                target_key="tk-1",
                operation_id="op-concurrent",
                payload_digest="digest-1",
            )
            results["acquired"].append(index)
        except claims.ClaimConflictError:
            results["conflicted"].append(index)

    threads = [threading.Thread(target=try_acquire, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread should acquire the claim
    assert len(results["acquired"]) == 1
    assert len(results["conflicted"]) == 7


def test_spawned_processes_race_for_one_claim(tmp_path, monkeypatch):
    """The shared adjacent lock serializes claims across spawned processes."""
    root = _root(tmp_path, monkeypatch)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    result_queue = ctx.Queue()
    processes = [ctx.Process(target=_spawn_claim_worker,
                             args=(str(root), barrier, result_queue)) for _ in range(2)]
    for process in processes:
        process.start()
    results = [result_queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert [result[0] for result in results].count("acquired") == 1
    assert [result[0] for result in results].count("conflicted") == 1


# ── Atomic failure preserves previous document ──────────────────────────

def test_atomic_failure_preserves_previous_operations(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    operations.create_operation(_make_operation("op-before"))
    before = paths.operations_file().read_text(encoding="utf-8")

    monkeypatch.setattr(storage.os, "replace",
                        lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        operations.create_operation(_make_operation("op-after"))

    assert paths.operations_file().read_text(encoding="utf-8") == before


# ── Corrupt operations file quarantined ─────────────────────────────────

def test_corrupt_operations_file_quarantined(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    path = paths.operations_file()
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert operations.list_operations() == []
    assert list((root / "quarantine").glob("*.corrupt"))


def test_unknown_version_operations_quarantined(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    path = paths.operations_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 99, "operations": []}), encoding="utf-8")
    assert operations.list_operations() == []
    assert list((root / "quarantine").glob("*.corrupt"))


# ── Lease expiry detection ──────────────────────────────────────────────

def test_lease_expiry_detection(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    # Create a claim with an expired lease
    claim = models.new_claim(
        claim_id="tk-1:attempt-1",
        target_key="tk-1",
        operation_id="op-lease",
        attempt_id="attempt-1",
        owner_pid=99999,  # different PID
        owner_started_at="2020-01-01T00:00:00+00:00",
        payload_digest="digest",
    )
    # Manually expire the lease
    claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(claim)

    expired = claims.detect_expired_claims()
    assert len(expired) == 1
    assert expired[0]["claim_id"] == "tk-1:attempt-1"
    assert expired[0]["state"] == "expired"


def test_active_claim_not_expired(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    claim = models.new_claim(
        claim_id="tk-active:attempt-1",
        target_key="tk-active",
        operation_id="op-active",
        attempt_id="attempt-1",
        owner_pid=99999,
        owner_started_at="2020-01-01T00:00:00+00:00",
        payload_digest="digest",
    )
    # Lease is in the future (5 minutes from now by default)
    storage.upsert_claim(claim)

    expired = claims.detect_expired_claims()
    # A different PID is not evidence of death while the lease is valid.
    assert expired == []


def test_expired_claim_requires_recovery_before_acquisition(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    claim = models.new_claim(
        claim_id="tk-expired:attempt-old", target_key="tk-expired",
        operation_id="op-expired", attempt_id="attempt-old", owner_pid=1,
        owner_started_at="2020-01-01T00:00:00+00:00", payload_digest="digest")
    claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(claim)
    with pytest.raises(claims.ClaimConflictError):
        claims.acquire_claim(target_key="tk-expired", operation_id="op-expired",
                             payload_digest="new-digest")
    assert storage.find_claim(claim["claim_id"])["state"] == "claimed"
    claims.detect_expired_claims()
    with pytest.raises(claims.ClaimConflictError):
        claims.acquire_claim(target_key="tk-expired", operation_id="op-expired",
                             payload_digest="new-digest")
    claims.mark_reconciled(claim["claim_id"])
    replacement = claims.acquire_claim(target_key="tk-expired", operation_id="op-expired",
                                       payload_digest="new-digest")
    assert replacement["state"] == "claimed"


def test_concurrent_operation_target_mutations_preserve_updates(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    targets = [models.new_target(target_key=f"tk-{i}", idempotency_key=f"ik-{i}",
                                 course_id=str(i)) for i in range(2)]
    operations.create_operation(_make_operation("op-mutations", targets))

    def update(index):
        operations.update_target("op-mutations", f"tk-{index}",
                                 lambda target: {**target, "error_code": f"error-{index}"})

    threads = [threading.Thread(target=update, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stored = operations.get_operation("op-mutations")
    assert {target["error_code"] for target in stored["targets"]} == {"error-0", "error-1"}


def test_stale_worker_cannot_checkpoint_after_recovery_fencing(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    operation = _make_operation("op-fence")
    operations.create_operation(operation)
    target = operation["targets"][0]
    claim = claims.acquire_claim(target_key=target["target_key"],
                                 operation_id=operation["operation_id"],
                                 payload_digest="digest")
    executor._update_target_claimed(operation["operation_id"], target["target_key"],
                                     claim, {"existing_page": None})
    context = executor.ExecutionContext(operation_id=operation["operation_id"],
                                        target_key=target["target_key"], claim=claim)
    stored_claim = storage.find_claim(claim["claim_id"])
    stored_claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(stored_claim)
    claims.detect_expired_claims()
    before = operations.get_operation(operation["operation_id"])
    with pytest.raises(executor.LostClaimError):
        context.before_send("create_page", "digest")
    after = operations.get_operation(operation["operation_id"])
    assert after["targets"] == before["targets"]


# ── Stale claim recovery ────────────────────────────────────────────────

def test_stale_claim_marked_expired_on_recovery(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    # Create a stale claim (expired lease)
    claim = models.new_claim(
        claim_id="tk-stale:attempt-old",
        target_key="tk-stale",
        operation_id="op-stale",
        attempt_id="attempt-old",
        owner_pid=99999,
        owner_started_at="2020-01-01T00:00:00+00:00",
        payload_digest="digest",
    )
    claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(claim)

    expired = claims.detect_expired_claims()
    assert any(c["claim_id"] == "tk-stale:attempt-old" for c in expired)


# ── release_claim safety ────────────────────────────────────────────────

def test_release_claim_does_not_release_expired_claim(tmp_path, monkeypatch):
    """A fenced stale worker must not flip an expired claim to released."""
    _root(tmp_path, monkeypatch)
    claim = models.new_claim(
        claim_id="tk-stale:attempt-old", target_key="tk-stale",
        operation_id="op-stale", attempt_id="attempt-old", owner_pid=99999,
        owner_started_at="2020-01-01T00:00:00+00:00", payload_digest="digest")
    claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(claim)
    claims.detect_expired_claims()
    assert storage.find_claim(claim["claim_id"])["state"] == "expired"

    # A stale worker tries to release the now-expired claim.
    claims.release_claim(claim["claim_id"])

    # The claim must still be expired, not released.
    stored = storage.find_claim(claim["claim_id"])
    assert stored["state"] == "expired"


def test_release_claim_releases_active_claim(tmp_path, monkeypatch):
    """A normal claimed record can still be released."""
    _root(tmp_path, monkeypatch)
    claim = claims.acquire_claim(
        target_key="tk-active", operation_id="op-active",
        payload_digest="digest")
    assert claim["state"] == "claimed"
    claims.release_claim(claim["claim_id"])
    assert storage.find_claim(claim["claim_id"])["state"] == "released"


# ── Recovery atomicity ──────────────────────────────────────────────────

def test_recovery_reconciles_claim_and_target_atomically(tmp_path, monkeypatch):
    """After recovery, the claim is reconciled and the target is applied
    in one transaction — a new attempt cannot acquire the target during
    the gap because the claim is not reconciled until the target is written.
    """
    _root(tmp_path, monkeypatch)
    from api.operation_ledger import recovery

    # Build an operation with a sent_unknown target and an expired claim.
    target = models.new_target(
        target_key="tk-recover", idempotency_key="ik-recover", course_id="101")
    target["state"] = "sent_unknown"
    target["attempt_id"] = "attempt-old"
    target["returned_object_id"] = "my-slug"
    target["steps"] = [models.new_step("create_page")]
    target["steps"][0]["step_key"] = "create_page"
    target["steps"][0]["state"] = "applied"
    target["steps"][0]["returned_object_id"] = "my-slug"
    op = _make_operation("op-recover", targets=[target])
    operations.create_operation(op)

    # Create an expired claim for this target.
    claim = models.new_claim(
        claim_id=f"tk-recover:{target.get('attempt_id', 'attempt-old')}",
        target_key="tk-recover",
        operation_id="op-recover",
        attempt_id="attempt-old",
        owner_pid=99999,
        owner_started_at="2020-01-01T00:00:00+00:00",
        payload_digest="digest")
    claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(claim)
    claims.detect_expired_claims()
    assert storage.find_claim(claim["claim_id"])["state"] == "expired"

    # Mock the adapter so reconcile proves applied.
    class FakeAdapter:
        kind = "content.page"
        def reconcile(self, payload, target, baseline):
            return {"state": "applied",
                    "returned_object_id": "my-slug",
                    "returned_object_url": "http://canvas/pages/my-slug"}
    monkeypatch.setattr(registry, "get_adapter", lambda kind: FakeAdapter())

    summary = recovery.recover_pending_operations()
    assert summary["recovered"] == 1

    # The target must be applied.
    stored_op = operations.get_operation("op-recover")
    assert stored_op["targets"][0]["state"] == "applied"

    # The claim must be reconciled (reconciled_at set).
    stored_claim = storage.find_claim(claim["claim_id"])
    assert stored_claim["state"] == "expired"
    assert stored_claim.get("reconciled_at") is not None

    # A new attempt can now acquire a fresh claim (the old one is reconciled).
    new_claim = claims.acquire_claim(
        target_key="tk-recover", operation_id="op-recover",
        payload_digest="new-digest")
    assert new_claim["state"] == "claimed"


def test_recovery_atomicity_no_gap_for_new_attempt(tmp_path, monkeypatch):
    """Negative test: if recovery only reconciled the claim without writing
    the target, a new attempt could re-acquire and re-send. This test verifies
    the target is already applied when the claim becomes reconciled, by
    checking there is no intermediate state where the claim is reconciled
    but the target is still sent_unknown.

    We simulate this by patching modify_ledger to capture the document state
    inside the transaction and asserting both mutations are visible together.
    """
    _root(tmp_path, monkeypatch)
    from api.operation_ledger import recovery, storage as storage_mod

    target = models.new_target(
        target_key="tk-atomic", idempotency_key="ik-atomic", course_id="101")
    target["state"] = "sent_unknown"
    target["attempt_id"] = "attempt-old"
    target["returned_object_id"] = "my-slug"
    target["steps"] = [models.new_step("create_page")]
    target["steps"][0]["step_key"] = "create_page"
    target["steps"][0]["state"] = "applied"
    target["steps"][0]["returned_object_id"] = "my-slug"
    op = _make_operation("op-atomic", targets=[target])
    operations.create_operation(op)

    claim = models.new_claim(
        claim_id="tk-atomic:attempt-old",
        target_key="tk-atomic",
        operation_id="op-atomic",
        attempt_id="attempt-old",
        owner_pid=99999,
        owner_started_at="2020-01-01T00:00:00+00:00",
        payload_digest="digest")
    claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(claim)
    claims.detect_expired_claims()

    captured_states = []
    original_modify_ledger = storage_mod.modify_ledger

    def capturing_modify_ledger(mutator):
        def wrapped_mutator(ops_doc, claims_doc):
            result = mutator(ops_doc, claims_doc)
            # Capture the state after the mutator runs but before commit.
            for op_item in ops_doc["operations"]:
                if op_item.get("operation_id") == "op-atomic":
                    for t in op_item.get("targets", []):
                        if t.get("target_key") == "tk-atomic":
                            target_state = t.get("state")
            claim_reconciled = any(
                c.get("claim_id") == "tk-atomic:attempt-old"
                and c.get("reconciled_at") is not None
                for c in claims_doc["claims"])
            captured_states.append((target_state, claim_reconciled))
            return result
        return original_modify_ledger(wrapped_mutator)

    monkeypatch.setattr(storage_mod, "modify_ledger", capturing_modify_ledger)

    class FakeAdapter:
        kind = "content.page"
        def reconcile(self, payload, target, baseline):
            return {"state": "applied",
                    "returned_object_id": "my-slug",
                    "returned_object_url": "http://canvas/pages/my-slug"}
    monkeypatch.setattr(registry, "get_adapter", lambda kind: FakeAdapter())

    recovery.recover_pending_operations()

    # Inside the transaction, the target is applied AND the claim is reconciled
    # in the same atomic step — there is no intermediate state.
    assert len(captured_states) >= 1
    for target_state, claim_reconciled in captured_states:
        if claim_reconciled:
            assert target_state == "applied", (
                "claim reconciled before target was written — gap exists")


# ── Catalog reconciliation hook (Batch 7 unit 01) ───────────────────────

def _fresh_scope(records):
    return {
        "state": "current",
        "last_success_at": "2026-01-01T00:00:00+00:00",
        "last_attempt_at": "2026-01-01T00:00:00+00:00",
        "error_code": "",
        "records": records,
    }


def _spy_invalidate_scope(monkeypatch):
    calls = []

    def fake_invalidate_scope(course_id, scope_key, **kwargs):
        calls.append((course_id, scope_key))
        return None

    monkeypatch.setattr(course_catalog, "invalidate_scope", fake_invalidate_scope)
    return calls


def test_successful_apply_invalidates_the_kinds_mapped_catalog_scopes(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    calls = _spy_invalidate_scope(monkeypatch)

    class FakeAdapter:
        kind = "content.assignment"
        def capture_baseline(self, payload, target):
            return {}
        def check_drift(self, payload, target, baseline):
            return False
        def execute(self, payload, target, baseline, claim, context):
            return {"state": "applied", "returned_object_id": "999"}
    monkeypatch.setattr(registry, "get_adapter", lambda kind: FakeAdapter())

    op = _make_operation("op-catalog-1", targets=[models.new_target(
        target_key="tk-catalog-1", idempotency_key="ik-catalog-1", course_id="101")])
    op["kind"] = "content.assignment"
    operations.create_operation(op)
    batch = batches.freeze_batch(["op-catalog-1"], {"op-catalog-1": [{}]})
    operations.set_operation_review("op-catalog-1", batch)

    result = executor.apply_operation("op-catalog-1", batch["batch_id"], batch["review_digest"])

    assert result["status"] == "applied"
    assert set(calls) == {("101", "assignments"), ("101", "modules")}


def test_failed_apply_never_invalidates_catalog(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    calls = _spy_invalidate_scope(monkeypatch)

    class FakeAdapter:
        kind = "content.assignment"
        def capture_baseline(self, payload, target):
            return {}
        def check_drift(self, payload, target, baseline):
            return False
        def execute(self, payload, target, baseline, claim, context):
            return {"state": "failed", "error_code": "canvas_rejected"}
    monkeypatch.setattr(registry, "get_adapter", lambda kind: FakeAdapter())

    op = _make_operation("op-catalog-2", targets=[models.new_target(
        target_key="tk-catalog-2", idempotency_key="ik-catalog-2", course_id="101")])
    op["kind"] = "content.assignment"
    operations.create_operation(op)
    batch = batches.freeze_batch(["op-catalog-2"], {"op-catalog-2": [{}]})
    operations.set_operation_review("op-catalog-2", batch)

    result = executor.apply_operation("op-catalog-2", batch["batch_id"], batch["review_digest"])

    assert result["status"] == "failed"
    assert calls == []


def test_recovery_apply_invalidates_catalog_scopes(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    from api.operation_ledger import recovery
    calls = _spy_invalidate_scope(monkeypatch)

    target = models.new_target(
        target_key="tk-recover-catalog", idempotency_key="ik-recover-catalog", course_id="202")
    target["state"] = "sent_unknown"
    target["attempt_id"] = "attempt-old"
    op = _make_operation("op-recover-catalog", targets=[target])
    op["kind"] = "content.quiz"
    operations.create_operation(op)

    claim = models.new_claim(
        claim_id="tk-recover-catalog:attempt-old",
        target_key="tk-recover-catalog",
        operation_id="op-recover-catalog",
        attempt_id="attempt-old",
        owner_pid=99999,
        owner_started_at="2020-01-01T00:00:00+00:00",
        payload_digest="digest")
    claim["lease_expires_at"] = "2020-01-01T00:00:01+00:00"
    storage.upsert_claim(claim)
    claims.detect_expired_claims()

    class FakeAdapter:
        kind = "content.quiz"
        def reconcile(self, payload, target, baseline):
            return {"state": "applied", "returned_object_id": "quiz-1"}
    monkeypatch.setattr(registry, "get_adapter", lambda kind: FakeAdapter())

    summary = recovery.recover_pending_operations()

    assert summary["recovered"] == 1
    assert set(calls) == {("202", "assignments"), ("202", "modules")}


@pytest.mark.parametrize(
    ("kind", "payload", "expected_scopes"),
    [
        ("content.assignment", {}, {"assignments", "modules"}),
        ("content.quiz", {}, {"assignments", "modules"}),
        ("content.quick_assignment", {}, {"assignments"}),
            ("gradebook.sis_bridge", {}, {"assignments", "modules"}),
        # A page always lands a Canvas page, so `pages` is unconditional; the
        # module scope stays payload-sensitive (a bare page touches no module).
        ("content.page", {}, {"pages"}),
        ("content.page", {"module_name": "Unit 1"}, {"pages", "modules"}),
        # An unmapped kind still invalidates nothing.
        ("content.not_a_real_kind", {"module_name": "Unit 1"}, set()),
    ],
)
def test_catalog_reconcile_kind_mapping_respects_page_and_rubric_boundaries(
    monkeypatch, kind, payload, expected_scopes,
):
    calls = _spy_invalidate_scope(monkeypatch)

    reconcile_catalog_after_apply(kind, "303", payload=payload)

    assert set(calls) == {("303", scope) for scope in expected_scopes}


def test_page_apply_invalidates_the_pages_catalog_scope(tmp_path, monkeypatch):
    """The regression this guards: a page created in Canvas left the local
    `pages` catalog scope fresh, so `get_course_pages` kept reporting the old
    record set until someone refreshed the catalog by hand."""
    _root(tmp_path, monkeypatch)
    calls = _spy_invalidate_scope(monkeypatch)

    class FakeAdapter:
        kind = "content.page"
        def capture_baseline(self, payload, target):
            return {}
        def check_drift(self, payload, target, baseline):
            return False
        def execute(self, payload, target, baseline, claim, context):
            return {"state": "applied", "returned_object_id": "a-new-page"}
    monkeypatch.setattr(registry, "get_adapter", lambda kind: FakeAdapter())

    op = _make_operation("op-catalog-page", targets=[models.new_target(
        target_key="tk-catalog-page", idempotency_key="ik-catalog-page",
        course_id="404")])
    op["normalized_payload"] = {
        "title": "Test", "body": "<p>Hi</p>", "published": True, "module_name": None,
    }
    operations.create_operation(op)
    batch = batches.freeze_batch(["op-catalog-page"], {"op-catalog-page": [{}]})
    operations.set_operation_review("op-catalog-page", batch)

    result = executor.apply_operation(
        "op-catalog-page", batch["batch_id"], batch["review_digest"])

    assert result["status"] == "applied"
    assert set(calls) == {("404", "pages")}


def test_every_scope_the_hook_can_emit_is_a_real_invalidatable_scope():
    """Vocabulary-drift guard. The hook names scopes as bare strings; a scope
    that `course_catalog` does not accept would raise at apply time, and a real
    scope the hook never names is a silent staleness gap (which is exactly how
    `pages` was missed when the v3 catalog added it)."""
    kinds = set(_catalog_reconcile._KIND_TO_CATALOG_SCOPES) | {
        _catalog_reconcile._PAGE_KIND}
    scopes = set()
    for kind in kinds:
        for payload in ({}, {"module_name": "Unit 1"}):
            scopes |= set(_catalog_reconcile._scopes_for(kind, payload))

    assert scopes <= course_catalog.INVALIDATABLE_SCOPES, (
        "hook emits a scope course_catalog.invalidate_scope would reject")
    assert scopes == course_catalog.INVALIDATABLE_SCOPES - {"assignment_groups"}, (
        "a v3 catalog scope gained or lost coverage in the post-apply hook: "
        "confirm it against the adapters and update "
        "docs/reference/mutation-reconciliation-map.md family 2")


def test_page_apply_actually_marks_a_real_catalog_document_stale(tmp_path):
    """End to end against a real v3 document: the pages scope flips to stale,
    its records survive untouched, and no other scope moves."""
    document = {
        "version": course_catalog.CATALOG_VERSION,
        "course_id": "606",
        "course_name": "Fictional Course",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "assignments": _fresh_scope({}),
        "modules": _fresh_scope([]),
        "assignment_groups": _fresh_scope([]),
        "pages": _fresh_scope([{
            "id": "500", "title": "Syllabus", "body_text": "hello",
            "published": True, "front_page": False,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }]),
    }
    course_catalog.write_catalog(copy.deepcopy(document), root=str(tmp_path))

    reconcile_catalog_after_apply(
        "content.page", "606", payload={"title": "New page"},
        root=str(tmp_path), attempted_at="2026-02-02T00:00:00+00:00",
    )

    stored = course_catalog.read_catalog("606", root=str(tmp_path))["catalog"]
    assert stored["pages"]["state"] == "stale"
    assert stored["pages"]["error_code"] == "invalidated"
    assert stored["pages"]["last_attempt_at"] == "2026-02-02T00:00:00+00:00"
    assert stored["pages"]["records"] == document["pages"]["records"]
    for other in ("assignments", "modules", "assignment_groups"):
        assert stored[other] == document[other]
