"""Allowlisted local operational events for support diagnostics.

This is intentionally separate from the application's general logger. Records
contain machine codes and aggregate counts only; workflow data never belongs in
this file.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from api import __version__


_EVENT_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ERROR_CLASS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_PRIORITY_RE = re.compile(r"^(preflight|post_write|focus|manual|background|concluded)$")
_OUTCOMES = {"ok", "blocked", "failed", "unconfigured", "refused", "error"}
_MAX_BYTES = 1_048_576
_BACKUP_COUNT = 3
_ALLOWED_KEYS = {
    "timestamp", "app_version", "event", "outcome", "duration_ms",
    "status_code", "error_class", "count", "logical_count", "physical_count",
    "byte_count", "retry_count", "transport_ms", "queue_wait_ms", "priority",
    "scope", "yield_count", "cancel_count",
}
_REQUIRED_KEYS = {"timestamp", "app_version", "event", "outcome"}

_LOCK = threading.RLock()
_LOGGER = logging.getLogger("canvasexpert.operational")
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False
_HANDLER: RotatingFileHandler | None = None
_HANDLER_PATH: Path | None = None

# Separate logger/handler for errors.log (B3). Deliberately NOT the same
# logger as _LOGGER above: this file carries raw traceback text -- it is
# not JSON, not key-allowlisted, and must never be treated as
# safe-by-construction the way an operations.jsonl record is. It may
# contain arbitrary strings from anywhere in the process (exception
# messages, repr'd values, absolute paths), so it is excluded from any
# automatic upload path and only ever leaves the machine via the support
# bundle, which requires the teacher's own explicit action.
_ERROR_LOGGER = logging.getLogger("canvasexpert.errors")
_ERROR_LOGGER.setLevel(logging.ERROR)
_ERROR_LOGGER.propagate = False
_ERROR_HANDLER: RotatingFileHandler | None = None
_ERROR_HANDLER_PATH: Path | None = None


def _log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path(tempfile.gettempdir())
    return root / "CanvasExpert" / "Logs" / "operations.jsonl"


def _error_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path(tempfile.gettempdir())
    return root / "CanvasExpert" / "Logs" / "errors.log"


def _nonnegative_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _status_code(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise ValueError("status_code must be an integer from 100 through 599")
    return value


def _error_class_name(value) -> str:
    name = value.__name__ if isinstance(value, type) else str(value)
    if not _ERROR_CLASS_RE.fullmatch(name):
        raise ValueError("error_class must be a class name")
    return name


def _valid_record(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    if not _REQUIRED_KEYS <= set(record) or not set(record) <= _ALLOWED_KEYS:
        return False
    if not isinstance(record.get("timestamp"), str) or not record["timestamp"]:
        return False
    if not isinstance(record.get("app_version"), str) or not record["app_version"]:
        return False
    if not isinstance(record.get("event"), str) or not _EVENT_RE.fullmatch(record["event"]):
        return False
    if record.get("outcome") not in _OUTCOMES:
        return False
    for key in ("duration_ms", "count", "logical_count", "physical_count", "byte_count",
                "retry_count", "transport_ms", "queue_wait_ms", "yield_count", "cancel_count"):
        if key in record and (isinstance(record[key], bool) or
                              not isinstance(record[key], int) or record[key] < 0):
            return False
    if "status_code" in record and (
        isinstance(record["status_code"], bool)
        or not isinstance(record["status_code"], int)
        or not 100 <= record["status_code"] <= 599
    ):
        return False
    if "error_class" in record and (
        not isinstance(record["error_class"], str)
        or not _ERROR_CLASS_RE.fullmatch(record["error_class"])
    ):
        return False
    if "scope" in record and (not isinstance(record["scope"], str) or
                               not _SCOPE_RE.fullmatch(record["scope"])):
        return False
    if "priority" in record and (not isinstance(record["priority"], str) or
                                  not _PRIORITY_RE.fullmatch(record["priority"])):
        return False
    return True


def _handler_for(path: Path) -> RotatingFileHandler:
    global _HANDLER, _HANDLER_PATH
    if _HANDLER is not None and _HANDLER_PATH == path:
        return _HANDLER
    if _HANDLER is not None:
        _LOGGER.removeHandler(_HANDLER)
        _HANDLER.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
    _HANDLER = handler
    _HANDLER_PATH = path
    return handler


def _error_handler_for(path: Path) -> RotatingFileHandler:
    global _ERROR_HANDLER, _ERROR_HANDLER_PATH
    if _ERROR_HANDLER is not None and _ERROR_HANDLER_PATH == path:
        return _ERROR_HANDLER
    if _ERROR_HANDLER is not None:
        _ERROR_LOGGER.removeHandler(_ERROR_HANDLER)
        _ERROR_HANDLER.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _ERROR_LOGGER.addHandler(handler)
    _ERROR_HANDLER = handler
    _ERROR_HANDLER_PATH = path
    return handler


def write_traceback(text: str) -> None:
    """Append a formatted traceback (or other raw diagnostic text) to the
    rotating error log, beside operations.jsonl. Best-effort, matching
    emit()'s own never-raise contract for its I/O -- diagnostics must never
    break the caller. Unlike emit(), this takes an arbitrary string with no
    key allowlist or JSON validation; see the module note on _ERROR_LOGGER."""
    if not text:
        return None
    try:
        with _LOCK:
            handler = _error_handler_for(_error_log_path())
            _ERROR_LOGGER.error(text.rstrip("\n"))
            handler.flush()
    except Exception:
        return None


def tail_traceback_text(max_bytes: int = 200_000) -> str:
    """Return up to the last `max_bytes` of errors.log, or "" if it doesn't
    exist yet. Read as raw text, not parsed -- this file is not JSON."""
    max_bytes = _nonnegative_int(max_bytes, "max_bytes")
    path = _error_log_path()
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def emit(
    event: str,
    outcome: str,
    *,
    duration_ms: int | None = None,
    status_code: int | None = None,
    error_class: type[BaseException] | str | None = None,
    count: int | None = None,
    logical_count: int | None = None,
    physical_count: int | None = None,
    byte_count: int | None = None,
    retry_count: int | None = None,
    transport_ms: int | None = None,
    queue_wait_ms: int | None = None,
    priority: str | None = None,
    scope: str | None = None,
    yield_count: int | None = None,
    cancel_count: int | None = None,
) -> None:
    """Write one validated, privacy-minimized event if local logging permits."""
    if not isinstance(event, str) or not _EVENT_RE.fullmatch(event):
        raise ValueError("event must be a lower-case machine code")
    if outcome not in _OUTCOMES:
        raise ValueError("unsupported operational outcome")
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "app_version": __version__,
        "event": event,
        "outcome": outcome,
    }
    if duration_ms is not None:
        record["duration_ms"] = _nonnegative_int(duration_ms, "duration_ms")
    if status_code is not None:
        record["status_code"] = _status_code(status_code)
    if error_class is not None:
        record["error_class"] = _error_class_name(error_class)
    if count is not None:
        record["count"] = _nonnegative_int(count, "count")
    for key, value in {
        "logical_count": logical_count, "physical_count": physical_count,
        "byte_count": byte_count, "retry_count": retry_count, "transport_ms": transport_ms,
        "queue_wait_ms": queue_wait_ms, "yield_count": yield_count, "cancel_count": cancel_count,
    }.items():
        if value is not None:
            record[key] = _nonnegative_int(value, key)
    if priority is not None:
        if not isinstance(priority, str) or not _PRIORITY_RE.fullmatch(priority):
            raise ValueError("unsupported operational priority")
        record["priority"] = priority
    if scope is not None:
        if not isinstance(scope, str) or not _SCOPE_RE.fullmatch(scope):
            raise ValueError("scope must be a safe machine code")
        record["scope"] = scope
    if not _valid_record(record):
        raise ValueError("operational record failed validation")
    try:
        with _LOCK:
            handler = _handler_for(_log_path())
            _LOGGER.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handler.flush()
    except Exception:
        # Diagnostics are strictly best-effort and must never break a workflow.
        return None


def tail(max_records: int = 500) -> list[dict]:
    """Return the newest valid records from the current JSONL log."""
    max_records = _nonnegative_int(max_records, "max_records")
    if max_records == 0:
        return []
    path = _log_path()
    try:
        with path.open(encoding="utf-8") as handle:
            records = []
            for line in handle:
                try:
                    record = json.loads(line)
                except (OSError, TypeError, ValueError):
                    continue
                if _valid_record(record):
                    records.append(record)
        return records[-max_records:]
    except OSError:
        return []
