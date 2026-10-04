"""Validated, atomic, machine-local receipt storage."""

import copy
import json
import os
import threading
from pathlib import Path

from . import paths
from api.storage_support import (
    atomic_write_bytes,
    atomic_write_json,
    interprocess_lock,
    quarantine_corrupt_file,
)


VERSION = 1
_LOCK = threading.RLock()


class ReceiptConflictError(ValueError):
    """Raised when an immutable receipt ID already exists."""


class ReceiptSchemaError(ValueError):
    """Raised when a receipt document does not match the supported schema."""


def _empty() -> dict:
    return {"version": VERSION, "receipts": []}


def _validate_receipt(receipt: dict) -> None:
    if not isinstance(receipt, dict) or receipt.get("version") != VERSION:
        raise ReceiptSchemaError("unsupported receipt schema")
    required = ("receipt_id", "subject_type", "subject_id", "kind", "status", "targets")
    if any(not isinstance(receipt.get(name), str) or not receipt.get(name) for name in required[:-1]):
        raise ReceiptSchemaError("receipt envelope is incomplete")
    if receipt.get("subject_type") not in ("operation", "routine"):
        raise ReceiptSchemaError("unsupported receipt subject")
    if receipt.get("status") not in ("applied", "partial", "failed", "blocked", "no_effect"):
        raise ReceiptSchemaError("unsupported receipt status")
    if not isinstance(receipt.get("targets"), list):
        raise ReceiptSchemaError("receipt targets must be a list")


def _validate_document(document: dict) -> None:
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise ReceiptSchemaError("unsupported receipt document version")
    if not isinstance(document.get("receipts"), list):
        raise ReceiptSchemaError("receipt document must contain a list")
    for receipt in document["receipts"]:
        _validate_receipt(receipt)


def _quarantine(path: Path) -> None:
    quarantine_corrupt_file(path, paths.quarantine_dir())


def _read_unlocked() -> dict:
    path = paths.receipts_file()
    if not path.exists():
        return _empty()
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
        _validate_document(document)
        return document
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ReceiptSchemaError):
        _quarantine(path)
        return _empty()


def _atomic_write_unlocked(document: dict) -> None:
    _validate_document(document)
    atomic_write_json(paths.receipts_file(), document)


def read_document() -> dict:
    with _LOCK:
        return copy.deepcopy(_read_unlocked())


def append_receipt(receipt: dict) -> dict:
    with _LOCK:
        _validate_receipt(receipt)
        document = _read_unlocked()
        if any(item.get("receipt_id") == receipt["receipt_id"] for item in document["receipts"]):
            raise ReceiptConflictError("receipt ID already exists")
        document["receipts"].append(copy.deepcopy(receipt))
        _atomic_write_unlocked(document)
        return copy.deepcopy(receipt)


def find_receipt(receipt_id: str) -> dict | None:
    with _LOCK:
        document = _read_unlocked()
        for receipt in document["receipts"]:
            if receipt.get("receipt_id") == receipt_id:
                return copy.deepcopy(receipt)
        return None


# ── Operations document ─────────────────────────────────────────────────

class OperationsSchemaError(ValueError):
    """Raised when an operations document does not match the supported schema."""


def _empty_operations() -> dict:
    return {"version": 1, "operations": []}


def _validate_operations_document(document: dict) -> None:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise OperationsSchemaError("unsupported operations document version")
    if not isinstance(document.get("operations"), list):
        raise OperationsSchemaError("operations document must contain a list")


def _read_operations_unlocked() -> dict:
    path = paths.operations_file()
    if not path.exists():
        return _empty_operations()
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
        _validate_operations_document(document)
        return document
    except (OSError, ValueError, TypeError, json.JSONDecodeError, OperationsSchemaError):
        _quarantine(path)
        return _empty_operations()


def _atomic_write_operations_unlocked(document: dict) -> None:
    _validate_operations_document(document)
    atomic_write_json(paths.operations_file(), document)


def read_operations_document() -> dict:
    with _LOCK:
        return copy.deepcopy(_read_operations_unlocked())


