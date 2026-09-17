"""Tests for the Quick Assignment operation adapter.

Patterns follow test_operation_ledger.py: private-root redirection via
monkeypatch, mock Canvas API, and adapter lifecycle verification.
"""
import json

import pytest

from api.operation_ledger import (
    models, operations, paths, registry,
)
from api.operation_ledger.adapters.quick_assignment import (
    QuickAssignmentAdapter,
    _normalize,
)


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(paths, "private_root", lambda: root)
    return root


def _fake_courses():
    return [
        {"id": 101, "name": "Algebra 1"},
        {"id": 202, "name": "Biology"},
    ]


def _fakecanvas_get(path, params=None, timeout=20):
    if "assignments/24680" in path:
        return {"id": 24680, "name": "Exit Ticket",
                "html_url": "https://c/42/assignments/24680"}, None
    if "assignments" in path and "search_term" in (params or {}):
        search = (params or {}).get("search_term", "").lower()
        if "existing" in search:
            return [{"id": 999, "name": "Existing Quiz",
                     "html_url": "https://c/42/assignments/999",
                     "points_possible": 100, "published": False}], None
        return [], None
    if "assignment_groups" in path:
        return [{"id": 55, "name": "Homework"}], None
    return None, None


def _fake_canvas_send(method, path, payload, timeout=30):
    return {
        "id": 24680,
        "name": payload.get("assignment", {}).get("name", "Untitled"),
        "html_url": "https://canvas.invalid/courses/42/assignments/24680",
    }, None


def _fakecanvas_get_all(path, params=None, timeout=30):
    if "assignment_groups" in path:
        return [{"id": 55, "name": "Homework"}], None
    return [], None


# ── Protocol conformance ─────────────────────────────────────────────────

def test_adapter_is_registered():
    adapter = registry.get_adapter("content.quick_assignment")
    assert adapter is not None
    assert adapter.kind == "content.quick_assignment"


def test_payload_build_minimal(monkeypatch):
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({
        "name": "Exit \u2014 Ticket",
    })
    assert payload["name"] == "Exit - Ticket"
    assert payload["points"] == 100.0
    assert payload["submission_type"] == "none"
    assert payload["published"] is False
    assert "due_at" not in payload
    assert "assignment_group_name" not in payload


def test_payload_build_full(monkeypatch):
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({
        "name": "  Essay 1  ",
        "points": 50,
        "submission_type": "online_text_entry",
        "published": True,
        "due_at": "2026-08-01T23:59:00Z",
        "assignment_group_name": "Homework",
    })
    assert payload["name"] == "Essay 1"
    assert payload["points"] == 50.0
    assert payload["published"] is True
    assert payload["due_at"] == "2026-08-01T23:59:00Z"
    assert payload["assignment_group_name"] == "Homework"


def test_payload_build_raises_on_empty_name():
    adapter = QuickAssignmentAdapter()
    with pytest.raises(ValueError, match="name is required"):
        adapter.build_payload({})
    with pytest.raises(ValueError, match="name is required"):
        adapter.build_payload({"name": "   "})


def test_source_digest_is_deterministic():
    adapter = QuickAssignmentAdapter()
    payload = {"name": "Exit", "points": 100, "submission_type": "none",
               "published": False}
    d1 = adapter.source_digest(payload)
    d2 = adapter.source_digest(payload)
    assert d1 == d2
    # Changing a field changes the digest
    d3 = adapter.source_digest({**payload, "name": "Changed"})
    assert d1 != d3


# ── Target verification ─────────────────────────────────────────────────

def test_verify_targets_valid(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.config.active_courses",
        _fake_courses,
    )
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({"name": "Quiz 1"})
    targets = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "202"}])
    assert len(targets) == 2
    assert targets[0]["course_id"] == "101"
    assert targets[1]["course_id"] == "202"
    assert targets[0]["target_key"]
    assert targets[0]["idempotency_key"]


def test_verify_targets_rejects_unknown_course(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.config.active_courses",
        _fake_courses,
    )
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({"name": "Quiz 1"})
    with pytest.raises(ValueError, match="not in active courses"):
        adapter.verify_targets(payload, [{"course_id": "999"}])


def test_verify_targets_rejects_missing_course_id(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.config.active_courses",
        _fake_courses,
    )
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({"name": "Quiz 1"})
    with pytest.raises(ValueError, match="target missing course_id"):
        adapter.verify_targets(payload, [{}])


# ── Baseline / drift ────────────────────────────────────────────────────

