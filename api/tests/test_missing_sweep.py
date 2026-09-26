"""Service laws and review-shape contracts for the course-wide missing sweep."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from api import grading_policy, missing_sweep
from api.operation_ledger.adapters import missing_fill as adapter_module
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


@pytest.fixture
def service_harness(monkeypatch):
    entries = [
        {"assignment_id": "assignment-1", "assignment_title": "Essay",
         "points_possible": 25, "missing_value": 5,
         "cached_due_date": "2026-08-01T12:00:00Z",
         "user_id": "student-1", "action": "fill"},
    ]
    vault = FakeVault()
    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    monkeypatch.setattr(missing_sweep, "_vault", lambda: vault)
    monkeypatch.setattr(adapter_module, "_discover",
                        lambda course_id: (copy.deepcopy(entries), {"has_score": 2}, None))
    return entries


def test_preview_contract_is_pseudonym_only_and_grouped_by_assignment(service_harness):
    result = missing_sweep.preview_missing_sweep("course-1")

    assert result["ok"] is True
    assert set(result) == {"ok", "operation_id", "batch_id", "review_digest", "preview"}
    assert set(result["preview"]) == {"assignments", "row_count", "skipped"}
    assert result["preview"]["row_count"] == 1
    assert result["preview"]["skipped"] == {"has_score": 2}
    assert result["preview"]["assignments"] == [{
        "assignment_id": "assignment-1", "title": "Essay", "due_at": "2026-08-01T12:00:00Z",
        "points_possible": 25, "missing_value": 5, "row_count": 1,
        "pseudonyms": ["Pikachu"],
    }]
    serialized = json.dumps(result)
    assert "student-1" not in serialized


def test_no_grading_policy_refuses_before_operation_creation(monkeypatch):
    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    monkeypatch.setattr(adapter_module, "_discover",
                        lambda course_id: ([], {}, {"blocking_error": "no_grading_policy"}))

    result = missing_sweep.preview_missing_sweep("course-1")

    assert result == {"ok": False, "code": "no_grading_policy",
                      "error": "no_grading_policy", "blocking": True}


def test_no_eligible_rows_is_refused_as_no_changes(monkeypatch):
    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    monkeypatch.setattr(missing_sweep, "_vault", lambda: FakeVault())
    monkeypatch.setattr(adapter_module, "_discover",
                        lambda course_id: ([], {"before_window": 3}, None))

    result = missing_sweep.preview_missing_sweep("course-1")

    assert result == {"ok": False, "code": "no_changes",
                      "error": "No missing rows are eligible for the sweep."}


# ── Law: the sweep never writes a row with a submission, a score, or an
# excuse (the eligibility function, criterion 3 row cases) ──────────────────

def test_row_eligibility_never_admits_a_submission_score_excuse_or_stale_enrollment():
    today = date(2026, 9, 1)
    current_ids = {"student-1"}
    base = {"user_id": "student-1", "missing": True,
            "cached_due_date": "2026-08-01T12:00:00Z", "workflow_state": "unsubmitted"}

    assert adapter_module._row_eligibility(base, current_ids, [], 0, today) is None

    assert adapter_module._row_eligibility(
        {**base, "workflow_state": "submitted"}, current_ids, [], 0, today
    ) == "has_submission"
    assert adapter_module._row_eligibility(
        {**base, "score": 5}, current_ids, [], 0, today
    ) == "has_score"
    assert adapter_module._row_eligibility(
        {**base, "excused": True}, current_ids, [], 0, today
    ) == "excused"
    assert adapter_module._row_eligibility(
        base, set(), [], 0, today
    ) == "not_current"
    assert adapter_module._row_eligibility(
        {**base, "missing": False}, current_ids, [], 0, today
    ) == "not_missing"
    assert adapter_module._row_eligibility(
        {**base, "cached_due_date": None}, current_ids, [], 0, today
    ) == "no_due_date"


# ── Law: the school-day count excludes weekends and no-school dates
# (criterion 2), using fixed dates and an explicit no-school list ──────────

def _add_school_days(start: date, count: int, no_school_dates=()) -> date:
    excluded = set(no_school_dates)
    current = start
    added = 0
    while added < count:
        current = current + timedelta(days=1)
        if current.weekday() < 5 and current.isoformat() not in excluded:
            added += 1
    return current


def test_row_window_excludes_weekends_and_no_school_dates_before_the_threshold():
    due = date(2026, 9, 1)  # a Tuesday
    no_school = ["2026-09-15"]  # a weekday inside the window, deliberately excluded
    current_ids = {"student-1"}
    entry = {"user_id": "student-1", "missing": True, "workflow_state": "unsubmitted",
             "cached_due_date": f"{due.isoformat()}T12:00:00Z"}

    at_16 = _add_school_days(due, 16, no_school)
    assert adapter_module._row_eligibility(entry, current_ids, no_school, 16, at_16) is None

    one_short = _add_school_days(due, 15, no_school)
    assert adapter_module._row_eligibility(
        entry, current_ids, no_school, 16, one_short) == "before_window"

    # 2 grace days raise the threshold past the same 16-school-day date.
    assert adapter_module._row_eligibility(
        entry, current_ids, no_school, 16 + 2, at_16) == "before_window"

    # Prove the excluded no-school date actually mattered: ignoring it, the
    # same calendar date is more than 16 school days out.
    assert grading_policy.school_days_between(due, at_16, []) > 16


# ── Assignment-level exclusions (criterion 3) ───────────────────────────────

@pytest.mark.parametrize("patch, reason", [
    ({"published": False}, "unpublished"),
    ({"grading_type": "percent"}, "not_points"),
    ({"points_possible": 0}, "not_points"),
    ({"submission_types": ["none"]}, "no_submission"),
    ({"submission_types": ["on_paper"]}, "on_paper"),
    ({"submission_types": ["external_tool"]}, "external_tool"),
    ({"group_category_id": "77", "grade_group_students_individually": False},
     "group_assignment"),
    ({"in_closed_grading_period": True}, "closed_grading_period"),
])
def test_assignment_eligibility_names_each_exclusion(patch, reason):
    assignment = {
        "published": True, "grading_type": "points", "points_possible": 25,
        "submission_types": ["online_text_entry"], "is_quiz_lti_assignment": False,
        "group_category_id": None, "grade_group_students_individually": False,
        "in_closed_grading_period": False,
    }
    assignment.update(patch)
    assert adapter_module._assignment_eligibility(assignment) == reason


def test_assignment_eligibility_excepts_new_quiz_external_tool_and_individual_group():
    assignment = {
        "published": True, "grading_type": "points", "points_possible": 25,
        "submission_types": ["external_tool"], "is_quiz_lti_assignment": True,
        "group_category_id": None, "grade_group_students_individually": False,
        "in_closed_grading_period": False,
    }
    assert adapter_module._assignment_eligibility(assignment) is None

    assignment = {
        "published": True, "grading_type": "points", "points_possible": 25,
        "submission_types": ["online_text_entry"], "is_quiz_lti_assignment": False,
        "group_category_id": "77", "grade_group_students_individually": True,
        "in_closed_grading_period": False,
    }
    assert adapter_module._assignment_eligibility(assignment) is None
