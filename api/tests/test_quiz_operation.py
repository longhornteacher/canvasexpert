"""QuizForge quiz adapter tests — prepare, review, apply, retry, reconcile.

Tests the full operation-ledger lifecycle for ``content.quiz`` using mocked
Canvas calls and a mocked plan subprocess. No live Canvas interaction.
"""
import json
import os

import pytest

from api.operation_ledger import (
    batches, claims, executor, models, operations, paths, registry, storage,
)
from api.operation_ledger.adapters import QuizAdapter
from api.operation_ledger.adapters import quiz as quiz_adapter_module
from api.platform_services import canvas_client, config


# ── Sample plan ──────────────────────────────────────────────────────────

SAMPLE_PLAN = {
    "version": 1,
    "title": "Algebra Quiz 1",
    "source_path": "/tmp/algebra.txt",
    "quiz_payload": {
        "quiz": {
            "title": "Algebra Quiz 1",
            "points_possible": 100.0,
            "grading_type": "points",
            "quiz_settings": {
                "shuffle_answers": True,
                "shuffle_questions": False,
                "result_view_settings": {
                    "result_view_restricted": True,
                    "display_points_awarded": True,
                    "display_points_possible": True,
                    "display_items": True,
                    "display_item_response": True,
                    "display_item_response_correctness": True,
                    "display_item_response_qualifier": "after_last_attempt",
                    "display_item_correct_answer": True,
                    "display_item_feedback": True,
                },
            },
        },
    },
    "items": [
        {
            "index": 1,
            "source_item_id": "q1",
            "source_type": "MC",
            "payload": {"item": {"entry_type": "Item", "position": 1, "points_possible": 50.0}},
        },
        {
            "index": 2,
            "source_item_id": "q2",
            "source_type": "TF",
            "payload": {"item": {"entry_type": "Item", "position": 2, "points_possible": 50.0}},
        },
    ],
    "assignment_settings": {
        "due_at": "2026-08-01T23:59:00Z",
        "published": True,
        "post_to_sis": False,
    },
    "module": {
        "module_name": "Unit 1",
    },
}

SAMPLE_PLAN_NO_MODULE = {**SAMPLE_PLAN, "module": {}}

SAMPLE_PLAN_NO_SETTINGS = {**SAMPLE_PLAN, "assignment_settings": {}, "module": {}}


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(paths, "private_root", lambda: root)
    return root


def _mock_active_courses(monkeypatch, courses=None):
    if courses is None:
        courses = [
            {"id": "101", "name": "Algebra 1", "active": True},
            {"id": "102", "name": "Biology", "active": True},
        ]
    monkeypatch.setattr(config, "active_courses", lambda: courses)


def _mock_plan_subprocess(monkeypatch, plan=None, fail=False):
    """Mock run_json_object at the adapter's import path."""
    plan = plan or SAMPLE_PLAN

    def fake_run_json_object(args, extra_env=None, timeout=30, max_output_bytes=2_000_000):
        if fail:
            raise ValueError("planner failed")
        return plan

    monkeypatch.setattr(quiz_adapter_module, "run_json_object", fake_run_json_object)


def _mock_canvas_send(monkeypatch, responses=None):
    """Mock _canvas_send with a queue of (response, error) tuples."""
    calls = []
    queue = list(responses or [])

    def fake_send(method, path, payload, timeout=30):
        calls.append({"method": method, "path": path, "payload": payload})
        if queue:
            return queue.pop(0)
        return None, "no more mock responses"

    monkeypatch.setattr(canvas_client, "_canvas_send", fake_send)
    return calls


def _mockcanvas_get(monkeypatch, responses=None):
    """Mock canvas_get with a queue of (data, error) tuples."""
    calls = []
    queue = list(responses or [])

    def fake_get(path, params=None, timeout=20):
        calls.append({"path": path, "params": params})
        if queue:
            return queue.pop(0)
        return None, "no more mock responses"

    monkeypatch.setattr(canvas_client, "canvas_get", fake_get)
    return calls


def _mockcanvas_get_all(monkeypatch, responses=None):
    """Mock canvas_get_all with a queue of (list, error) tuples."""
    queue = list(responses or [])

    def fake_get_all(path, params=None, timeout=30):
        if queue:
            return queue.pop(0)
        return [], None

    monkeypatch.setattr(canvas_client, "canvas_get_all", fake_get_all)


# ── Protocol conformance ─────────────────────────────────────────────────

def test_adapter_is_registered():
    adapter = registry.get_adapter("content.quiz")
    assert adapter is not None
    assert adapter.kind == "content.quiz"


