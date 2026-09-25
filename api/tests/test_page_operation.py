"""PageForge page adapter tests — prepare, review, apply, retry, reconcile.

Tests the full operation-ledger lifecycle for ``content.page`` using mocked
Canvas calls. No live Canvas interaction.
"""
import json
import os
import time

import pytest

from api.operation_ledger import (
    batches, claims, executor, models, operations, paths, registry, storage,
)
from api.operation_ledger.adapters import PageAdapter
from api.platform_services import canvas_client, config
from api.webui import pf


# ── Fixtures ─────────────────────────────────────────────────────────────

PAGEFORGE_VALID = """<PAGEFORGE_JSON>
{
  "version": "2.0-json",
  "type": "PAGE",
  "title": "Test Page",
  "layout": "standard",
  "overview": "<p>Hello world</p>"
}
</PAGEFORGE_JSON>"""

PAGEFORGE_INVALID = """<PAGEFORGE_JSON>
{
  "version": "2.0-json",
  "type": "PAGE",
  "title": "",
  "overview": ""
}
</PAGEFORGE_JSON>"""


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(paths, "private_root", lambda: root)
    return root


def _write_pageforge(tmp_path, content=PAGEFORGE_VALID, name="test.pageforge.json"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _mock_active_courses(monkeypatch, courses=None):
    if courses is None:
        courses = [
            {"id": "101", "name": "Course A", "active": True},
            {"id": "102", "name": "Course B", "active": True},
        ]
    monkeypatch.setattr(config, "active_courses", lambda: courses)


def _mock_canvas_send(monkeypatch, responses=None, errors=None):
    """Mock _canvas_send with a queue of (response, error) tuples.

    ``responses`` is a list of (dict, None) or (None, error_str) tuples.
    Each call pops the next one. If exhausted, returns (None, "no more mock responses").
    """
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


# ── Prepare tests ───────────────────────────────────────────────────────

def test_prepare_with_valid_file(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True})
    assert payload["title"] == "Test Page"
    assert "<p>Hello world</p>" in payload["body"]
    assert payload["published"] is True
    assert payload["source_path"] == path

    digest = adapter.source_digest(payload)
    assert len(digest) == 64  # SHA-256 hex


def test_page_uses_untiered_color_during_prepare_and_freezes_it(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)
    colors = {"Support": "silver", "Core": "red", "Accelerate": "blue", "untiered": "purple"}
    monkeypatch.setattr(config, "get_tier_colors", lambda: dict(colors))
    adapter = PageAdapter()
    request = {"path": path}
    payload = adapter.build_payload(request)
    assert "#63428f" in payload["body"]
    digest = adapter.source_digest(payload)
    colors["untiered"] = "orange"
    assert "#63428f" in payload["body"]
    assert digest == adapter.source_digest(payload)
    updated = adapter.build_payload(request)
    assert "#a44a12" in updated["body"]
    assert adapter.source_digest(updated) != digest

    targets = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])
    assert len(targets) == 2
    assert targets[0]["target_key"] != targets[1]["target_key"]
    assert targets[0]["idempotency_key"] != targets[1]["idempotency_key"]


def test_prepare_with_invalid_file(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    path = _write_pageforge(tmp_path, PAGEFORGE_INVALID, "bad.pageforge.json")

    adapter = PageAdapter()
    with pytest.raises(ValueError):
        adapter.build_payload({"path": path, "published": False})


def test_prepare_with_unknown_kind():
    from api.operation_ledger import registry
    with pytest.raises(ValueError, match="unknown operation kind"):
        registry.get_adapter("content.nonexistent")


def test_prepare_unknown_course_rejected(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch, [{"id": "101", "name": "Only Course", "active": True}])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})
    with pytest.raises(ValueError, match="not in active courses"):
        adapter.verify_targets(payload, [{"course_id": "999"}])


# ── Review tests ────────────────────────────────────────────────────────

