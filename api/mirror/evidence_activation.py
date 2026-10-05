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
import re

from api.mirror.evidence_paths import local_source_root
from api.mirror.evidence_schema import canonical_bytes

ACTIVATION_SCHEMA_VERSION = 1
ACTIVATION_STATES = frozenset({"inactive", "active", "rolled_back", "repair_required"})
REQUIRED_ACTIVATION_SCOPES = frozenset({"course.context", "course.roster",
    "course.sections", "course.assignments", "assignment.submissions"})


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
            return ActivationState("repair_required", ACTIVATION_SCHEMA_VERSION, {},
                                   reason="invalid_checkpoint")
        return ActivationState(document["state"], document["schema_version"],
                               document.get("coverage") or {},
                               document.get("activated_at"), document.get("rolled_back_at"),
                               document.get("reason"))
    except (OSError, ValueError, TypeError):
        return ActivationState("repair_required", ACTIVATION_SCHEMA_VERSION, {},
                               reason="invalid_checkpoint")


def _write_verified_activation(*, source_key: str, workspace_root, coverage: dict,
                               activated_at: str) -> ActivationState:
    """Persist coverage that has already passed the CE-owned verification path."""
    courses = coverage.get("courses") if isinstance(coverage, dict) else None
    valid = isinstance(courses, dict) and bool(courses)
    if valid:
        for course_id, proof in courses.items():
            if (not isinstance(course_id, str) or not course_id.isdecimal()
                    or not isinstance(proof, dict)
                    or proof.get("verification_state") != "verified"
                    or proof.get("verified_import") is not True
                    or not re.fullmatch(r"[0-9a-f]{64}", str(proof.get("index_revision") or ""))
                    or not isinstance(proof.get("required_scopes"), list)
                    or not REQUIRED_ACTIVATION_SCOPES.issubset(set(proof["required_scopes"]))):
                valid = False
                break
    if not valid:
        raise ValueError("activation_requires_coverage")
    document = {"schema_version": ACTIVATION_SCHEMA_VERSION, "state": "active",
                "coverage": coverage, "activated_at": activated_at}
    _write(_path(source_key, workspace_root), document)
    return read_activation(source_key=source_key, workspace_root=workspace_root)


def activate_read_authority(*, source_key: str, workspace_root, report_path,
                            activated_at: str) -> ActivationState:
    """Activate only from a persisted migration report and current safe-index coverage."""
    from api.mirror.evidence_activation_proof import build_activation_coverage
    from api.mirror.evidence_index import EvidenceIndex
    from api.mirror.evidence_paths import local_source_root
    from api.mirror.service import rebuild_evidence_index

    coverage = build_activation_coverage(report_path=report_path,
        source_key=source_key, workspace_root=workspace_root)
    rebuilt = rebuild_evidence_index(root=workspace_root, source_key=source_key)
    if rebuilt.get("state") != "current":
        raise ValueError("activation_index_not_current")
    index = EvidenceIndex(local_source_root(source_key, workspace_root) / "query.sqlite3")
    with index.read_connection() as db:
        revision_row = db.execute("SELECT value FROM index_metadata WHERE key='revision'").fetchone()
        revision = revision_row[0] if revision_row else None
        if not re.fullmatch(r"[0-9a-f]{64}", str(revision or "")):
            raise ValueError("activation_index_revision_missing")
        for course_id, proof in coverage["courses"].items():
            scopes = db.execute("SELECT scope,status,membership_complete,pending_commits,ambiguous_entities "
                "FROM scope_coverage WHERE source_key=? AND course_id=?", (source_key, course_id)).fetchall()
            ready = {row["scope"] for row in scopes if row["status"] == "ready"
                and row["membership_complete"] and not json.loads(row["pending_commits"])
                and not json.loads(row["ambiguous_entities"])}
            submission_scopes = [row for row in scopes if row["scope"] == "assignment.submissions"]
            if (not REQUIRED_ACTIVATION_SCOPES.issubset(ready)
                    or not submission_scopes
                    or any(row["status"] != "ready" or not row["membership_complete"]
                           or json.loads(row["pending_commits"])
                           or json.loads(row["ambiguous_entities"]) for row in submission_scopes)):
                raise ValueError("activation_index_coverage_incomplete")
            proof["index_revision"] = revision
            proof["required_scopes"] = sorted(REQUIRED_ACTIVATION_SCOPES)
    return _write_verified_activation(source_key=source_key,
        workspace_root=workspace_root, coverage=coverage, activated_at=activated_at)


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
