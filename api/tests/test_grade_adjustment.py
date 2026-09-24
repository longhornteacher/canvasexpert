"""Service laws and review-shape contracts for existing-grade adjustments."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager

import pytest

from api import grade_adjustment
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
