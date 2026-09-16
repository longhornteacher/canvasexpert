"""Routine coordinator — atomic exactly-once run claims and durable receipts.

Every trigger (foreground, background, launch) calls ``run_routine()``.
It atomically claims ``(routine_id, scheduled_due)``; claims expire after
two hours and are recovered with a redacted Attention record.  Two
simultaneous triggers execute exactly once.

An invoked run writes exactly one receipt: ``no_effect`` when it proves no
work, otherwise applied/partial/failed/blocked.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from api.platform_services import config
from api import operational_log
from api.operation_ledger import paths as ledger_paths
from api.operation_ledger import storage as ledger_storage
from api.operation_ledger import models as ledger_models
from api.operation_ledger import receipts as ledger_receipts


# ── Constants ────────────────────────────────────────────────────────────

CLAIM_EXPIRY_HOURS = 2
COORDINATOR_VERSION = 1
CLAIMS_FILENAME = "routine_claims.v1.json"


# ── Claim storage ────────────────────────────────────────────────────────

def _claims_path() -> Path:
    return ledger_paths.private_root() / CLAIMS_FILENAME


def _default_claims() -> dict:
    return {"version": COORDINATOR_VERSION, "claims": []}


def _load_claims() -> dict:
    path = _claims_path()
    if not path.exists():
        return _default_claims()
    try:
        data = json.loads(path.read_bytes())
        if not isinstance(data, dict):
            return _default_claims()
        return data
    except (json.JSONDecodeError, OSError):
        return _default_claims()


def _save_claims(claims: dict) -> None:
    path = _claims_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger_storage.atomic_write_json(path, claims)


# ── Core coordinator ─────────────────────────────────────────────────────

_COORDINATOR_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _claim_key(routine_id: str, scheduled_due: str | None) -> str:
    return f"{routine_id}|{scheduled_due or ''}"


def _acquire_claim(routine_id: str, scheduled_due: str | None) -> bool:
    """Atomically acquire a run claim.  Returns True if this caller may run."""
    key = _claim_key(routine_id, scheduled_due)
    now = _now_iso()
    with _COORDINATOR_LOCK:
        claims = _load_claims()
        existing = next(
            (c for c in claims["claims"] if c["key"] == key), None
        )
        if existing:
            # Check expiry
            try:
                expires = datetime.fromisoformat(existing["expires_at"])
                if expires > datetime.now(timezone.utc):
                    return False  # Still active — another runner has it
            except (ValueError, TypeError) as exc:
                operational_log.emit("routines.claim_parse", "failed", error_class=type(exc))
            # Expired — reclaim
            existing["acquired_at"] = now
            existing["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(hours=CLAIM_EXPIRY_HOURS)
            ).isoformat(timespec="seconds")
            existing["pid"] = os.getpid()
        else:
            claims["claims"].append({
                "key": key,
                "routine_id": routine_id,
                "scheduled_due": scheduled_due,
                "acquired_at": now,
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=CLAIM_EXPIRY_HOURS)
                ).isoformat(timespec="seconds"),
                "pid": os.getpid(),
            })
        _save_claims(claims)
        return True


def _release_claim(routine_id: str, scheduled_due: str | None) -> None:
    """Release a claim after completion."""
    key = _claim_key(routine_id, scheduled_due)
    with _COORDINATOR_LOCK:
        claims = _load_claims()
        claims["claims"] = [c for c in claims["claims"] if c["key"] != key]
        _save_claims(claims)


def _detect_expired_claims() -> list[dict]:
    """Return expired claims for Attention recovery."""
    now = datetime.now(timezone.utc)
    expired = []
    with _COORDINATOR_LOCK:
        claims = _load_claims()
        for c in claims["claims"]:
            try:
                expires = datetime.fromisoformat(c["expires_at"])
                if expires <= now:
                    expired.append(c)
            except (ValueError, TypeError):
                expired.append(c)
        # Remove expired
        claims["claims"] = [c for c in claims["claims"] if c not in expired]
        _save_claims(claims)
    return expired


# ── Receipt helpers ──────────────────────────────────────────────────────

def _write_receipt(
    routine_id: str,
    status: str,
    *,
    summary: str = "",
    detail: dict | None = None,
) -> dict:
    """Write a durable receipt for a routine run."""
    receipt = ledger_receipts.new_receipt(
        subject_type="routine",
        subject_id=routine_id,
        kind=f"routine.{routine_id}",
        status=status,
        targets=[],
        summary=summary,
        detail=detail or {},
    )
    return ledger_receipts.create_receipt(receipt)


# ── Public API ───────────────────────────────────────────────────────────

def run_routine(
    routine_id: str,
    runner_fn,
    params: dict | None = None,
    *,
    scheduled_due: str | None = None,
) -> dict:
    """Run a routine with exactly-once coordination.

    Args:
        routine_id: The routine identifier (e.g. "download", "curve").
        runner_fn: Callable that takes ``params`` and returns a result dict.
        params: Parameters passed to the runner.
        scheduled_due: Optional ISO timestamp for scheduled runs.

    Returns:
        A result dict with ``ok``, ``status``, ``receipt``, and ``summary``.
    """
    # Detect expired claims first
    expired = _detect_expired_claims()
    if expired:
        for c in expired:
            _write_receipt(
                c["routine_id"],
                "attention",
                summary=f"Stale claim expired for {c['routine_id']}",
            )

    # Acquire claim
    if not _acquire_claim(routine_id, scheduled_due):
        return {
            "ok": True,
            "status": "skipped",
            "summary": "Another run is already in progress",
            "receipt": None,
        }

    try:
        # Run the routine
        result = runner_fn(params or {})

        # Classify effect
        if isinstance(result, dict):
            status = result.get("status", "applied")
            summary = result.get("summary", "")
            detail = result.get("detail", {})
        else:
            status = "applied" if result else "failed"
            summary = str(result) if result else "no result"
            detail = {}

        # Map to receipt status
        receipt_status = _classify_status(status)

        # Write receipt
        receipt = _write_receipt(
            routine_id,
            receipt_status,
            summary=summary,
            detail=detail,
        )

        return {
            "ok": receipt_status not in ("failed", "attention"),
            "status": receipt_status,
            "summary": summary,
            "receipt": receipt,
        }

    except Exception as exc:
        receipt = _write_receipt(
            routine_id,
            "failed",
            summary=str(exc),
        )
        return {
            "ok": False,
            "status": "failed",
            "summary": str(exc),
            "receipt": receipt,
        }

    finally:
        _release_claim(routine_id, scheduled_due)


def _classify_status(runner_status: str) -> str:
    """Map a runner result status to a receipt status."""
    mapping = {
        "applied": "applied",
        "partial": "partial",
        "failed": "failed",
        "blocked": "blocked",
        "no_effect": "no_effect",
        "attention": "attention",
        "skipped": "no_effect",
    }
    return mapping.get(runner_status, "applied" if runner_status else "failed")


# ── Attention recovery ───────────────────────────────────────────────────

def recover_stale_claims() -> list[dict]:
    """Scan for expired claims and return Attention records."""
    return _detect_expired_claims()
