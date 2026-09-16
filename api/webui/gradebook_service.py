"""Gradebook business logic extracted from routes/gradebook.py.

Imported by routes/gradebook.py, routes/push.py, and routes/routines.py.
Keeping service functions here breaks circular-import risk and makes them
unit-testable without HTTP.
"""
import json
from pathlib import Path

from api.platform_services import config
from api.operation_ledger import paths as ledger_paths
from api.operation_ledger import storage as ledger_storage

CURVE_EVENT_STORAGE_ERROR = "curve_event_storage_unavailable"
_CURVE_EVENT_VERSION = 1
_CURVE_EVENT_REQUIRED = ("id", "course_id", "assignment_id", "applied_at", "reverted", "students")


class CurveEventStorageError(RuntimeError):
    """Redacted, stable error for unavailable curve-event persistence."""

    def __init__(self):
        super().__init__(CURVE_EVENT_STORAGE_ERROR)


def _validate_curve_event(event):
    if not isinstance(event, dict):
        raise ValueError("curve event must be an object")
    if any(key not in event for key in _CURVE_EVENT_REQUIRED):
        raise ValueError("curve event envelope is incomplete")
    for key in ("id", "course_id", "assignment_id", "applied_at"):
        if not isinstance(event[key], str) or not event[key]:
            raise ValueError("curve event envelope field is invalid")
    if not isinstance(event["reverted"], bool):
        raise ValueError("curve event reverted field is invalid")
    if not isinstance(event["students"], list):
        raise ValueError("curve event students field is invalid")


def _validate_curve_events(events):
    if not isinstance(events, list):
        raise ValueError("curve events must be a list")
    for event in events:
        _validate_curve_event(event)


def _decode_curve_document(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("curve event JSON is invalid") from exc


def _read_versioned_curve_events(path):
    document = _decode_curve_document(Path(path).read_bytes())
    if not isinstance(document, dict) or document.get("version") != _CURVE_EVENT_VERSION:
        raise ValueError("curve event document version is invalid")
    events = document.get("events")
    _validate_curve_events(events)
    return events


def _load_curve_events_unlocked():
    live_path = ledger_paths.curve_events_file()
    if not live_path.exists():
        return []
    return _read_versioned_curve_events(live_path)


def _load_curve_events():
    try:
        with ledger_storage.storage_lock():
            return _load_curve_events_unlocked()
    except CurveEventStorageError:
        raise
    except Exception:
        raise CurveEventStorageError() from None


def _save_curve_events(events):
    try:
        _validate_curve_events(events)
        with ledger_storage.storage_lock():
            _load_curve_events_unlocked()
            live_path = ledger_paths.curve_events_file()
            ledger_storage.atomic_write_json(
                live_path, {"version": _CURVE_EVENT_VERSION, "events": events})
            if _read_versioned_curve_events(live_path) != events:
                raise ValueError("curve event write comparison failed")
    except CurveEventStorageError:
        raise
    except Exception:
        raise CurveEventStorageError() from None


def _apply_curve_model(scored_students, curve_type, settings, points_possible):
    pts = float(points_possible or 100)
    valid = [(s["user_id"], s["student_name"], float(s["score"]))
             for s in scored_students if s.get("score") is not None]
    results = []

    if curve_type == "flat_bump":
        bump = float(settings.get("bump", 0))
        cap = float(settings.get("cap", pts)) if settings.get("cap") not in (None, "") else pts
        do_no_harm = bool(settings.get("do_no_harm", False))
        for uid, name, score in valid:
            new_s = min(score + bump, cap)
            if do_no_harm:
                new_s = max(new_s, score)
            new_s = round(new_s, 2)
            results.append({"user_id": uid, "student_name": name,
                            "original_score": score, "curved_score": new_s,
                            "changed": new_s != score})

    elif curve_type == "target_average":
        if not valid:
            return []
        target = pts * float(settings.get("target_avg_pct", 75)) / 100
        cap = float(settings.get("cap", pts)) if settings.get("cap") not in (None, "") else pts
        do_no_harm = bool(settings.get("do_no_harm", True))
        current_avg = sum(s for _, _, s in valid) / len(valid)
        bump = target - current_avg
        for uid, name, score in valid:
            new_s = min(score + bump, cap)
            if do_no_harm:
                new_s = max(new_s, score)
            new_s = round(new_s, 2)
            results.append({"user_id": uid, "student_name": name,
                            "original_score": score, "curved_score": new_s,
                            "changed": new_s != score})

    elif curve_type == "proportional":
        if not valid:
            return []
        target = pts * float(settings.get("target_avg_pct", 75)) / 100
        cap = float(settings.get("cap", pts)) if settings.get("cap") not in (None, "") else pts
        do_no_harm = bool(settings.get("do_no_harm", True))
        current_avg = sum(s for _, _, s in valid) / len(valid)
        total_lift = (target - current_avg) * len(valid)
        max_score = max(s for _, _, s in valid) or 1
        weights = [max_score - s + 1 for _, _, s in valid]
        total_w = sum(weights)
        for (uid, name, score), w in zip(valid, weights):
            lift = (w / total_w * total_lift) if total_w else 0
            new_s = min(score + lift, cap)
            if do_no_harm:
                new_s = max(new_s, score)
            new_s = round(new_s, 2)
            results.append({"user_id": uid, "student_name": name,
                            "original_score": score, "curved_score": new_s,
                            "changed": new_s != score})

    elif curve_type == "floor_cap":
        floor_pts = float(settings.get("floor", 0))
        cap_pts = float(settings.get("cap", pts)) if settings.get("cap") not in (None, "") else pts
        for uid, name, score in valid:
            new_s = round(max(min(score, cap_pts), floor_pts), 2)
            results.append({"user_id": uid, "student_name": name,
                            "original_score": score, "curved_score": new_s,
                            "changed": new_s != score})
    return results