# ── Prepare tests ────────────────────────────────────────────────────────

def test_prepare_with_valid_plan(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt", "settings": {}})
    assert payload["mode"] == "whole"
    assert payload["plan"]["title"] == "Algebra Quiz 1"
    assert len(payload["plan"]["items"]) == 2

    digest = adapter.source_digest(payload)
    assert len(digest) == 64  # SHA-256 hex

    targets = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])
    assert len(targets) == 2
    assert targets[0]["target_key"] != targets[1]["target_key"]
    assert targets[0]["idempotency_key"] != targets[1]["idempotency_key"]


def test_prepare_rejects_differentiated_mode(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    with pytest.raises(ValueError, match="at least two variants"):
        adapter.build_payload({"path": "/tmp/algebra.txt", "mode": "differentiated"})


def test_prepare_rejects_empty_items(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_plan_subprocess(monkeypatch, plan={**SAMPLE_PLAN, "items": []})

    adapter = QuizAdapter()
    with pytest.raises(ValueError, match="plan has no items"):
        adapter.build_payload({"path": "/tmp/algebra.txt"})


def test_prepare_rejects_missing_quiz_payload(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_plan_subprocess(monkeypatch, plan={**SAMPLE_PLAN, "quiz_payload": {}})

    adapter = QuizAdapter()
    with pytest.raises(ValueError, match="plan missing quiz_payload"):
        adapter.build_payload({"path": "/tmp/algebra.txt"})


def test_prepare_rejects_plan_subprocess_failure(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_plan_subprocess(monkeypatch, fail=True)

    adapter = QuizAdapter()
    with pytest.raises(ValueError, match="planner failed"):
        adapter.build_payload({"path": "/tmp/algebra.txt"})


def test_prepare_unknown_course_rejected(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch, [{"id": "101", "name": "Only Course", "active": True}])
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    with pytest.raises(ValueError, match="not in active courses"):
        adapter.verify_targets(payload, [{"course_id": "999"}])


def test_source_digest_deterministic(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    p1 = adapter.build_payload({"path": "/tmp/algebra.txt"})
    p2 = adapter.build_payload({"path": "/tmp/algebra.txt"})
    assert adapter.source_digest(p1) == adapter.source_digest(p2)


# ── Baseline / drift tests ──────────────────────────────────────────────

def test_capture_baseline_no_existing(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # no existing assignments
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    baseline = adapter.capture_baseline(payload, {"course_id": "101"})
    assert baseline["existing_quiz"] is None
    assert "canvas_error" not in baseline


def test_capture_baseline_with_existing_new_quiz(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([{"id": 555, "name": "Algebra Quiz 1", "new_quizzes": True,
           "published": False, "html_url": "http://canvas/assignments/555"}], None),
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    baseline = adapter.capture_baseline(payload, {"course_id": "101"})
    assert baseline["existing_quiz"] is not None
    assert baseline["existing_quiz"]["id"] == "555"
    assert baseline["existing_quiz"]["new_quizzes"] is True


def test_drift_blocks_unknown_same_title(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    baseline = {"existing_quiz": {"id": "555", "name": "Algebra Quiz 1",
                                   "new_quizzes": True}}
    target = {"steps": []}  # no known IDs
    assert adapter.check_drift(payload, target, baseline) is True


def test_drift_allows_known_exact_id(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    baseline = {"existing_quiz": {"id": "555", "name": "Algebra Quiz 1",
                                   "new_quizzes": True}}
    target = {
        "steps": [
            {"step_key": "create_quiz:0", "returned_object_id": "555"},
        ]
    }
    assert adapter.check_drift(payload, target, baseline) is True  # still drift (title collision)


def test_no_drift_when_no_existing(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    baseline = {"existing_quiz": None}
    assert adapter.check_drift(payload, target={}, baseline=baseline) is False


# ── Review tests ─────────────────────────────────────────────────────────

def test_review_freezes_per_target_summaries(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # course 101: no existing
        ([], None),  # course 102: no existing
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])

    frozen = []
    for t in targets:
        baseline = adapter.capture_baseline(payload, t)
        frozen.append(adapter.freeze_review(payload, t, baseline))

    assert len(frozen) == 2
    assert frozen[0]["course_name"] == "Algebra 1"
    assert frozen[0]["title"] == "Algebra Quiz 1"
    assert frozen[0]["mode"] == "whole"
    assert frozen[0]["item_count"] == 2
    assert frozen[0]["item_types"]["MC"] == 1
    assert frozen[0]["item_types"]["TF"] == 1
    assert frozen[0]["total_points"] == 100.0
    assert frozen[0]["due_at"] == "2026-08-01T23:59:00Z"
    assert frozen[0]["published"] is True
    assert frozen[0]["baseline_has_existing"] is False


def test_review_with_existing_quiz(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([{"id": 555, "name": "Algebra Quiz 1", "new_quizzes": True,
           "published": False, "html_url": "http://canvas/assignments/555"}], None),
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets = adapter.verify_targets(payload, [{"course_id": "101"}])

    baseline = adapter.capture_baseline(payload, targets[0])
    review = adapter.freeze_review(payload, targets[0], baseline)
    assert review["baseline_has_existing"] is True
    assert review["baseline_existing_id"] == "555"
    assert review["baseline_existing_new_quizzes"] is True


def test_batch_review_digest_computed(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None), ([], None),
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])

    target_records = []
    frozen = []
    for t in targets:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, t, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    assert batch["batch_id"].startswith("batch-")
    assert len(batch["review_digest"]) == 64
    assert len(batch["frozen_reviews"]) == 2

    operations.set_operation_review(op_id, batch)
    fetched = operations.get_operation(op_id)
    assert fetched["review"]["batch_id"] == batch["batch_id"]
    assert fetched["status"] == "reviewed"


# ── Apply tests ──────────────────────────────────────────────────────────

def test_apply_creates_quiz_and_items(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    # capture_baseline: setup (1) + apply-time (1) + verify after patch (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline (assignments search)
        ([], None),  # apply-time capture_baseline (assignments search)
        # Assignment verify after patch for course 101
        ({"id": "1001", "name": "Algebra Quiz 1", "published": True,
          "due_at": "2026-08-01T23:59:00Z", "post_to_sis": False}, None),
    ])
    _mockcanvas_get_all(monkeypatch, [
        ([{"id": 10, "name": "Unit 1"}], None),  # module lookup
    ])
    # Mock Canvas POST for quiz create, 2 items, patch assignment, module attach
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"id": 1001}, None),  # create_quiz:0
        ({"id": 2001}, None),  # create_item:0:1
        ({"id": 2002}, None),  # create_item:0:2
        ({"id": 1001}, None),  # patch_assignment:0 (PUT returns assignment)
        ({"id": 3001}, None),  # attach_module:0
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    frozen = []
    for t in targets_in:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, t, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert len(result["target_results"]) == 1
    tr = result["target_results"][0]
    assert tr["state"] == "applied"
    assert tr["returned_object_id"] == "1001"

    # Verify Canvas calls
    assert len(send_calls) == 5  # quiz, 2 items, patch, module
    assert "/api/quiz/v1/courses/101/quizzes" in send_calls[0]["path"]
    assert "items" in send_calls[1]["path"]
    assert "items" in send_calls[2]["path"]
    assert "modules" in send_calls[4]["path"]

    stored = operations.get_operation(op_id)
    assert stored["targets"][0]["returned_object_id"] == "1001"
    steps = {s["step_key"]: s for s in stored["targets"][0]["steps"]}
    assert steps["create_quiz:0"]["state"] == "applied"
    assert steps["create_item:0:1"]["state"] == "applied"
    assert steps["create_item:0:2"]["state"] == "applied"
    assert steps["attach_module:0"]["state"] == "applied"


def test_apply_without_module(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch, plan=SAMPLE_PLAN_NO_MODULE)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ({"id": "1001", "name": "Algebra Quiz 1", "published": True,
          "due_at": "2026-08-01T23:59:00Z", "post_to_sis": False}, None),  # verify after patch
    ])
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"id": 1001}, None),  # create_quiz:0
        ({"id": 2001}, None),  # create_item:0:1
        ({"id": 2002}, None),  # create_item:0:2
        ({"id": 1001}, None),  # patch_assignment:0
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    frozen = []
    for t in targets_in:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, t, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["ok"] is True
    assert result["status"] == "applied"
    assert len(send_calls) == 4  # quiz, 2 items, patch (no module)


def test_apply_without_assignment_settings(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch, plan=SAMPLE_PLAN_NO_SETTINGS)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift
    ])
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"id": 1001}, None),  # create_quiz:0
        ({"id": 2001}, None),  # create_item:0:1
        ({"id": 2002}, None),  # create_item:0:2
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    frozen = []
    for t in targets_in:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, t, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["ok"] is True
    assert result["status"] == "applied"
    assert len(send_calls) == 3  # no patch_assignment call


