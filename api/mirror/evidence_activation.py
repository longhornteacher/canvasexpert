"""Machine-local activation checkpoint and resumable evidence-work recovery.

One explicit activation record per workspace/source replaces a scattered
feature-flag framework. Until activation, the old production reads remain in
place; after activation, no invisible old-cache fallback may report fresh data.
A failed index rebuilds from immutable evidence; a failed new read presents
last-good or a typed repair. Rollback preserves new evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_schema import canonical_bytes

ACTIVATION_SCHEMA_VERSION = 1
ACTIVATION_STATES = frozenset({"inactive", "active", "rolled_back"})


@dataclass(frozen=True)
class ActivationState:
    state: str
    schema_version: int
    coverage: dict
    activated_at: str | None = None
    rolled_back_at: str | None = None
    reason: str | None = None


def _path(source_key: str, workspace_root) -> Path:
    return local_source_root(source_key, workspace_root) / "activation.v1.json"


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(canonical_bytes(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_activation(*, source_key: str, workspace_root) -> ActivationState:
    path = _path(source_key, workspace_root)
    if not path.exists():
        return ActivationState("inactive", ACTIVATION_SCHEMA_VERSION, {})
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if (document.get("schema_version") != ACTIVATION_SCHEMA_VERSION
                or document.get("state") not in ACTIVATION_STATES):
            return ActivationState("inactive", ACTIVATION_SCHEMA_VERSION, {})
        return ActivationState(document["state"], document["schema_version"],
                               document.get("coverage") or {},
                               document.get("activated_at"), document.get("rolled_back_at"),
                               document.get("reason"))
    except (OSError, ValueError, TypeError):
        return ActivationState("inactive", ACTIVATION_SCHEMA_VERSION, {})


def activate_read_authority(*, source_key: str, workspace_root, coverage: dict,
                            activated_at: str) -> ActivationState:
    """Activate the new read owner only after verified coverage is recorded."""
    if not isinstance(coverage, dict) or not coverage:
        raise ValueError("activation_requires_coverage")
    document = {"schema_version": ACTIVATION_SCHEMA_VERSION, "state": "active",
                "coverage": coverage, "activated_at": activated_at}
    _write(_path(source_key, workspace_root), document)
    return read_activation(source_key=source_key, workspace_root=workspace_root)


def rollback_read_authority(*, source_key: str, workspace_root, reason: str,
                            rolled_back_at: str) -> ActivationState:
    """Return to the previous read owner without deleting any new evidence."""
    current = read_activation(source_key=source_key, workspace_root=workspace_root)
    document = {"schema_version": ACTIVATION_SCHEMA_VERSION, "state": "rolled_back",
                "coverage": current.coverage, "activated_at": current.activated_at,
                "rolled_back_at": rolled_back_at, "reason": str(reason or "")[:120]}
    _write(_path(source_key, workspace_root), document)
    return read_activation(source_key=source_key, workspace_root=workspace_root)


def recover_evidence_work(*, source_key: str, workspace_root, jobs, cache,
                          publisher_for, recover_original, run_adapter,
                          writer_key: str, run_id: str,
                          capture_chunk=None, extraction_limit: int = 20) -> dict:
    """Resume interrupted attachment capture and extraction after a restart.

    Jobs survive in the private local control store; a completed validated
    original is reused rather than re-downloaded. One failure never stops
    siblings. Returns aggregate counters only.
    """
    from api.mirror.evidence_extraction import extract_captured_attachments
    capture = {"processed": 0, "captured": 0, "failed": 0, "skipped": 0}
    if capture_chunk is not None:
        try:
            capture = capture_chunk()
        except Exception:
            capture = {"processed": 0, "captured": 0, "failed": 0, "skipped": 0,
                       "error": "capture_failed"}
    outcome = extract_captured_attachments(
        publisher_for=publisher_for, jobs=jobs, cache=cache,
        recover_original=recover_original, run_adapter=run_adapter,
        writer_key=writer_key, run_id=run_id, limit=extraction_limit)
    return {"capture": capture,
            "extraction": {"processed": outcome.processed, "published": outcome.published,
                           "cached": outcome.cached, "failed": outcome.failed,
                           "gaps": list(outcome.gaps)},
            "remaining": jobs.summary()["pending"]}