def test_capture_baseline_no_existing(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({"name": "New Assignment"})
    baseline = adapter.capture_baseline(payload, {"course_id": "42"})
    assert baseline["existing_assignment"] is None


def test_capture_baseline_finds_existing(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({"name": "Existing Quiz"})
    baseline = adapter.capture_baseline(payload, {"course_id": "42"})
    assert baseline["existing_assignment"] is not None
    assert baseline["existing_assignment"]["id"] == "999"


def test_drift_detected_when_existing_found():
    adapter = QuickAssignmentAdapter()
    assert adapter.check_drift(
        {"name": "Existing Quiz"},
        {"course_id": "42"},
        {"existing_assignment": {"id": "999", "name": "Existing Quiz"}},
    ) is True


def test_no_drift_when_no_existing():
    adapter = QuickAssignmentAdapter()
    assert adapter.check_drift(
        {"name": "New Quiz"},
        {"course_id": "42"},
        {"existing_assignment": None},
    ) is False


# ── Freeze review ───────────────────────────────────────────────────────

def test_freeze_review(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.config.active_courses",
        _fake_courses,
    )
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({
        "name": "Exit Ticket", "points": 10,
        "submission_type": "online_text_entry", "published": True,
    })
    review = adapter.freeze_review(payload, {"course_id": "101"}, {"existing_assignment": None})
    assert review["course_name"] == "Algebra 1"
    assert review["assignment_name"] == "Exit Ticket"
    assert review["points"] == 10.0
    assert review["baseline_has_existing"] is False


def test_freeze_review_with_existing(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.config.active_courses",
        _fake_courses,
    )
    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({"name": "Exit Ticket"})
    review = adapter.freeze_review(
        payload, {"course_id": "101"},
        {"existing_assignment": {"id": "999", "html_url": "https://c/a/999"}},
    )
    assert review["baseline_has_existing"] is True
    assert review["baseline_existing_id"] == "999"


# ── Execute ──────────────────────────────────────────────────────────────

def test_execute_creates_assignment(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client._canvas_send",
        _fake_canvas_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.config.active_courses",
        _fake_courses,
    )

    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({
        "name": "Exit Ticket", "points": 10, "published": True,
    })

    # Create an operation to satisfy the ledger plumbing
    op = models.new_operation(
        operation_id="op-qa-execute",
        kind="content.quick_assignment",
        source_ref=None,
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload,
        targets=[
            models.new_target(
                target_key=adapter.target_key(payload, "42"),
                idempotency_key=adapter.idempotency_key(payload, "42"),
                course_id="42",
            )
        ],
    )
    operations.create_operation(op)

    target = op["targets"][0]
    baseline = adapter.capture_baseline(payload, target)
    assert baseline["existing_assignment"] is None

    # Build a minimal claim and context for the execution
    from api.operation_ledger.executor import ExecutionContext
    from api.operation_ledger import claims

    claim = claims.acquire_claim(
        target_key=target["target_key"],
        operation_id=op["operation_id"],
        payload_digest=models.sha256_dict(payload),
    )

    context = ExecutionContext(
        operation_id=op["operation_id"],
        target_key=target["target_key"],
        claim=claim,
    )

    result = adapter.execute(payload, target, baseline, claim, context)
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "24680"
    assert "assignments/24680" in (result.get("returned_object_url") or "")


def test_execute_handles_canvas_error(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)

    def fail_send(method, path, payload, timeout=30):
        return None, "HTTP 400: bad request"

    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client._canvas_send",
        fail_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )

    adapter = QuickAssignmentAdapter()
    payload = adapter.build_payload({"name": "Fail Assignment"})

    op = models.new_operation(
        operation_id="op-qa-fail",
        kind="content.quick_assignment",
        source_ref=None,
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload,
        targets=[
            models.new_target(
                target_key=adapter.target_key(payload, "42"),
                idempotency_key=adapter.idempotency_key(payload, "42"),
                course_id="42",
            )
        ],
    )
    operations.create_operation(op)

    target = op["targets"][0]
    baseline = adapter.capture_baseline(payload, target)

    from api.operation_ledger.executor import ExecutionContext
    from api.operation_ledger import claims

    claim = claims.acquire_claim(
        target_key=target["target_key"],
        operation_id=op["operation_id"],
        payload_digest=models.sha256_dict(payload),
    )

    context = ExecutionContext(
        operation_id=op["operation_id"],
        target_key=target["target_key"],
        claim=claim,
    )

    result = adapter.execute(payload, target, baseline, claim, context)
    assert result["state"] == "failed"
    assert result["error_code"] == "canvas_rejected"


# ── Reconcile ────────────────────────────────────────────────────────────

def test_reconcile_finds_assignment(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    adapter = QuickAssignmentAdapter()
    result = adapter.reconcile(
        {"name": "Exit Ticket"},
        {"course_id": "42", "returned_object_id": "24680"},
        {},
    )
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "24680"


# ── Retry / reversal ────────────────────────────────────────────────────

def test_retry_selector_picks_unresolved():
    adapter = QuickAssignmentAdapter()
    operation = {
        "targets": [
            {"target_key": "tk-1", "state": "applied"},
            {"target_key": "tk-2", "state": "failed"},
            {"target_key": "tk-3", "state": "sent_unknown"},
            {"target_key": "tk-4", "state": "skipped"},
        ]
    }
    selected = adapter.retry_selector(operation)
    keys = {t["target_key"] for t in selected}
    assert "tk-1" not in keys  # applied is terminal
    assert "tk-2" in keys      # failed is unresolved
    assert "tk-3" in keys      # sent_unknown is unresolved
    assert "tk-4" not in keys  # skipped is terminal




# ── Full prepare->verify->freeze->apply pipeline ─────────────────────────

def test_pipeline_with_mocks(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.config.active_courses",
        _fake_courses,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client.canvas_get",
        _fakecanvas_get,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client._canvas_send",
        _fake_canvas_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.quick_assignment.canvas_client.canvas_get_all",
        _fakecanvas_get_all,
    )

    adapter = registry.get_adapter("content.quick_assignment")
    assert adapter is not None

    # 1. Build payload
    payload = adapter.build_payload({
        "name": "Exit Ticket",
        "points": 10,
        "submission_type": "online_text_entry",
        "published": True,
    })
    assert payload["name"] == "Exit Ticket"

    # 2. Verify targets
    targets = adapter.verify_targets(payload, [
        {"course_id": "101"},
        {"course_id": "202"},
    ])
    assert len(targets) == 2

    # 3. Capture baselines
    bl1 = adapter.capture_baseline(payload, targets[0])
    assert bl1["existing_assignment"] is None

    # 4. Freeze review
    r1 = adapter.freeze_review(payload, targets[0], bl1)
    assert r1["course_name"] == "Algebra 1"
    assert r1["assignment_name"] == "Exit Ticket"

    # 5. No drift when no existing assignment
    assert adapter.check_drift(payload, targets[0], bl1) is False