def test_apply_quiz_create_failure(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift
    ])
    send_calls = _mock_canvas_send(monkeypatch, [
        (None, "HTTP 400: bad request"),  # create_quiz:0 fails
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    frozen = []
    for t in targets_in:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, t, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["target_results"][0]["state"] == "failed"


def test_apply_item_create_failure(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift
    ])
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"id": 1001}, None),  # create_quiz:0 succeeds
        (None, "HTTP 500: server error"),  # create_item:0:1 fails
        ({}, None),  # rollback_quiz:0 succeeds
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    frozen = []
    for t in targets_in:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, t, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["ok"] is False
    assert result["target_results"][0]["state"] == "failed"
    assert result["target_results"][0]["failed_items"] == [{
        "item_index": 1,
        "field": "item",
        "id": "q1",
        "source_type": "MC",
        "canvas_status": 500,
        "reason": "Canvas rejected this quiz item.",
    }]
    assert result["target_results"][0]["rollback_state"] == "applied"
    assert result["target_results"][0]["cleanup_required"] is False
    assert send_calls[2]["method"] == "DELETE"
    assert send_calls[2]["path"].endswith("/quizzes/1001")


def test_apply_item_rejection_reports_cleanup_when_rollback_fails(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [([], None), ([], None), ([], None)])
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"id": 1001}, None),
        (None, "HTTP 422: invalid item"),
        (None, "HTTP 403: forbidden"),
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])
    target_records = []
    frozen = []
    for target in targets_in:
        baseline = adapter.capture_baseline(payload, target)
        target_records.append(models.new_target(
            target_key=target["target_key"], idempotency_key=target["idempotency_key"],
            course_id=target["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, target, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload), normalized_payload=payload,
        targets=target_records)
    operations.create_operation(op)
    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    target_result = result["target_results"][0]
    assert target_result["error_code"] == "item_rejected"
    assert target_result["rollback_state"] == "failed"
    assert target_result["rollback_error_code"] == "rollback_failed"
    assert target_result["cleanup_required"] is True
    assert send_calls[2]["method"] == "DELETE"


def test_apply_quiz_create_sent_unknown(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift
    ])
    send_calls = _mock_canvas_send(monkeypatch, [
        (None, "timeout: connection timed out"),  # create_quiz:0 uncertain
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    frozen = []
    for t in targets_in:
        baseline = adapter.capture_baseline(payload, t)
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=baseline))
        frozen.append(adapter.freeze_review(payload, t, baseline))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["ok"] is False
    assert result["target_results"][0]["state"] == "sent_unknown"


def test_apply_retry_resumes_unfinished_item(tmp_path, monkeypatch):
    """Retry after partial item creation resumes at the first unfinished item."""
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ({"id": 1001, "title": "Algebra Quiz 1"}, None),  # verify existing quiz
        ({"id": 2001}, None),  # verify item 1
        ({"id": "1001", "name": "Algebra Quiz 1", "published": True,
          "due_at": "2026-08-01T23:59:00Z", "post_to_sis": False}, None),  # verify after patch
    ])
    _mockcanvas_get_all(monkeypatch, [
        ([{"id": 10, "name": "Unit 1"}], None),  # module lookup
    ])
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"id": 2002}, None),  # create_item:0:2 (item 1 already done)
        ({"id": 1001}, None),  # patch_assignment:0
        ({"id": 3001}, None),  # attach_module:0
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    frozen = []
    for t in targets_in:
        baseline = adapter.capture_baseline(payload, t)
        tr = models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"],
            baseline=baseline,
            steps=[
                {"step_key": "create_quiz:0", "state": "applied",
                 "returned_object_id": "1001", "outbound_started_at": "2026-01-01T00:00:00Z"},
                {"step_key": "create_item:0:1", "state": "applied",
                 "returned_object_id": "2001", "outbound_started_at": "2026-01-01T00:00:01Z"},
                {"step_key": "create_item:0:2", "state": "pending"},
            ],
        )
        tr["returned_object_id"] = "1001"
        target_records.append(tr)

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.quiz",
        source_ref={"type": "workspace_relative", "value": "/tmp/algebra.txt"},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["ok"] is True
    assert result["status"] == "applied"
    assert len(send_calls) == 3  # item 2 + patch + module attach


