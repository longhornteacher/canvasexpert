"""Service laws and review-shape contracts for existing-grade adjustments."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from api import grade_adjustment
from api.mirror import read_service
from api.operation_ledger.adapters import grade_adjustment as adapter_module
from api.platform_services import config


class FakeVault:
    def __init__(self):
        self.by_id = {"student-1": "Pikachu", "student-2": "Eevee",
                      "student-3": "Snorlax"}

    @contextmanager
    def transaction(self):
        yield self

    def get_or_assign(self, canvas_id, *args, **kwargs):
        return self.by_id[str(canvas_id)]

    def reverse(self, pseudonym):
        for canvas_id, value in self.by_id.items():
            if value == pseudonym:
                return {"canvas_id": canvas_id}
        return None


@pytest.fixture
def service_harness(monkeypatch):
    baseline = {
        "course_id": "course-1",
        "assignment_id": "assignment-1",
        "assignment": {
            "id": "assignment-1", "name": "Synthetic Lab",
            "grading_type": "points", "points_possible": 10,
        },
        "entries": [
            {"user_id": "student-1", "eligible": True, "before": 4,
             "before_excused": False, "skip_reason": None},
            {"user_id": "student-2", "eligible": True, "before": 6,
             "before_excused": False, "skip_reason": None},
            {"user_id": "student-3", "eligible": False, "before": 5,
             "before_excused": True, "skip_reason": "excused"},
        ],
        "roster": [{"id": "student-1"}, {"id": "student-2"},
                   {"id": "student-3"}],
        "synced_at": "2026-09-23T12:00:00Z",
        "freshness": {"state": "current", "within_policy": True},
    }
    vault = FakeVault()
    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    monkeypatch.setattr(grade_adjustment, "_vault", lambda: vault)
    monkeypatch.setattr(adapter_module, "_mirror_baseline",
                        lambda payload, target: copy.deepcopy(baseline))
    return baseline


def test_preview_contract_is_pseudonym_only_and_contains_summary(service_harness):
    result = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "rule", "model": "flat_bump", "settings": {"bump": 2}},
    )

    assert result["ok"] is True
    assert set(result) == {"ok", "operation_id", "batch_id", "review_digest", "preview"}
    assert set(result["preview"]) == {"assignment_title", "points_possible",
                                      "adjustment", "summary", "changed"}
    assert set(result["preview"]["summary"]) == {
        "eligible", "changed", "unchanged", "raised", "lowered", "capped",
        "skipped", "class_average_before_pct", "class_average_after_pct",
    }
    assert result["preview"]["changed"] == [
        {"pseudonym": "Eevee", "before": 6, "after": 8},
        {"pseudonym": "Pikachu", "before": 4, "after": 6},
    ]
    serialized = json.dumps(result)
    assert "student-1" not in serialized
    assert "student-2" not in serialized
    assert "student-3" not in serialized


@pytest.mark.parametrize("model, settings", [
    ("flat_bump", {"bump": 10, "cap": 8}),
    ("target_average", {"target_avg_pct": 100, "cap": 8}),
    ("proportional", {"target_avg_pct": 100, "cap": 8, "_scores": [4, 6]}),
    ("floor_cap", {"floor": 8, "cap": 8}),
])
def test_all_rule_models_default_to_no_harm_and_respect_cap(model, settings):
    before = 4
    after, _capped = grade_adjustment._rule_score(
        before, model, settings, 10, 4, 6, 2)

    assert after >= before
    assert after <= 8


def test_invalid_pseudonym_is_named_and_no_changes_are_refused(service_harness):
    invalid = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "explicit", "entries": [{"pseudonym": "Missingno", "delta": 1}]},
    )
    assert invalid["ok"] is False
    assert invalid["code"] == "invalid_adjustment"
    assert "Missingno" in invalid["error"]

    unchanged = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "rule", "model": "flat_bump", "settings": {"bump": 0}},
    )
    assert unchanged == {
        "ok": False, "code": "no_changes",
        "error": "No grade entries would change.",
    }


def test_rule_curve_never_lifts_a_missing_or_zero_row(monkeypatch):
    """Law: a rule curve never changes a missing or zero row; an explicit
    adjustment (the teacher fixing named rows) may still target them."""
    baseline = {
        "course_id": "course-1", "assignment_id": "assignment-1",
        "assignment": {"id": "assignment-1", "name": "Curve Lab",
                       "grading_type": "points", "points_possible": 10},
        "entries": [
            {"user_id": "student-1", "eligible": True, "before": 4,
             "before_excused": False, "skip_reason": None, "missing": False},
            {"user_id": "student-2", "eligible": True, "before": 0,
             "before_excused": False, "skip_reason": None, "missing": False},
            {"user_id": "student-3", "eligible": True, "before": 0,
             "before_excused": False, "skip_reason": None, "missing": True},
        ],
        "roster": [{"id": "student-1"}, {"id": "student-2"},
                   {"id": "student-3"}],
        "synced_at": "2026-09-23T12:00:00Z",
        "freshness": {"state": "current", "within_policy": True},
    }
    vault = FakeVault()
    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    monkeypatch.setattr(grade_adjustment, "_vault", lambda: vault)
    monkeypatch.setattr(adapter_module, "_mirror_baseline",
                        lambda payload, target: copy.deepcopy(baseline))

    rule_preview = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "rule", "model": "flat_bump", "settings": {"bump": 5}},
    )
    assert rule_preview["ok"] is True
    assert rule_preview["preview"]["changed"] == [
        {"pseudonym": "Pikachu", "before": 4, "after": 9},
    ]
    assert rule_preview["preview"]["summary"]["skipped"]["zero"] == 1
    assert rule_preview["preview"]["summary"]["skipped"]["missing"] == 1

    explicit_preview = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "explicit", "entries": [
            {"pseudonym": "Eevee", "new_score": 3},
            {"pseudonym": "Snorlax", "new_score": 2},
        ]},
    )
    assert explicit_preview["ok"] is True
    assert {row["pseudonym"] for row in explicit_preview["preview"]["changed"]} == {
        "Eevee", "Snorlax",
    }


class _FixedClock(datetime):
    """A real ``datetime`` subclass with ``now()`` pinned to a fixed Saturday
    evening, so the school-hours policy window is deterministically the
    600-minute out-of-school window regardless of when the suite runs."""
    fixed = datetime(2026, 9, 26, 20, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed if tz is None else cls.fixed.astimezone(tz)


def test_freshness_attention_passes_current_mirror_inside_window_and_blocks_outside_it(
        monkeypatch):
    """Law: an all-current mirror synced inside the freshness policy window
    never blocks; the same mirror synced outside the window returns
    freshness_attention. Regression for a defect where the derived state
    could never resolve to "current" on a real (non-mocked) read, so every
    live preview blocked even with a fully current mirror."""
    monkeypatch.setattr(adapter_module.freshness_policy, "datetime", _FixedClock)

    inside_window = "2026-09-26T19:30:00Z"  # 30 minutes old, well inside 600 minutes
    fresh_scope = {"state": "current", "last_success_at": inside_window}
    assert adapter_module._freshness_attention(fresh_scope, fresh_scope, fresh_scope) == {}

    outside_window = "2026-09-24T20:00:00Z"  # 2 days old, well outside 600 minutes
    stale_scope = {"state": "current", "last_success_at": outside_window}
    blocked = adapter_module._freshness_attention(stale_scope, stale_scope, stale_scope)
    assert blocked["blocking_error"] == "freshness_attention"


def test_mirror_refresh_required_blocks_only_a_keyless_late_row(monkeypatch):
    """Criterion 4, narrowed: Canvas only deducts from late submissions, so a
    row missing the entered_score key is eligible directly on its score when
    the row is not late. A late row in the same keyless shape still blocks
    with mirror_refresh_required, now carrying a calm attention reason naming
    the daily background full-refresh remedy."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def wire_submission_row(row: dict) -> None:
        def fake_private(name):
            def _reader(course_id, max_age_hours=None):
                if name == "roster":
                    return {"records": [{"id": "student-1"}],
                            "last_success_at": now, "state": "current"}
                if name == "assignments":
                    return {"records": [{"id": "assignment-1", "grading_type": "points",
                                         "points_possible": 10, "name": "Curve Lab"}],
                            "last_success_at": now, "state": "current"}
                return {"records": [row], "last_success_at": now, "state": "current"}
            return _reader
        monkeypatch.setattr(read_service, "private_roster", fake_private("roster"))
        monkeypatch.setattr(read_service, "private_assignments", fake_private("assignments"))
        monkeypatch.setattr(read_service, "private_submissions", fake_private("submissions"))

    payload = {"course_id": "course-1", "assignment_id": "assignment-1"}
    target = {"course_id": "course-1"}

    wire_submission_row({"assignment_id": "assignment-1", "user_id": "student-1",
                         "score": 7, "excused": False, "late": False})
    non_late = adapter_module._mirror_baseline(payload, target)
    assert "blocking_error" not in non_late
    entry = next(row for row in non_late["entries"] if row["user_id"] == "student-1")
    assert entry == {"user_id": "student-1", "eligible": True, "skip_reason": None,
                     "before": 7, "before_excused": False, "missing": False}

    wire_submission_row({"assignment_id": "assignment-1", "user_id": "student-1",
                         "score": 7, "excused": False, "late": True})
    late = adapter_module._mirror_baseline(payload, target)
    assert late["blocking_error"] == "mirror_refresh_required"
    assert late["attention"]["action"] == "ask_teacher_confirmation"
    assert "daily background refresh" in late["attention"]["reason"]


def test_unsupported_grading_type_refuses_before_operation_creation(
        service_harness, monkeypatch):
    baseline = copy.deepcopy(service_harness)
    baseline["blocking_error"] = "unsupported_grading_type"
    monkeypatch.setattr(adapter_module, "_mirror_baseline",
                        lambda payload, target: copy.deepcopy(baseline))

    result = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "rule", "model": "flat_bump", "settings": {"bump": 1}},
    )

    assert result == {
        "ok": False,
        "code": "unsupported_grading_type",
        "error": "unsupported_grading_type",
        "blocking": True,
    }
