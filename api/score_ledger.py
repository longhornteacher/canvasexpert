"""Private, append-only score evidence and recoverable exports.

The configured teacher workspace owns this archive.  CanvasMirror is only a
projection and may be reset without affecting these records.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from api import local_runtime
from api.platform_services import workspace
from api.shared_storage import create_json_exclusive, assert_store_readable

SCHEMA_VERSION = 1
_EVENT_FIELDS = (
    "source", "action", "course_id", "assignment_id", "student_id", "attempt",
    "submission_digest", "raw_score", "entered_score", "observed_entered_score", "canvas_score",
    "points_deducted", "late_status", "late_days", "curve_rule_id", "feedback",
    "stage_id", "session_id", "operation_id", "origin_event_id", "old_canvas_score",
    "new_canvas_score", "old_entered_score", "new_entered_score", "actor",
    "old_points_deducted", "new_points_deducted", "old_late_status", "new_late_status",
    "old_late_days", "new_late_days", "observation_at",
    "corrects_event_id",
)


class ScoreLedgerError(RuntimeError):
    """A durable evidence boundary failed closed."""


def _root(root=None) -> Path:
    base = root if root is not None else workspace.workspace_root()
    if not base:
        raise ScoreLedgerError("score_ledger_workspace_unavailable")
    result = Path(workspace.archive_dir(str(base))) / "Score Ledger"
    return result


def _scope_dir(course_id: str, assignment_id: str, root=None) -> Path:
    if not str(course_id or "").strip() or not str(assignment_id or "").strip():
        raise ScoreLedgerError("score_ledger_invalid_scope")
    value = workspace.bounded_join(str(_root(root)), "Events", workspace.safe_id(course_id),
                                   workspace.safe_id(assignment_id), max_path=240)
    return Path(value)


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _device_label() -> str:
    # machine_id contains a host name. Only its digest is durable evidence.
    return hashlib.sha256(local_runtime.machine_id().encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_scope(course_id: str, assignment_id: str, root=None) -> list[dict]:
    directory = _scope_dir(course_id, assignment_id, root)
    if not directory.exists():
        return []
    assert_store_readable(directory, root=root)
    events = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScoreLedgerError("score_ledger_corrupt") from exc
        if (not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION
                or record.get("event_id") != path.stem):
            raise ScoreLedgerError("score_ledger_corrupt")
        _validate_event(record, course_id, assignment_id)
        events.append(record)
    return events


def _validate_event(record: dict, course_id: str, assignment_id: str) -> None:
    if (str(record.get("course_id") or "") != str(course_id)
            or str(record.get("assignment_id") or "") != str(assignment_id)
            or not record.get("source") or not record.get("action")
            or not record.get("timestamp") or not record.get("device_id")):
        raise ScoreLedgerError("score_ledger_corrupt")
    if record.get("student_id") is None and record.get("source") not in {"canvas_external", "mirror_observed"}:
        raise ScoreLedgerError("score_ledger_corrupt")
    for key in ("raw_score", "entered_score", "observed_entered_score", "canvas_score", "points_deducted", "late_days",
                "old_canvas_score", "new_canvas_score", "old_entered_score", "new_entered_score",
                "old_late_days", "new_late_days"):
        value = record.get(key)
        if isinstance(value, bool):
            raise ScoreLedgerError("score_ledger_corrupt")
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ScoreLedgerError("score_ledger_corrupt") from exc
            if not math.isfinite(number):
                raise ScoreLedgerError("score_ledger_corrupt")
    feedback = record.get("feedback")
    digest = hashlib.sha256(str(feedback or "").encode("utf-8")).hexdigest()
    if record.get("feedback_sha256") != digest:
        raise ScoreLedgerError("score_ledger_corrupt")
    integrity = {k: v for k, v in record.items() if k != "integrity_sha256"}
    if record.get("integrity_sha256") != hashlib.sha256(_canonical(integrity)).hexdigest():
        raise ScoreLedgerError("score_ledger_corrupt")


def list_events(course_id: str, assignment_id: str, *, root=None) -> list[dict]:
    """Read validated private events for one assignment."""
    return _read_scope(str(course_id), str(assignment_id), root)


def validate_scope(course_id: str, assignment_id: str, *, root=None) -> list[dict]:
    """Validate a scope once before a multi-row operation begins."""
    return _read_scope(str(course_id), str(assignment_id), root)


def append_event(record: dict, *, idempotency_key: str, root=None) -> dict:
    """Publish one immutable event; a repeated key returns its original event."""
    if not isinstance(record, dict) or not idempotency_key:
        raise ScoreLedgerError("score_ledger_invalid_event")
    event = {key: record.get(key) for key in _EVENT_FIELDS
             if key != "corrects_event_id" or record.get(key) not in (None, "")}
    if not str(event.get("course_id") or "").strip() or not str(event.get("assignment_id") or "").strip():
        raise ScoreLedgerError("score_ledger_invalid_scope")
    event.update({"schema_version": SCHEMA_VERSION, "timestamp": str(record.get("timestamp") or _timestamp()),
                  "device_id": _device_label()})
    if event["source"] not in {"ce_stage", "ce_apply", "ce_curve", "ce_adjustment",
                                "canvas_external", "mirror_observed"}:
        raise ScoreLedgerError("score_ledger_invalid_source")
    for key in ("raw_score", "entered_score", "observed_entered_score", "canvas_score", "points_deducted", "late_days",
                "old_canvas_score", "new_canvas_score", "old_entered_score", "new_entered_score",
                "old_late_days", "new_late_days"):
        value = event.get(key)
        if isinstance(value, bool):
            raise ScoreLedgerError("score_ledger_invalid_number")
        if value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ScoreLedgerError("score_ledger_invalid_number") from exc
            if not math.isfinite(number):
                raise ScoreLedgerError("score_ledger_invalid_number")
    feedback = event.get("feedback")
    event["feedback_sha256"] = hashlib.sha256(str(feedback or "").encode("utf-8")).hexdigest()
    event_id = hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()
    event["event_id"] = event_id
    event["logical_event_key"] = str(idempotency_key)
    event["integrity_sha256"] = hashlib.sha256(_canonical(event)).hexdigest()
    _validate_event(event, str(event["course_id"]), str(event["assignment_id"]))
    directory = _scope_dir(str(event.get("course_id") or ""), str(event.get("assignment_id") or ""), root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{event_id}.json"
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ScoreLedgerError("score_ledger_corrupt") from exc
        _validate_event(existing, str(event["course_id"]), str(event["assignment_id"]))
        ignored = {"timestamp", "device_id", "integrity_sha256"}
        if ({k: v for k, v in existing.items() if k not in ignored}
                != {k: v for k, v in event.items() if k not in ignored}):
            raise ScoreLedgerError("score_ledger_conflict")
        return existing
    if not create_json_exclusive(target, event, root=root):
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ScoreLedgerError("score_ledger_corrupt") from exc
        ignored = {"timestamp", "device_id", "integrity_sha256"}
        if ({k: v for k, v in existing.items() if k not in ignored}
                != {k: v for k, v in event.items() if k not in ignored}):
            raise ScoreLedgerError("score_ledger_conflict")
        return existing
    return event


def flush_exports(course_id: str, assignment_id: str, *, root=None) -> dict:
    try:
        return _publish_exports(str(course_id), str(assignment_id), root)
    except Exception as exc:
        raise ScoreLedgerError("score_ledger_export_failed") from exc


def _publish_exports(course_id: str, assignment_id: str, root=None) -> dict:
    events = _read_scope(course_id, assignment_id, root)
    base = Path(workspace.bounded_join(str(_root(root)), "Exports",
        workspace.safe_id(course_id), workspace.safe_id(assignment_id), max_path=240))
    base.mkdir(parents=True, exist_ok=True)
    rule_events = list_rule_events(course_id, root=root)
    json_doc = {"schema_version": SCHEMA_VERSION, "course_id": course_id,
                "assignment_id": assignment_id, "events": events,
                "rule_events": rule_events}
    csv_buffer = io.StringIO(newline="")
    rule_fields = ["record_type", "rule_id", "rule_action", "rule_scope",
                   "rule_assignment_id", "rule_formula"]
    writer = csv.DictWriter(csv_buffer, fieldnames=["schema_version", "event_id", "timestamp", "device_id", *_EVENT_FIELDS, "feedback_sha256", *rule_fields], extrasaction="ignore")
    writer.writeheader()
    writer.writerows({"record_type": "event", **row} for row in events)
    writer.writerows({"record_type": "rule", "schema_version": SCHEMA_VERSION,
        "event_id": row.get("event_id"), "timestamp": row.get("timestamp"),
        "device_id": row.get("device_id"), "rule_id": row.get("rule_id"),
        "rule_action": row.get("action"), "rule_scope": row.get("scope"),
        "rule_assignment_id": row.get("assignment_id"),
        "rule_formula": json.dumps(row.get("formula"), sort_keys=True)} for row in rule_events)
    for ext, payload in (("json", _canonical(json_doc)), ("csv", csv_buffer.getvalue().encode("utf-8-sig"))):
        digest = hashlib.sha256(payload).hexdigest()
        path = base / f"{digest}.{ext}"
        if ext == "json":
            create_json_exclusive(path, json_doc, root=root)
        elif not path.exists():
            _create_bytes_exclusive(path, payload, root=root)
    return {"schema_version": SCHEMA_VERSION, "json_digest": hashlib.sha256(_canonical(json_doc)).hexdigest()}


def _create_bytes_exclusive(path: Path, payload: bytes, *, root=None) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_name, path)
            return True
        except FileExistsError:
            return False
    finally:
        try:
            os.remove(temp_name)
        except FileNotFoundError:
            pass


def append_rule_event(course_id: str, rule: dict, *, idempotency_key: str, root=None) -> dict:
    """Append immutable rule lifecycle evidence in the course rule journal."""
    if not str(course_id or "").strip():
        raise ScoreLedgerError("score_ledger_invalid_scope")
    directory = Path(workspace.bounded_join(str(_root(root)), "Rules",
                                            workspace.safe_id(course_id), max_path=240))
    directory.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256((str(idempotency_key) + "\0" + hashlib.sha256(_canonical(rule)).hexdigest()).encode()).hexdigest()
    event = {"schema_version": SCHEMA_VERSION, "event_id": identity, "course_id": str(course_id),
             "timestamp": _timestamp(), "device_id": _device_label(), **rule}
    event["integrity_sha256"] = hashlib.sha256(_canonical(event)).hexdigest()
    path = directory / f"{identity}.json"
    if not create_json_exclusive(path, event, root=root):
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ScoreLedgerError("score_ledger_corrupt") from exc
        existing_integrity = {k: v for k, v in existing.items() if k != "integrity_sha256"}
        if existing.get("integrity_sha256") != hashlib.sha256(_canonical(existing_integrity)).hexdigest():
            raise ScoreLedgerError("score_ledger_corrupt")
        if {k: v for k, v in existing.items() if k not in {"timestamp", "device_id", "integrity_sha256"}} != {k: v for k, v in event.items() if k not in {"timestamp", "device_id", "integrity_sha256"}}:
            raise ScoreLedgerError("score_ledger_conflict")
        return existing
    flush_course_exports(str(course_id), root=root)
    return event


def list_rule_events(course_id: str, *, root=None) -> list[dict]:
    directory = Path(workspace.bounded_join(str(_root(root)), "Rules",
                                            workspace.safe_id(course_id), max_path=240))
    if not directory.exists():
        return []
    assert_store_readable(directory, root=root)
    result = []
    for path in sorted(directory.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScoreLedgerError("score_ledger_corrupt") from exc
        integrity = {k: v for k, v in row.items() if k != "integrity_sha256"}
        if (row.get("schema_version") != SCHEMA_VERSION or row.get("event_id") != path.stem
                or str(row.get("course_id") or "") != str(course_id)
                or not row.get("rule_id") or row.get("action") not in {"create", "deactivate", "exclude_assignment"}
                or row.get("integrity_sha256") != hashlib.sha256(_canonical(integrity)).hexdigest()):
            raise ScoreLedgerError("score_ledger_corrupt")
        result.append(row)
    return result


def flush_course_exports(course_id: str, *, root=None) -> None:
    event_root = _root(root) / "Events" / workspace.safe_id(course_id)
    if not event_root.exists():
        return
    for assignment in event_root.iterdir():
        if assignment.is_dir():
            flush_exports(str(course_id), assignment.name, root=root)


def assignment_ids(course_id: str, *, root=None) -> list[str]:
    directory = Path(workspace.bounded_join(str(_root(root)), "Events",
                                            workspace.safe_id(course_id), max_path=240))
    if not directory.exists():
        return []
    assert_store_readable(directory, root=root)
    return sorted(path.name for path in directory.iterdir() if path.is_dir())