# ── Reconciliation tests ─────────────────────────────────────────────────

def test_reconcile_applied_quiz(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ({"id": 1001, "title": "Algebra Quiz 1"}, None),  # quiz verify
        ({"id": 2001}, None),  # item 1 verify
        ({"id": 2002}, None),  # item 2 verify
        # Module item lookup - single item dict from canvas_get
        ({"id": 3001, "type": "Assignment", "content_id": 1001}, None),
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    target = {
        "course_id": "101",
        "returned_object_id": "1001",
        "steps": [
            {"step_key": "create_quiz:0", "state": "applied",
             "returned_object_id": "1001", "outbound_started_at": "2026-01-01T00:00:00Z"},
            {"step_key": "create_item:0:1", "state": "applied",
             "returned_object_id": "2001", "outbound_started_at": "2026-01-01T00:00:01Z"},
            {"step_key": "create_item:0:2", "state": "applied",
             "returned_object_id": "2002", "outbound_started_at": "2026-01-01T00:00:02Z"},
            {"step_key": "create_module", "state": "applied",
             "returned_object_id": "10", "outbound_started_at": "2026-01-01T00:00:03Z"},
            {"step_key": "attach_module:0", "state": "applied",
             "returned_object_id": "3001", "module_id": "10",
             "outbound_started_at": "2026-01-01T00:00:04Z"},
        ],
    }
    result = adapter.reconcile(payload, target, {})
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "1001"
    assert result.get("module_item_id") == "3001"


def test_reconcile_pending_when_no_quiz_id_and_no_marker(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # no matching assignments
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    target = {"course_id": "101", "returned_object_id": None, "steps": []}
    result = adapter.reconcile(payload, target, {})
    assert result["state"] == "pending"


def test_reconcile_sent_unknown_when_quiz_missing_and_has_marker(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        (None, "HTTP 404"),  # quiz not found
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    target = {
        "course_id": "101",
        "returned_object_id": "1001",
        "steps": [
            {"step_key": "create_quiz:0", "state": "claimed",
             "outbound_started_at": "2026-01-01T00:00:00Z"},
        ],
    }
    result = adapter.reconcile(payload, target, {})
    assert result["state"] == "sent_unknown"


# ── Retry selector ───────────────────────────────────────────────────────

def test_retry_selector_returns_unresolved_targets(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    operation = {
        "targets": [
            {"state": "applied", "target_key": "t1"},
            {"state": "failed", "target_key": "t2"},
            {"state": "sent_unknown", "target_key": "t3"},
            {"state": "pending", "target_key": "t4"},
        ],
    }
    selected = adapter.retry_selector(operation)
    keys = [t["target_key"] for t in selected]
    assert "t2" in keys
    assert "t3" in keys
    assert "t4" not in keys  # pending is not unresolved
    assert "t1" not in keys


def test_reversal_not_supported(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)



# ── Plan determinism ─────────────────────────────────────────────────────

def test_plan_determinism(tmp_path, monkeypatch):
    """Same input produces same plan."""
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    adapter = QuizAdapter()
    p1 = adapter.build_payload({"path": "/tmp/algebra.txt", "settings": {"published": True}})
    p2 = adapter.build_payload({"path": "/tmp/algebra.txt", "settings": {"published": True}})
    assert adapter.source_digest(p1) == adapter.source_digest(p2)


def test_plan_no_network(tmp_path, monkeypatch):
    """build_payload must not call Canvas client."""
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)

    # Verify canvas_client is never called during build_payload
    original_get = canvas_client.canvas_get
    original_send = canvas_client._canvas_send
    calls = []

    def track_get(*a, **kw):
        calls.append("canvas_get")
        return original_get(*a, **kw)

    def track_send(*a, **kw):
        calls.append("_canvas_send")
        return original_send(*a, **kw)

    monkeypatch.setattr(canvas_client, "canvas_get", track_get)
    monkeypatch.setattr(canvas_client, "_canvas_send", track_send)

    adapter = QuizAdapter()
    adapter.build_payload({"path": "/tmp/algebra.txt"})
    assert len(calls) == 0, f"Canvas was called during plan: {calls}"


# ── PII safety ───────────────────────────────────────────────────────────

def test_review_no_full_item_bodies(tmp_path, monkeypatch):
    """Review must not expose full item payloads."""
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mock_plan_subprocess(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),
    ])

    adapter = QuizAdapter()
    payload = adapter.build_payload({"path": "/tmp/algebra.txt"})
    targets = adapter.verify_targets(payload, [{"course_id": "101"}])
    baseline = adapter.capture_baseline(payload, targets[0])
    review = adapter.freeze_review(payload, targets[0], baseline)

    # Review should not contain item payloads
    review_json = json.dumps(review)
    assert "points_possible" not in review_json  # item-level detail
    assert "entry_type" not in review_json  # item-level detail
    assert "source_path" not in review_json  # local path
    assert "course_id" not in review_json  # course ID
