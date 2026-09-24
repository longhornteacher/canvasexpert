"""Operation Ledger laws and one complete synthetic grade-adjustment example."""
from __future__ import annotations

import copy
from contextlib import contextmanager

import pytest

from api import grade_adjustment
from api.operation_ledger import models, paths, receipts
from api.operation_ledger.adapters import grade_adjustment as adapter_module
from api.operation_ledger.adapters.grade_adjustment import GradeAdjustmentAdapter
from api.platform_services import canvas_client, config


class FakeVault:
    def __init__(self):
        self.by_id = {"student-1": "Pikachu", "student-2": "Eevee"}

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


class FakeCanvas:
    def __init__(self):
        self.assignment = {
            "id": "assignment-1", "grading_type": "points",
            "points_possible": 10, "name": "Synthetic Lab",
        }
        self.submissions = {
            "student-1": {"score": 5, "excused": False},
            "student-2": {"score": 8, "excused": False},
            "student-3": {"score": 4, "excused": True},
        }
        self.puts = []

    def get(self, path, params=None, timeout=20):
        if path.endswith("/assignments/assignment-1"):
            return copy.deepcopy(self.assignment), None
        if "/submissions/" in path:
            user_id = path.rsplit("/", 1)[-1]
            return copy.deepcopy(self.submissions.get(user_id)), None
        return None, "missing"

    def send(self, method, path, payload, timeout=30):
        self.puts.append((method, path, copy.deepcopy(payload)))
        user_id = path.rsplit("/", 1)[-1]
        self.submissions[user_id] = {
            "score": float(payload["submission"]["posted_grade"]),
            "excused": False,
        }
        return copy.deepcopy(self.submissions[user_id]), None


def _payload():
    return {
        "course_id": "course-1", "assignment_id": "assignment-1",
        "entries": [
            {"user_id": "student-1", "before": 5, "after": 7,
             "before_excused": False, "changed": True},
            {"user_id": "student-2", "before": 7, "after": 9,
             "before_excused": False, "changed": True},
            {"user_id": "student-3", "before": 4, "after": 6,
             "before_excused": False, "changed": True},
        ],
    }


def test_live_score_or_excused_change_skips_one_entry_and_writes_the_other(
        monkeypatch):
    canvas = FakeCanvas()
    adapter = GradeAdjustmentAdapter()
    payload = _payload()
    target = {"course_id": "course-1", "steps": [
        models.new_step("adjust:0"), models.new_step("adjust:1"),
        models.new_step("adjust:2"),
    ], "failed_items": None}
    context = FakeContext()
    monkeypatch.setattr(canvas_client, "canvas_get", canvas.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", canvas.send)
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed",
                        lambda _course: None)

    result = adapter.execute(payload, target, {}, {}, context)

    assert result["state"] == "applied"
    assert len(canvas.puts) == 1
    method, path, request = canvas.puts[0]
    assert method == "PUT"
    assert "/courses/course-1/assignments/assignment-1/submissions/student-1" in path
    assert set(request) == {"submission"}
    assert set(request["submission"]) == {"posted_grade"}
    assert {row["outcome"] for row in result["failed_items"]} == {
        "done", "score_changed_since_preview",
    }
    assert sum(row["outcome"] == "score_changed_since_preview"
               for row in result["failed_items"]) == 2


def test_flat_bump_apply_receipt_and_revert_preview_are_digest_protected(
        monkeypatch):
    canvas = FakeCanvas()
    vault = FakeVault()
    monkeypatch.setattr(config, "active_courses",
                        lambda: [{"id": "course-1", "name": "Synthetic Course"}])
    monkeypatch.setattr(grade_adjustment, "_vault", lambda: vault)
    monkeypatch.setattr(canvas_client, "canvas_get", canvas.get)
    monkeypatch.setattr(canvas_client, "_canvas_send", canvas.send)
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed",
                        lambda _course: None)

    def mirror_baseline(payload, target):
        return {
            "course_id": "course-1", "assignment_id": "assignment-1",
            "assignment": copy.deepcopy(canvas.assignment),
            "entries": [
                {"user_id": user_id, "eligible": True,
                 "before": row["score"], "before_excused": row["excused"],
                 "skip_reason": None}
                for user_id, row in sorted(canvas.submissions.items())
                if user_id in {"student-1", "student-2"}
            ],
            "roster": [{"id": "student-1"}, {"id": "student-2"}],
            "synced_at": "2026-09-23T12:00:00Z",
            "freshness": {"state": "current", "within_policy": True},
        }

    monkeypatch.setattr(adapter_module, "_mirror_baseline", mirror_baseline)

    preview = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "rule", "model": "flat_bump", "settings": {"bump": 2}},
    )
    assert preview["ok"] is True
    applied = grade_adjustment.apply_grade_adjustment(
        preview["operation_id"], preview["batch_id"], preview["review_digest"])
    assert applied["ok"] is True
    assert applied["counts"] == {
        "adjusted": 2, "skipped_changed": 0, "failed": 0, "unverified": 0,
    }
    assert canvas.submissions["student-1"]["score"] == 7
    assert canvas.submissions["student-2"]["score"] == 10
    detail = receipts.get_receipt(applied["receipt_id"])
    assert {row["outcome"] for row in detail["targets"][0]["failed_items"]} == {"done"}
    assert len(grade_adjustment.report_adjustments()) == 1

    already = grade_adjustment.apply_grade_adjustment(
        preview["operation_id"], preview["batch_id"], preview["review_digest"])
    assert already["status"] == "already_applied"
    assert len(canvas.puts) == 2

    revert_preview = grade_adjustment.preview_grade_adjustment(
        "course-1", "assignment-1",
        {"kind": "revert", "operation_id": preview["operation_id"]},
    )
    assert revert_preview["ok"] is True
    assert revert_preview["preview"]["changed"] == [
        {"pseudonym": "Eevee", "before": 10, "after": 8},
        {"pseudonym": "Pikachu", "before": 7, "after": 5},
    ]
    reverted = grade_adjustment.apply_grade_adjustment(
        revert_preview["operation_id"], revert_preview["batch_id"],
        revert_preview["review_digest"])
    assert reverted["ok"] is True
    assert canvas.submissions["student-1"]["score"] == 5
    assert canvas.submissions["student-2"]["score"] == 8
    assert grade_adjustment.report_adjustments() == []