def test_review_freezes_per_target_summaries(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # course 101: no existing page
        ([], None),  # course 102: no existing page
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True, "module_name": "Unit 1"})
    targets = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])

    frozen = []
    for t in targets:
        baseline = adapter.capture_baseline(payload, t)
        frozen.append(adapter.freeze_review(payload, t, baseline))

    assert len(frozen) == 2
    assert frozen[0]["course_name"] == "Course A"
    assert frozen[0]["page_title"] == "Test Page"
    assert frozen[0]["published"] is True
    assert frozen[0]["module_name"] == "Unit 1"
    assert frozen[0]["baseline_has_existing_page"] is False


def test_batch_review_digest_computed(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    _mockcanvas_get(monkeypatch, [([], None), ([], None)])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})
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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
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

def test_apply_creates_pages(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    # Setup capture_baseline (2 courses) + apply-time capture_baseline (2) +
    # check_drift (2) + _get_page_html_url (2)
    _mockcanvas_get(monkeypatch, [
        ([], None), ([], None),  # setup capture_baseline for 2 courses
        ([], None), ([], None),  # apply-time capture_baseline for 2 courses
        ([], None), ([], None),  # check_drift for 2 courses (no existing page → no drift)
        ({"html_url": "http://canvas/pages/test"}, None),  # _get_page_html_url course 101
        ({"html_url": "http://canvas/pages/test"}, None),  # _get_page_html_url course 102
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])

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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    # Mock Canvas POST for page creation
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"url": "test-page", "html_url": "http://canvas/courses/101/pages/test-page"}, None),
        ({"url": "test-page", "html_url": "http://canvas/courses/102/pages/test-page"}, None),
    ])

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert len(result["target_results"]) == 2
    for tr in result["target_results"]:
        assert tr["state"] == "applied"
        assert tr["returned_object_id"] == "test-page"

    # Verify Canvas POST was called for each course
    assert len(send_calls) == 2
    assert "/api/v1/courses/101/pages" in send_calls[0]["path"]
    assert "/api/v1/courses/102/pages" in send_calls[1]["path"]
    stored = operations.get_operation(op_id)
    assert stored["targets"][0]["baseline"] == target_records[0]["baseline"]
    assert stored["targets"][0]["apply_baseline"] == {"existing_page": None}
    assert stored["targets"][0]["steps"][0]["state"] == "applied"
    assert stored["targets"][0]["steps"][0]["returned_object_id"] == "test-page"


def test_apply_with_drift_blocks(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True})

    # Baseline at review time: no existing page
    baseline_no_page = {"existing_page": None}

    # At apply time, drift check will call canvas_get and find a modified page
    target = models.new_target(
        target_key="tk-1", idempotency_key="ik-1", course_id="101",
        baseline=baseline_no_page)

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=[target])

    # Set a review so apply can proceed
    batch = batches.freeze_batch([op_id], {op_id: [
        adapter.freeze_review(payload, target, baseline_no_page)]})
    operations.create_operation(op)
    operations.set_operation_review(op_id, batch)

    # At apply time: capture_baseline finds a page, then check_drift detects it
    # capture_baseline (1 course) + check_drift (1 course)
    _mockcanvas_get(monkeypatch, [
        ([{"title": "Test Page", "body": "DIFFERENT", "published": False}], None),
        ([{"title": "Test Page", "body": "DIFFERENT", "published": False}], None),
    ])

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["status"] == "attention"
    assert result["target_results"][0]["state"] == "blocked"
    assert result["target_results"][0]["error_code"] == "drift_detected"


def test_partial_failure(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    # Setup capture_baseline (2) + apply-time capture_baseline (2) + check_drift (2) +
    # _get_page_html_url (1 success)
    _mockcanvas_get(monkeypatch, [
        ([], None), ([], None),  # setup capture_baseline
        ([], None), ([], None),  # apply-time capture_baseline
        ([], None), ([], None),  # check_drift (no existing page → no drift)
        ({"html_url": "http://canvas/101/pages/test-page"}, None),  # _get_page_html_url for success
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])

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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    # First course succeeds, second fails
    _mock_canvas_send(monkeypatch, [
        ({"url": "test-page", "html_url": "http://canvas/101/pages/test-page"}, None),
        (None, "HTTP 400: Bad Request"),
    ])

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["status"] == "partial"
    assert result["target_results"][0]["state"] == "applied"
    assert result["target_results"][1]["state"] == "failed"


def test_timeout_produces_sent_unknown(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    # Setup capture_baseline (1) + apply-time capture_baseline (1) + check_drift (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift (no existing page → no drift)
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})
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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    # Canvas call times out
    _mock_canvas_send(monkeypatch, [
        (None, "Connection timeout: read timed out"),
    ])

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["status"] == "attention"
    assert result["target_results"][0]["state"] == "sent_unknown"
    assert result["target_results"][0]["error_code"] == "timeout_or_disconnect"


