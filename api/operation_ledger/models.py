"""Data-model helpers for the operation ledger.

Pure functions for creating and validating operation, target, step, batch, and
claim records. No I/O — storage lives in ``storage.py``.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

VERSION = 1

# ── Enums ────────────────────────────────────────────────────────────────

OPERATION_STATUSES = (
    "working", "prepared", "reviewed", "applying",
    "applied", "partial", "failed", "attention", "abandoned",
)

TARGET_STATES = (
    "pending", "claimed", "sent_unknown",
    "applied", "partial", "failed", "blocked", "skipped",
)

STEP_STATES = TARGET_STATES  # same vocabulary

CLAIM_STATES = ("claimed", "released", "expired")

# Valid forward-only target transitions (plus attention→applying via retry).
TARGET_TRANSITIONS = {
    "pending":     {"claimed"},
    "claimed":     {"sent_unknown", "applied", "partial", "failed", "blocked", "skipped"},
    "sent_unknown": {"applied", "failed", "pending"},  # recovery may resolve
    "applied":     set(),   # terminal
    "partial":     {"pending"},  # retry may re-queue unfinished work
    "failed":      {"pending"},  # retry may re-queue
    "blocked":     {"pending"},  # retry after human review
    "skipped":     set(),   # terminal
}

OPERATION_TRANSITIONS = {
    "working":   {"prepared", "abandoned"},
    "prepared":  {"reviewed", "abandoned"},
    "reviewed":  {"applying", "abandoned"},
    "applying":  {"applied", "partial", "failed", "attention"},
    "attention": {"applying", "abandoned"},  # retry, or a teacher gives up (AC6)
    "applied":   set(),
    "partial":   {"applying", "abandoned"},  # retry unresolved, or abandon
    "failed":    {"abandoned"},
    "abandoned": set(),  # terminal; blocks later resume/apply (AC6)
}

LEASE_SECONDS = 300  # 5 minutes


# ── Timestamps ──────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def lease_expiry(acquired_at: str, seconds: int = LEASE_SECONDS) -> str:
    dt = datetime.fromisoformat(acquired_at)
    return (dt + _timedelta_seconds(seconds)).isoformat(timespec="seconds")


def _timedelta_seconds(seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)


# ── IDs ──────────────────────────────────────────────────────────────────

def new_operation_id() -> str:
    return f"op-{secrets.token_urlsafe(12)}"


def new_batch_id() -> str:
    return f"batch-{secrets.token_urlsafe(12)}"


def new_attempt_id() -> str:
    return str(uuid.uuid4())


# ── Digests ──────────────────────────────────────────────────────────────

def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sha256_dict(obj: dict) -> str:
    """Deterministic SHA-256 over a JSON-serializable dict."""
    import json
    return sha256_hex(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


# ── Record factories ─────────────────────────────────────────────────────

def new_operation(*, operation_id: str, kind: str, source_ref: dict | None,
                  source_digest: str, normalized_payload: dict,
                  targets: list[dict], source_job_id: str | None = None) -> dict:
    ts = now_iso()
    return {
        "version": VERSION,
        "operation_id": operation_id,
        "kind": kind,
        "source_job_id": source_job_id,
        "status": "prepared",
        "source_ref": source_ref,
        "source_digest": source_digest,
        "normalized_payload": normalized_payload,
        "review": None,
        "targets": targets,
        "created_at": ts,
        "updated_at": ts,
    }


def new_target(*, target_key: str, idempotency_key: str, course_id: str,
               baseline: dict | None = None, steps: list[dict] | None = None,
               apply_baseline: dict | None = None) -> dict:
    ts = now_iso()
    return {
        "target_key": target_key,
        "idempotency_key": idempotency_key,
        "course_id": course_id,
        "state": "pending",
        "attempt_id": None,
        "payload_digest": None,
        "claim_owner": None,
        "claim_acquired_at": None,
        "claim_lease_expires_at": None,
        "baseline": baseline,
        "apply_baseline": apply_baseline,
        "returned_object_id": None,
        "returned_object_url": None,
        "steps": steps or [],
        "error_code": None,
        "private_diagnostic": None,
        "failed_items": None,
        "cleanup_required": None,
        "rollback_state": None,
        "rollback_error_code": None,
        "updated_at": ts,
    }


def new_step(step_key: str) -> dict:
    return {
        "step_key": step_key,
        "state": "pending",
        "attempt_id": None,
        "returned_object_id": None,
        "error_code": None,
        "private_diagnostic": None,
        "outbound_started_at": None,
        "updated_at": now_iso(),
    }


def new_batch(*, batch_id: str, review_digest: str, frozen_reviews: list[dict]) -> dict:
    return {
        "batch_id": batch_id,
        "review_digest": review_digest,
        "frozen_reviews": frozen_reviews,
        "created_at": now_iso(),
    }


def new_claim(*, claim_id: str, target_key: str, operation_id: str,
              attempt_id: str, owner_pid: int, owner_started_at: str,
              payload_digest: str) -> dict:
    acquired = now_iso()
    return {
        "claim_id": claim_id,
        "target_key": target_key,
        "operation_id": operation_id,
        "attempt_id": attempt_id,
        "owner_pid": owner_pid,
        "owner_started_at": owner_started_at,
        "acquired_at": acquired,
        "lease_expires_at": lease_expiry(acquired),
        "payload_digest": payload_digest,
        "state": "claimed",
        "reconciled_at": None,
    }


# ── Validation ───────────────────────────────────────────────────────────

def validate_operation_status_transition(old: str, new: str) -> bool:
    return new in OPERATION_TRANSITIONS.get(old, set())


def validate_target_state_transition(old: str, new: str) -> bool:
    return new in TARGET_TRANSITIONS.get(old, set())


def is_terminal_target_state(state: str) -> bool:
    return state in ("applied", "skipped")


def is_unresolved_target_state(state: str) -> bool:
    return state in ("sent_unknown", "partial", "failed", "blocked")


def compute_operation_status(targets: list[dict]) -> str:
    """Derive the operation status from target states after apply/retry."""
    states = [t.get("state", "pending") for t in targets]
    if not states:
        return "failed"
    if all(s == "applied" or s == "skipped" for s in states):
        return "applied"
    if all(s == "partial" for s in states):
        return "partial"
    if all(s == "failed" for s in states):
        return "failed"
    if any(s in ("sent_unknown", "blocked") for s in states):
        return "attention"
    if any(s in ("applied", "skipped", "partial") for s in states):
        return "partial"
    return "failed"
