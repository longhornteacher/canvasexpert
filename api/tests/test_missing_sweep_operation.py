"""Operation Ledger laws and synthetic examples for the missing-work sweep."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

from api import freshness_policy, missing_sweep
from api.mirror import read_service
from api.operation_ledger import models, receipts
from api.operation_ledger.adapters import missing_fill as adapter_module
from api.operation_ledger.adapters.missing_fill import MissingFillAdapter
from api.platform_services import canvas_client, config


_SYNCED_AT = "2026-09-26T20:00:00Z"


class _FixedClock(datetime):
    """Pins ``freshness_policy``'s wall clock to exactly ``_SYNCED_AT`` so the
    mirror-freshness gate never depends on the real time a test happens to run."""
    fixed = datetime(2026, 9, 26, 20, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed if tz is None else cls.fixed.astimezone(tz)


class FakeVault:
    def __init__(self):
        self.by_id = {"student-1": "Pikachu", "student-2": "Eevee",
                      "student-3": "Snorlax"}

    @contextmanager
    def transaction(self):
        yield self

    def get_or_assign(self, canvas_id, *args, **kwargs):
        return self.by_id[str(canvas_id)]


class FakeContext:
    def __init__(self):
        self.before_send_calls = []
        self.checkpoints = []

    def before_send(self, step_key, payload_digest):
        self.before_send_calls.append((step_key, payload_digest))
        return {"step_key": step_key, "state": "claimed",
                "payload_digest": payload_digest}

    def checkpoint_step(self, step, **kwargs):
        saved = copy.deepcopy(step)
        saved.update(kwargs)
        self.checkpoints.append(saved)
        return saved


def _add_school_days(start: date, count: int, no_school_dates=()) -> date:
    excluded = set(no_school_dates)
    current = start
    added = 0
    while added < count:
        current = current + timedelta(days=1)
        if current.weekday() < 5 and current.isoformat() not in excluded:
            added += 1
    return current


class FakeCanvas:
    """A course-wide fake keyed by ``"assignment_id:user_id"`` submission rows,
    plus one live assignment record."""

    def __init__(self, assignment: dict, rows: dict[str, dict]):
        self.assignment = assignment
        self.rows = rows
        self.puts = []
        self.reject_keys: set[str] = set()
        self.uncertain_keys: set[str] = set()

    @staticmethod
    def _row_key(path: str) -> str:
        parts = path.split("/")
        assignment_id = parts[parts.index("assignments") + 1]
        user_id = parts[parts.index("submissions") + 1]
        return f"{assignment_id}:{user_id}"

    def get(self, path, params=None, timeout=20):
        if path.endswith(f"/assignments/{self.assignment['id']}"):
            return copy.deepcopy(self.assignment), None
        if "/submissions/" in path:
            return copy.deepcopy(self.rows.get(self._row_key(path))), None
        return None, "missing"

    def send(self, method, path, payload, timeout=30):
        key = self._row_key(path)
        self.puts.append((method, path, copy.deepcopy(payload)))
        if key in self.uncertain_keys:
            return None, "Connection timeout"
        if key in self.reject_keys:
            return None, "HTTP 400: assignment is in a closed grading period"
        posted = payload["submission"]["posted_grade"]
        entered = None if posted == "" else float(posted)
        current = self.rows.get(key, {})
        self.rows[key] = {
            **current,
            "entered_score": entered, "score": entered,
            "late_policy_status": payload["submission"]["late_policy_status"],
        }
        return copy.deepcopy(self.rows[key]), None


def _mirror_scope(records: list[dict]) -> dict:
    return {"records": records, "last_success_at": _SYNCED_AT, "state": "current"}


def test_preview_to_apply_verifies_the_sweep_then_undo_returns_it_to_blank(
    monkeypatch, grading_policy_files,
):
    """Example, once: preview -> apply -> verify (criterion 1), then undo
    (criterion 6), against a fake Canvas exercising the real mirror-prefilter
    plus live-per-assignment-GET discovery path."""
    due = date(2026, 8, 3)
    no_school_dates: list[str] = []
    today = _add_school_days(due, 16, no_school_dates)  # exactly at the 15-day threshold + 1 slack

    assignment = {
        "id": "assignment-1", "name": "Essay", "published": True,
        "grading_type": "points", "points_possible": 25,
        "submission_types": ["online_text_entry"], "is_quiz_lti_assignment": False,
        "group_category_id": None, "grade_group_students_individually": False,
        "in_closed_grading_period": False,
    }
    row = {"workflow_state": "unsubmitted", "score": None, "entered_score": None,
           "excused": False, "missing": True, "late_policy_status": None,
           "custom_grade_status_id": None}
    canvas = FakeCanvas(assignment, {"assignment-1:student-1": row})

    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    # Grading policy and no-school dates are now the two plain workspace
    # files (grading-policy-contract.md section 4); no-school dates stays
    # empty for this test simply by leaving Holidays.csv unwritten.
    grading_policy_files.policy(floor_percent=30, missing_percent=20,
                                sweep_after_school_days=15)
    monkeypatch.setattr(config, "get_extra_time", lambda course_id: [])
    monkeypatch.setattr(read_service, "private_roster",
                        lambda course_id, max_age_hours=None: _mirror_scope(
                            [{"id": "student-1"}]))
    monkeypatch.setattr(read_service, "private_assignments",
                        lambda course_id, max_age_hours=None: _mirror_scope([{
                            "id": "assignment-1", "name": "Essay", "published": True,
                            "due_at": f"{due.isoformat()}T12:00:00Z",
                        }]))
    monkeypatch.setattr(read_service, "private_submissions",
                        lambda course_id, max_age_hours=None: _mirror_scope([{
                            "assignment_id": "assignment-1", "user_id": "student-1",
                            "workflow_state": "unsubmitted", "score": None,
                            "excused": False, "missing": True,
                            "cached_due_date": f"{due.isoformat()}T12:00:00Z",
                        }]))
    monkeypatch.setattr(adapter_module, "_today_local_date", lambda: today)
    monkeypatch.setattr(freshness_policy, "datetime", _FixedClock)
    monkeypatch.setattr(canvas_client, "canvas_get", canvas.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", canvas.send)
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed",
                        lambda _course: None)
    monkeypatch.setattr(missing_sweep, "_vault", lambda: FakeVault())

    preview = missing_sweep.preview_missing_sweep("course-1")
    assert preview["ok"] is True
    assert preview["preview"]["row_count"] == 1
    assert preview["preview"]["assignments"][0]["missing_value"] == 5

    applied = missing_sweep.apply_missing_sweep(
        preview["operation_id"], preview["batch_id"], preview["review_digest"])
    assert applied["ok"] is True
    assert applied["counts"] == {"filled": 1, "skipped_changed": 0,
                                 "failed": 0, "unverified": 0}
    method, _path, request = canvas.puts[0]
    assert method == "PUT"
    assert request["submission"]["posted_grade"] == "5"
    assert request["submission"]["late_policy_status"] == "missing"
    assert canvas.rows["assignment-1:student-1"]["entered_score"] == 5
    assert canvas.rows["assignment-1:student-1"]["late_policy_status"] == "missing"
    detail = receipts.get_receipt(applied["receipt_id"])
    assert {row["outcome"] for row in detail["targets"][0]["failed_items"]} == {"done"}

    # Undo: a new preview built from the receipt, then applied.
    revert_preview = missing_sweep.preview_missing_sweep(
        "course-1", revert_operation_id=preview["operation_id"])
    assert revert_preview["ok"] is True
    assert revert_preview["operation_id"] != preview["operation_id"]

    reverted = missing_sweep.apply_missing_sweep(
        revert_preview["operation_id"], revert_preview["batch_id"],
        revert_preview["review_digest"])
    assert reverted["ok"] is True
    assert reverted["counts"]["filled"] == 1
    _method, _path, undo_request = canvas.puts[-1]
    assert undo_request["submission"]["posted_grade"] == ""
    assert undo_request["submission"]["late_policy_status"] == "missing"
    assert canvas.rows["assignment-1:student-1"]["entered_score"] is None
    assert canvas.rows["assignment-1:student-1"]["late_policy_status"] == "missing"


def test_invalid_grading_policy_file_blocks_the_sweep_preview_with_a_message(
    monkeypatch, grading_policy_files,
):
    """Criterion 3 (sweep side): an invalid Grading Policy.txt blocks the
    preview with grading_policy_file_invalid and load_policy's own readable
    message, before any mirror read is attempted."""
    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    grading_policy_files.raw_policy(
        "floor_percent: 10\nmissing_percent: 20\nsweep_after_school_days: 15\n")

    result = missing_sweep.preview_missing_sweep("course-1")

    assert result == {"ok": False, "code": "grading_policy_file_invalid",
                      "error": ("Grading Policy.txt's floor_percent must be at or "
                               "above missing_percent, and both must be between "
                               "0 and 100."),
                      "blocking": True}


def _three_row_payload() -> dict:
    return {
        "course_id": "course-1", "mode": "sweep",
        "entries": [
            {"assignment_id": "assignment-1", "assignment_title": "A",
             "points_possible": 25, "missing_value": 5,
             "cached_due_date": "2026-08-01T12:00:00Z",
             "user_id": "student-1", "action": "fill"},
            {"assignment_id": "assignment-2", "assignment_title": "B",
             "points_possible": 25, "missing_value": 5,
             "cached_due_date": "2026-08-01T12:00:00Z",
             "user_id": "student-2", "action": "fill"},
            {"assignment_id": "assignment-3", "assignment_title": "C",
             "points_possible": 25, "missing_value": 5,
             "cached_due_date": "2026-08-01T12:00:00Z",
             "user_id": "student-3", "action": "fill"},
        ],
    }


class _MultiRowCanvas:
    """Three independent submission rows, one per (assignment, user)."""

    def __init__(self):
        base = {"workflow_state": "unsubmitted", "score": None, "entered_score": None,
                "excused": False, "missing": True, "late_policy_status": None,
                "custom_grade_status_id": None}
        self.rows = {
            "assignment-1:student-1": dict(base),
            "assignment-2:student-2": dict(base),
            "assignment-3:student-3": dict(base),
        }
        self.reject_keys: set[str] = set()
        self.uncertain_keys: set[str] = set()
        self.puts = []

    @staticmethod
    def _row_key(path: str) -> str:
        parts = path.split("/")
        return f"{parts[parts.index('assignments') + 1]}:{parts[parts.index('submissions') + 1]}"

    def get(self, path, params=None, timeout=20):
        return copy.deepcopy(self.rows.get(self._row_key(path))), None

    def send(self, method, path, payload, timeout=30):
        key = self._row_key(path)
        self.puts.append((method, path, copy.deepcopy(payload)))
        if key in self.uncertain_keys:
            return None, "Connection timeout"
        if key in self.reject_keys:
            return None, "HTTP 400: assignment is in a closed grading period"
        posted = payload["submission"]["posted_grade"]
        entered = None if posted == "" else float(posted)
        self.rows[key] = {
            **self.rows[key],
            "entered_score": entered, "score": entered,
            "late_policy_status": payload["submission"]["late_policy_status"],
        }
        return copy.deepcopy(self.rows[key]), None


def _target_with_three_steps() -> dict:
    return {"course_id": "course-1", "steps": [
        models.new_step("fill:assignment-1:student-1"),
        models.new_step("fill:assignment-2:student-2"),
        models.new_step("fill:assignment-3:student-3"),
    ], "failed_items": None}


def test_a_4xx_row_leaves_it_failed_and_continues_but_a_transport_error_stops(monkeypatch):
    """Failure behavior (criterion 5): one test for continue-on-rejection and
    stop-on-transport-unknown."""
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed",
                        lambda _course: None)
    adapter = MissingFillAdapter()

    # A definite HTTP rejection on one row must not stop the course sweep;
    # the target ends "partial" with the other two rows applied.
    rejecting = _MultiRowCanvas()
    rejecting.reject_keys = {"assignment-2:student-2"}
    monkeypatch.setattr(canvas_client, "canvas_get", rejecting.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", rejecting.send)

    result = adapter.execute(_three_row_payload(), _target_with_three_steps(),
                             {}, {}, FakeContext())

    assert result["state"] == "partial"
    outcomes = {row["user_id"]: row["outcome"] for row in result["failed_items"]}
    assert outcomes == {"student-1": "done", "student-2": "missing_fill_rejected",
                        "student-3": "done"}
    assert rejecting.rows["assignment-2:student-2"]["entered_score"] is None

    # A transport-unknown error stops the operation outright; later rows are
    # left untouched for resume to reconcile.
    uncertain = _MultiRowCanvas()
    uncertain.uncertain_keys = {"assignment-2:student-2"}
    monkeypatch.setattr(canvas_client, "canvas_get", uncertain.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", uncertain.send)

    stopped = adapter.execute(_three_row_payload(), _target_with_three_steps(),
                              {}, {}, FakeContext())

    assert stopped["state"] == "sent_unknown"
    stopped_outcomes = {row["user_id"]: row["outcome"] for row in stopped["failed_items"]}
    assert stopped_outcomes == {"student-1": "done", "student-2": "missing_fill_uncertain"}
    assert "student-3" not in stopped_outcomes
