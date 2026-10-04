"""Tool-boundary contracts for local curve rules and score-history coverage."""
from __future__ import annotations

import hashlib
import re

import pytest

from api.feedback_vault import Vault
from api.mcp_server import tools
from api import score_ledger
from api.platform_services import workspace


@pytest.fixture(autouse=True)
def _score_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))


def test_rule_tools_are_local_and_history_discloses_recorded_only_coverage(
        tmp_path, monkeypatch, _set_active_courses):
    _set_active_courses(["course-1"])
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign("synthetic-student-1", real_name="Synthetic Student")
    vault.set_pseudonym("synthetic-student-1", "Pikachu")
    vault.save()
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)

    created = tools.set_score_curve_rule('course-1', {'model': 'gap_close', 'fraction': 0.3}, 'assignment-1')
    assert created["ok"] is True
    assert created["rule"]["preview"]["entered_score"] == 67
    empty = tools.get_score_ledger("course-1", "assignment-1")
    assert empty["coverage"] == "recorded_only"
    assert empty["first_recorded_at"] is None
    assert empty["events"] == []

    feedback = "Synthetic private feedback."
    score_ledger.append_event({"source": "ce_stage", "action": "staged",
        "course_id": "course-1", "assignment_id": "assignment-1",
        "student_id": "synthetic-student-1", "attempt": 1,
        "raw_score": 53, "entered_score": 67,
        "curve_rule_id": created["rule"]["rule_id"],
        "feedback": feedback}, idempotency_key="stage:test", root=tmp_path)
    page = tools.get_score_ledger("course-1", "assignment-1", limit=1)
    assert page["events"][0]["pseudonym"] == "Pikachu"
    assert page["events"][0]["feedback"] == "Pikachu private feedback."
    assert page["events"][0]["feedback_sha256"] == hashlib.sha256(
        page["events"][0]["feedback"].encode()).hexdigest()
    assert "student_id" not in page["events"][0]
    assert re.fullmatch(r"[0-9a-f]{64}", page["events"][0]["device_id"])
    assert "integrity_sha256" not in page["events"][0]
    assert page["first_recorded_at"] == page["events"][0]["timestamp"]
    assert tools.get_score_ledger("course-1", "assignment-1", offset=1)["events"] == []
    assert tools.get_score_ledger("course-1", "assignment-1", pseudonyms="Nobody")[
        "first_recorded_at"] is None

    deactivated = tools.set_score_curve_rule('course-1', rule_id=created['rule']['rule_id'], active=False)
    assert deactivated["ok"] is True
    assert any(event["action"] == "deactivate"
               for event in score_ledger.list_rule_events("course-1", root=tmp_path))


def test_score_history_page_has_a_serialized_bound_without_truncating_feedback(
        tmp_path, _set_active_courses):
    _set_active_courses(["course-1"])
    feedback = "A" * 18000
    for number in range(3):
        score_ledger.append_event({"source": "ce_stage", "action": "staged",
            "course_id": "course-1", "assignment_id": "assignment-1",
            "student_id": f"student-{number}", "attempt": 1,
            "raw_score": 1, "entered_score": 1,
            "feedback": feedback}, idempotency_key=f"stage:{number}", root=tmp_path)
    page = tools.get_score_ledger("course-1", "assignment-1", limit=3)
    assert len(page["events"]) == 2
    assert all(event["feedback"] == feedback for event in page["events"])
    assert page["next_offset"] == 2
    oversized = tools.get_score_ledger("course-1", "assignment-1", offset=2, limit=1)
    assert oversized["events"][0]["feedback"] == feedback