# ── Retry tests ──────────────────────────────────────────────────────────

def test_retry_only_unresolved(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    # Setup capture_baseline (2) + first apply capture_baseline (2) + check_drift (2) +
    # _get_page_html_url (1 success)
    _mockcanvas_get(monkeypatch, [
        ([], None), ([], None),  # setup capture_baseline
        ([], None), ([], None),  # first apply capture_baseline
        ([], None), ([], None),  # check_drift
        ({"html_url": "http://canvas/101/pages/test-page"}, None),  # _get_page_html_url for course 101
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}, {"course_id": "102"}])

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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    # First apply: course 101 succeeds, course 102 times out
    _mock_canvas_send(monkeypatch, [
        ({"url": "test-page", "html_url": "http://canvas/101/pages/test-page"}, None),
        (None, "Connection timeout"),
    ])
    result1 = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result1["status"] == "attention"
    assert result1["target_results"][0]["state"] == "applied"
    assert result1["target_results"][1]["state"] == "sent_unknown"

    # Retry: only course 102 should be retried
    # capture_baseline (1) + check_drift (1) + _get_page_html_url (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # capture_baseline for course 102
        ([], None),  # check_drift (no existing page → no drift)
        ({"html_url": "http://canvas/102/pages/test-page"}, None),  # _get_page_html_url
    ])
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"url": "test-page", "html_url": "http://canvas/102/pages/test-page"}, None),
    ])
    result2 = executor.retry_operation(op_id)

    assert result2["status"] == "applied"
    # Only one Canvas POST should have been made (for course 102)
    assert len(send_calls) == 1
    assert "/api/v1/courses/102/pages" in send_calls[0]["path"]


# ── Idempotent skip ──────────────────────────────────────────────────────

def test_idempotent_skip_with_returned_id(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    # Target already has a returned_object_id from a previous attempt
    target = models.new_target(
        target_key=targets_in[0]["target_key"],
        idempotency_key=targets_in[0]["idempotency_key"],
        course_id="101", baseline={"existing_page": None})
    target["returned_object_id"] = "existing-slug"

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=[target])
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: [
        adapter.freeze_review(payload, target, {"existing_page": None})]})
    operations.set_operation_review(op_id, batch)

    # Setup doesn't call capture_baseline (baseline is set manually).
    # Apply: capture_baseline (1) + check_drift (1) + page exists by slug (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # capture_baseline at apply time
        ([], None),  # check_drift (no existing page → no drift)
        ({"url": "existing-slug", "title": "Test Page"}, None),  # page exists by slug → skip
    ])

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["status"] == "applied"
    assert result["target_results"][0]["state"] == "applied"


# ── Same-title is not proof ─────────────────────────────────────────────

def test_same_title_is_not_proof(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    # Target has no returned_object_id (sent_unknown from a crash)
    target = models.new_target(
        target_key=targets_in[0]["target_key"],
        idempotency_key=targets_in[0]["idempotency_key"],
        course_id="101", baseline={"existing_page": None})
    target["state"] = "sent_unknown"

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=[target])
    operations.create_operation(op)

    # Reconcile: Canvas returns a page with the same title
    _mockcanvas_get(monkeypatch, [
        ([{"title": "Test Page", "body": "<p>Hello world</p>"}], None),
    ])

    result = adapter.reconcile(payload, target, {"existing_page": None})
    assert result["state"] == "sent_unknown"  # same-title is never proof


def test_reconcile_no_title_match_returns_pending(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})

    target = {"course_id": "101", "returned_object_id": None}
    _mockcanvas_get(monkeypatch, [
        ([{"title": "Different Page", "body": "other"}], None),
    ])

    result = adapter.reconcile(payload, target, {})
    assert result["state"] == "pending"


