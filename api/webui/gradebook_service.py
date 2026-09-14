"""Gradebook business logic extracted from routes/gradebook.py.

Imported by routes/gradebook.py, routes/push.py, and routes/routines.py.
Keeping service functions here breaks circular-import risk and makes them
unit-testable without HTTP.
"""
import json
from pathlib import Path

from api.platform_services import config
from . import deps, school_calendar
from .schooldays import parse_iso_local
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


def _split_for_extra_time(course_id, student_ids, base_due_iso, base_lock_iso=None):
    """Partition a tier's student_ids by the course extra-time roster.

    Returns ``{"ok": True, "standard": [...], "extended": [...]}`` on success.
    On a Calendar failure (unconfigured, invalid, the due/lock date outside
    coverage, or an instructional day in range naming an unloaded Bell
    Schedule) returns ``{"ok": False, "error": ..., "problems": [...]}``
    instead of silently treating every student as standard -- a broken
    calendar must not silently drop extended due dates.
    """
    roster = {str(e["id"]): int(e.get("days", 1))
              for e in config.get_extra_time(course_id)}
    base_due = parse_iso_local(base_due_iso) if base_due_iso else None
    if not roster or not base_due:
        return {"ok": True, "standard": list(student_ids), "extended": []}

    bell_schedules, _problems = deps.load_bell_schedules()
    known_schedule_ids = set(bell_schedules)
    base_lock = parse_iso_local(base_lock_iso) if base_lock_iso else None

    standard, buckets = [], {}
    for sid in student_ids:
        days = roster.get(str(sid))
        if days:
            buckets.setdefault(days, []).append(sid)
        else:
            standard.append(sid)

    extended = []
    for days in sorted(buckets):
        due_at, failure = school_calendar.add_school_days_checked(
            base_due, days, known_schedule_ids)
        if failure is not None:
            return {"ok": False, "error": school_calendar.CALENDAR_REPAIR_MESSAGE,
                    "problems": failure["problems"]}
        ext = {"days": days, "student_ids": buckets[days], "due_at": due_at.isoformat()}
        if base_lock:
            lock_at, failure = school_calendar.add_school_days_checked(
                base_lock, days, known_schedule_ids)
            if failure is not None:
                return {"ok": False, "error": school_calendar.CALENDAR_REPAIR_MESSAGE,
                        "problems": failure["problems"]}
            ext["lock_at"] = lock_at.isoformat()
        extended.append(ext)
    return {"ok": True, "standard": standard, "extended": extended}


def _expand_variants_extra_time(course_id, entries, settings):
    """Bake per-student extra-time overrides into a differentiated plan.

    A tier whose extra-time split fails closed (see _split_for_extra_time)
    keeps its whole-group override and carries an explicit "extra_time_error"
    instead of silently pushing every student on the standard due date.
    """
    try:
        s = json.loads(settings) if settings else {}
    except json.JSONDecodeError:
        s = {}
    base_due, base_lock = s.get("due_at"), s.get("lock_at")
    for e in entries:
        sids = e.get("student_ids") or []
        if not sids:
            continue
        split = _split_for_extra_time(course_id, sids, base_due, base_lock)
        if not split.get("ok", True):
            e["extra_time_error"] = split.get("error")
            continue
        if not split["extended"]:
            continue
        label = e.get("label", "tier")
        overrides = []
        if split["standard"]:
            overrides.append({"student_ids": split["standard"],
                              "title": f"{label} group"})
        for ext in split["extended"]:
            ov = {"student_ids": ext["student_ids"],
                  "title": f"{label} +{ext['days']}d",
                  "due_at": ext["due_at"]}
            if ext.get("lock_at"):
                ov["lock_at"] = ext["lock_at"]
            overrides.append(ov)
        e["overrides"] = overrides
    return entries


# The legacy _sweep_compute was deleted in slice 00c: it unpacked four values
# from schooldays.school_days_late_detail (which returns two) and crashed on
# any late submission. The single sweep-compute owner is
# api/operation_ledger/adapters/sweep.py::_compute_sweep.