def find_operation(operation_id: str) -> dict | None:
    with _LOCK:
        document = _read_operations_unlocked()
        for op in document["operations"]:
            if op.get("operation_id") == operation_id:
                return copy.deepcopy(op)
        return None


def upsert_operation(operation: dict) -> dict:
    """Insert or replace an operation by operation_id. Returns a deep copy."""
    def _mutator(document):
        found = False
        for i, op in enumerate(document["operations"]):
            if op.get("operation_id") == operation["operation_id"]:
                document["operations"][i] = copy.deepcopy(operation)
                found = True
                break
        if not found:
            document["operations"].append(copy.deepcopy(operation))
        return document

    storage_doc = modify_operations(_mutator)
    for op in storage_doc["operations"]:
        if op.get("operation_id") == operation["operation_id"]:
            return copy.deepcopy(op)
    raise KeyError(f"operation {operation['operation_id']} was not persisted")


# ── Claims document ─────────────────────────────────────────────────────

class ClaimsSchemaError(ValueError):
    """Raised when a claims document does not match the supported schema."""


def _empty_claims() -> dict:
    return {"version": 1, "claims": []}


def _validate_claims_document(document: dict) -> None:
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ClaimsSchemaError("unsupported claims document version")
    if not isinstance(document.get("claims"), list):
        raise ClaimsSchemaError("claims document must contain a list")


def _read_claims_unlocked() -> dict:
    path = paths.claims_file()
    if not path.exists():
        return _empty_claims()
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
        _validate_claims_document(document)
        return document
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ClaimsSchemaError):
        _quarantine(path)
        return _empty_claims()


def _atomic_write_claims_unlocked(document: dict) -> None:
    _validate_claims_document(document)
    atomic_write_json(paths.claims_file(), document)


def upsert_claim(claim: dict) -> dict:
    """Insert or replace a claim by claim_id. Returns a deep copy."""
    def _mutator(document):
        found = False
        for i, c in enumerate(document["claims"]):
            if c.get("claim_id") == claim["claim_id"]:
                document["claims"][i] = copy.deepcopy(claim)
                found = True
                break
        if not found:
            document["claims"].append(copy.deepcopy(claim))
        return document

    claims_doc = modify_claims(_mutator)
    for item in claims_doc["claims"]:
        if item.get("claim_id") == claim["claim_id"]:
            return copy.deepcopy(item)
    raise KeyError(f"claim {claim['claim_id']} was not persisted")


def modify_claims(mutator) -> dict:
    """Atomically read-modify-write the claims document.

    ``mutator`` receives a deep copy of the claims document and must return
    the (possibly modified) document. The lock is held across the entire
    operation, so concurrent callers are serialized.
    """
    with _LOCK:
        with interprocess_lock(paths.ledger_lock_file()):
            document = _read_claims_unlocked()
            result = mutator(document)
            _atomic_write_claims_unlocked(document)
            return result


def modify_operations(mutator) -> dict:
    """Atomically read-modify-write the operations document.

    ``mutator`` receives a deep copy of the operations document and must return
    the (possibly modified) document. The lock is held across the entire
    operation.
    """
    with _LOCK:
        with interprocess_lock(paths.ledger_lock_file()):
            document = _read_operations_unlocked()
            result = mutator(document)
            _atomic_write_operations_unlocked(document)
            return result


def modify_ledger(mutator) -> dict:
    """Atomically read-modify-write operations and claims under one OS lock."""
    with _LOCK:
        with interprocess_lock(paths.ledger_lock_file()):
            operations_doc = _read_operations_unlocked()
            claims_doc = _read_claims_unlocked()
            result = mutator(operations_doc, claims_doc)
            _atomic_write_operations_unlocked(operations_doc)
            _atomic_write_claims_unlocked(claims_doc)
            return result


def find_claim(claim_id: str) -> dict | None:
    with _LOCK:
        document = _read_claims_unlocked()
        for c in document["claims"]:
            if c.get("claim_id") == claim_id:
                return copy.deepcopy(c)
        return None