def test_reconcile_with_slug_proves_applied(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    adapter = PageAdapter()
    target = {"course_id": "101", "returned_object_id": "my-slug"}
    _mockcanvas_get(monkeypatch, [
        ({"url": "my-slug", "title": "Test Page", "html_url": "http://canvas/pages/my-slug"}, None),
    ])
    result = adapter.reconcile({"title": "Test Page"}, target, {})
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "my-slug"


def test_reconcile_with_slug_404_returns_pending(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    adapter = PageAdapter()
    target = {"course_id": "101", "returned_object_id": "my-slug"}
    _mockcanvas_get(monkeypatch, [
        (None, "HTTP 404: Not Found"),
    ])
    result = adapter.reconcile({"title": "Test Page"}, target, {})
    assert result["state"] == "pending"


# ── Module attachment ────────────────────────────────────────────────────

def test_module_attachment_after_page(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    # Setup capture_baseline (1) + apply-time capture_baseline (1) + check_drift (1) +
    # _get_page_html_url (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift (no existing page → no drift)
        ({"html_url": "http://canvas/101/pages/test-page"}, None),  # _get_page_html_url
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True, "module_name": "Unit 1"})
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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    # Mock: page creation, module list (find existing), module item POST
    checkpoint_observations = []
    def fake_get_all(path, params=None, timeout=30):
        stored = operations.get_operation(op_id)
        checkpoint_observations.append(stored["targets"][0]["steps"])
        return ([{"id": 55, "name": "Unit 1"}], None)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake_get_all)
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"url": "test-page", "html_url": "http://canvas/101/pages/test-page"}, None),  # create page
        ({"id": 77}, None),  # add module item
    ])

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["status"] == "applied"
    assert len(send_calls) == 2
    assert "/pages" in send_calls[0]["path"]
    assert "/modules/55/items" in send_calls[1]["path"]
    assert checkpoint_observations[0][0]["step_key"] == "create_page"
    assert checkpoint_observations[0][0]["returned_object_id"] == "test-page"
    stored = operations.get_operation(op_id)
    assert stored["targets"][0]["steps"][-1]["returned_object_id"] == "77"


def test_retry_resumes_module_attachment(tmp_path, monkeypatch):
    """Retry uses recorded page ID, does not create a second page."""
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True, "module_name": "Unit 1"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    # Target has returned_object_id from a previous attempt (page was created
    # but module attachment was sent_unknown)
    target = models.new_target(
        target_key=targets_in[0]["target_key"],
        idempotency_key=targets_in[0]["idempotency_key"],
        course_id="101", baseline={"existing_page": None})
    target["returned_object_id"] = "test-page"
    target["state"] = "sent_unknown"

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=[target])
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: [
        adapter.freeze_review(payload, target, {"existing_page": None})]})
    operations.set_operation_review(op_id, batch)

    # At retry: capture_baseline (1) + check_drift (1) + page exists by slug → skip create
    _mockcanvas_get(monkeypatch, [
        ([], None),  # capture_baseline at apply time
        ([], None),  # check_drift (no existing page → no drift)
        ({"url": "test-page", "title": "Test Page"}, None),  # page exists by slug → skip
    ])
    _mockcanvas_get_all(monkeypatch, [([{"id": 55, "name": "Unit 1"}], None)])
    send_calls = _mock_canvas_send(monkeypatch, [
        ({"id": 77}, None),  # module item POST only — no page creation
    ])

    result = executor.retry_operation(op_id)

    assert result["status"] == "applied"
    # Only one Canvas POST: module item, NOT page creation
    assert len(send_calls) == 1
    assert "/modules/55/items" in send_calls[0]["path"]
    assert "/pages" not in send_calls[0]["path"]


# ── Reversal ────────────────────────────────────────────────────────────



# ── Receipt written per apply ───────────────────────────────────────────

def test_receipt_written_per_apply(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    # Setup capture_baseline (1) + apply-time capture_baseline (1) + check_drift (1) +
    # _get_page_html_url (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift (no existing page → no drift)
        ({"html_url": "http://canvas/101/pages/test-page"}, None),  # _get_page_html_url
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})
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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    _mock_canvas_send(monkeypatch, [
        ({"url": "test-page", "html_url": "http://canvas/101/pages/test-page"}, None),
    ])

    executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    # Check receipt was written
    from api.operation_ledger import receipts
    receipt_list = receipts.list_receipts()
    assert len(receipt_list) == 1
    assert receipt_list[0]["subject_type"] == "operation"
    assert receipt_list[0]["subject_id"] == op_id
    assert receipt_list[0]["kind"] == "content.page"
    assert receipt_list[0]["status"] == "applied"


def test_adapter_exceptions_are_private_and_do_not_stop_later_targets(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    adapter = PageAdapter()
    targets = [
        models.new_target(target_key="tk-101", idempotency_key="ik-101", course_id="101",
                          baseline={"existing_page": None}),
        models.new_target(target_key="tk-102", idempotency_key="ik-102", course_id="102",
                          baseline={"existing_page": None}),
    ]
    payload = {"title": "Test Page", "body": "<p>Body</p>", "published": False,
               "module_name": None, "source_path": "fixture.pageforge.json"}
    op_id = models.new_operation_id()
    operation = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": "fixture.pageforge.json"},
        source_digest=adapter.source_digest(payload), normalized_payload=payload,
        targets=targets)
    operations.create_operation(operation)
    monkeypatch.setattr(registry, "get_adapter", lambda kind: adapter)
    batch = batches.freeze_batch([op_id], {op_id: [
        adapter.freeze_review(payload, target, target["baseline"]) for target in targets]})
    operations.set_operation_review(op_id, batch)
    monkeypatch.setattr(adapter, "capture_baseline", lambda payload, target: {"existing_page": None})
    monkeypatch.setattr(adapter, "check_drift", lambda payload, target, baseline: False)

    def execute(payload, target, baseline, claim, context):
        if target["course_id"] == "101":
            raise RuntimeError("private failure text")
        return {"state": "applied", "steps": []}

    monkeypatch.setattr(adapter, "execute", execute)
    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["status"] == "partial"
    stored = operations.get_operation(op_id)
    assert stored["targets"][0]["error_code"] == "adapter_exception"
    assert stored["targets"][0]["private_diagnostic"] == "RuntimeError"
    assert stored["targets"][1]["state"] == "applied"
    from api.operation_ledger import receipts
    assert receipts.list_receipts()[0]["status"] == "partial"


def test_adapter_exception_after_marker_is_attention(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch, [{"id": "101", "name": "Course A", "active": True}])
    adapter = PageAdapter()
    payload = {"title": "Test Page", "body": "<p>Body</p>", "published": False,
               "module_name": None, "source_path": "fixture.pageforge.json"}
    target = models.new_target(target_key="tk-101", idempotency_key="ik-101", course_id="101",
                               baseline={"existing_page": None})
    op_id = models.new_operation_id()
    operation = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": "fixture.pageforge.json"},
        source_digest=adapter.source_digest(payload), normalized_payload=payload,
        targets=[target])
    operations.create_operation(operation)
    monkeypatch.setattr(registry, "get_adapter", lambda kind: adapter)
    batch = batches.freeze_batch([op_id], {op_id: [
        adapter.freeze_review(payload, target, target["baseline"]) ]})
    operations.set_operation_review(op_id, batch)
    monkeypatch.setattr(adapter, "capture_baseline", lambda payload, target: {"existing_page": None})
    monkeypatch.setattr(adapter, "check_drift", lambda payload, target, baseline: False)

    def execute(payload, target, baseline, claim, context):
        context.before_send("create_page", "outbound-digest")
        raise RuntimeError("must not be returned")

    monkeypatch.setattr(adapter, "execute", execute)
    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])
    assert result["status"] == "attention"
    stored = operations.get_operation(op_id)
    assert stored["targets"][0]["state"] == "sent_unknown"
    assert stored["targets"][0]["error_code"] == "adapter_exception_after_send"
    assert stored["targets"][0]["steps"][0]["outbound_started_at"]


# ── PII minimization in GET /api/operations ─────────────────────────────

def test_pii_minimization_in_list_operations(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": False})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    target_records = []
    for t in targets_in:
        target_records.append(models.new_target(
            target_key=t["target_key"], idempotency_key=t["idempotency_key"],
            course_id=t["course_id"], baseline=None))

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    minimized = operations.list_operations_pii_minimized()
    text = json.dumps(minimized)
    # No course IDs
    assert "101" not in text
    # No payload
    assert "Test Page" not in text
    assert "normalized_payload" not in text
    # No target details
    assert "target_key" not in text
    assert "idempotency_key" not in text


# ── Missing module-item ID is not success ───────────────────────────────

def test_attach_without_item_id_is_sent_unknown(tmp_path, monkeypatch):
    """A POST response without an id must not be checkpointed as applied."""
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    # capture_baseline (1) + apply-time capture_baseline (1) + check_drift (1) +
    # _get_page_html_url (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # setup capture_baseline
        ([], None),  # apply-time capture_baseline
        ([], None),  # check_drift
        ({"html_url": "http://canvas/101/pages/test-page"}, None),  # _get_page_html_url
    ])
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True, "module_name": "Unit 1"})
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
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=target_records)
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: frozen})
    operations.set_operation_review(op_id, batch)

    # Page creation succeeds, module found, module item POST returns no id.
    _mockcanvas_get_all(monkeypatch, [([{"id": 55, "name": "Unit 1"}], None)])
    _mock_canvas_send(monkeypatch, [
        ({"url": "test-page", "html_url": "http://canvas/101/pages/test-page"}, None),
        ({}, None),  # module item POST — no id in response
    ])

    result = executor.apply_operation(op_id, batch["batch_id"], batch["review_digest"])

    assert result["status"] == "attention"
    assert result["target_results"][0]["state"] == "sent_unknown"
    assert result["target_results"][0]["error_code"] == "unparseable_response"
    stored = operations.get_operation(op_id)
    attach = next(s for s in stored["targets"][0]["steps"]
                  if s["step_key"] == "attach_module")
    assert attach["state"] == "sent_unknown"


def test_previously_applied_attach_without_item_id_is_sent_unknown(tmp_path, monkeypatch):
    """A previously applied attachment with no item ID must not be treated
    as applied on retry — it must be sent_unknown."""
    _root(tmp_path, monkeypatch)
    _mock_active_courses(monkeypatch)
    path = _write_pageforge(tmp_path)

    adapter = PageAdapter()
    payload = adapter.build_payload({"path": path, "published": True, "module_name": "Unit 1"})
    targets_in = adapter.verify_targets(payload, [{"course_id": "101"}])

    # Target has page created and attach_module step marked applied but no item_id.
    target = models.new_target(
        target_key=targets_in[0]["target_key"],
        idempotency_key=targets_in[0]["idempotency_key"],
        course_id="101", baseline={"existing_page": None})
    target["returned_object_id"] = "test-page"
    target["state"] = "sent_unknown"
    target["steps"] = [
        {"step_key": "create_page", "state": "applied",
         "returned_object_id": "test-page"},
        {"step_key": "attach_module", "state": "applied",
         "module_id": "55", "returned_object_id": None},
    ]

    op_id = models.new_operation_id()
    op = models.new_operation(
        operation_id=op_id, kind="content.page",
        source_ref={"type": "workspace_relative", "value": path},
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload, targets=[target])
    operations.create_operation(op)

    batch = batches.freeze_batch([op_id], {op_id: [
        adapter.freeze_review(payload, target, {"existing_page": None})]})
    operations.set_operation_review(op_id, batch)

    # capture_baseline (1) + check_drift (1) + page exists by slug (1)
    _mockcanvas_get(monkeypatch, [
        ([], None),  # capture_baseline
        ([], None),  # check_drift
        ({"url": "test-page", "title": "Test Page"}, None),  # page exists
    ])
    _mockcanvas_get_all(monkeypatch, [([{"id": 55, "name": "Unit 1"}], None)])
    send_calls = _mock_canvas_send(monkeypatch, [])

    result = executor.retry_operation(op_id)
    # The previously applied attachment without an item ID must not be
    # treated as applied — it must be sent_unknown without re-sending.
    assert result["status"] == "attention"
    assert result["target_results"][0]["state"] == "sent_unknown"
    assert result["target_results"][0]["error_code"] == "module_item_exact_id_unverified"
    assert len(send_calls) == 0
