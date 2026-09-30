"""Tests for the content.assignment_update operation adapter.

Patterns follow test_assignment_operation.py: private-root redirection, a
fake Canvas transport at the canvas_client seam, and adapter lifecycle
verification. This kind is a sibling to content.assignment (assignment.py
stays untouched -- see docs/handoffs/assignment-update-write-path.md), not a
variant of it: no title matching, patch semantics only, and identity is the
caller-supplied Canvas assignment_id.
"""
import pytest

from api.operation_ledger import claims, models, operations, paths, registry
from api.operation_ledger.adapters.assignment_update import AssignmentUpdateAdapter
from api.operation_ledger.executor import ExecutionContext


def _root(tmp_path, monkeypatch):
    root = tmp_path / "local-private"
    monkeypatch.setattr(paths, "private_root", lambda: root)
    return root


def _fake_courses():
    return [{"id": 42, "name": "Fictional Course"}]


_LIVE_ASSIGNMENT = {
    "id": 24680,
    "name": "Found Poetry",
    "html_url": "https://c/42/assignments/24680",
    "updated_at": "2026-09-01T12:00:00Z",
    "published": False,
    "due_at": None,
    "unlock_at": None,
    "lock_at": None,
}


def _fake_canvas_get(path, params=None, timeout=20):
    if "assignments/24680" in path:
        return dict(_LIVE_ASSIGNMENT), None
    if "assignments/missing" in path:
        return None, "HTTP 404: Not Found"
    return None, None


def _fake_canvas_send_ok(method, path, payload, timeout=30):
    assert method == "PUT"
    return {
        "id": 24680,
        "html_url": "https://canvas.invalid/courses/42/assignments/24680",
        **payload.get("assignment", {}),
    }, None


# ── Protocol conformance ─────────────────────────────────────────────────

def test_adapter_is_registered():
    adapter = registry.get_adapter("content.assignment_update")
    assert adapter is not None
    assert adapter.kind == "content.assignment_update"


# ── Payload / patch semantics ────────────────────────────────────────────

def test_build_payload_requires_assignment_id():
    adapter = AssignmentUpdateAdapter()
    with pytest.raises(ValueError, match="assignment_id is required"):
        adapter.build_payload({"published": True})


def test_build_payload_refuses_when_no_field_supplied():
    """Acceptance criterion 5: no updatable field means refused, not a no-op apply."""
    adapter = AssignmentUpdateAdapter()
    with pytest.raises(ValueError, match="at least one of"):
        adapter.build_payload({"assignment_id": "24680"})


def test_build_payload_carries_only_the_supplied_fields():
    adapter = AssignmentUpdateAdapter()
    payload = adapter.build_payload({
        "assignment_id": "24680",
        "due_at": "2026-09-11T23:59:00Z",
    })
    assert payload["fields"] == {"due_at": "2026-09-11T23:59:00Z"}
    assert "published" not in payload["fields"]
    assert "unlock_at" not in payload["fields"]
    assert "lock_at" not in payload["fields"]


def test_build_payload_none_published_is_not_supplied():
    adapter = AssignmentUpdateAdapter()
    payload = adapter.build_payload({
        "assignment_id": "24680", "published": None, "due_at": "2026-09-11T23:59:00Z",
    })
    assert "published" not in payload["fields"]


def test_build_payload_false_published_is_still_supplied():
    """False is a real, meaningful value -- only None/absent means leave alone."""
    adapter = AssignmentUpdateAdapter()
    payload = adapter.build_payload({"assignment_id": "24680", "published": False})
    assert payload["fields"]["published"] is False


# ── Baseline / drift ──────────────────────────────────────────────────────

def test_capture_baseline_reads_the_live_assignment(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )
    adapter = AssignmentUpdateAdapter()
    payload = {"assignment_id": "24680", "fields": {"published": True}}
    baseline = adapter.capture_baseline(payload, {"course_id": "42"})
    assert baseline["assignment"]["updated_at"] == "2026-09-01T12:00:00Z"
    assert baseline["assignment"]["name"] == "Found Poetry"
    # Never reads or carries the fields this path must not touch.
    assert "description" not in baseline["assignment"]
    assert "points_possible" not in baseline["assignment"]
    assert "assignment_group_id" not in baseline["assignment"]


def test_preview_from_dates_are_the_base_dates_not_an_overrides(monkeypatch):
    """Law: Canvas reports an override's dates on the default teacher read, so the
    frozen "from" values must come from the override_assignment_dates=false read."""
    base = {**_LIVE_ASSIGNMENT, "due_at": "2026-09-29T21:00:00Z", "lock_at": "2026-09-29T21:00:00Z"}
    overridden = {**base, "due_at": "2026-10-02T21:00:00Z", "lock_at": "2026-10-02T21:00:00Z"}

    def canvas_get(path, params=None, timeout=20):
        wants_base = (params or {}).get("override_assignment_dates") == "false"
        return dict(base if wants_base else overridden), None

    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get", canvas_get)
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.config.active_courses", _fake_courses)
    adapter = AssignmentUpdateAdapter()
    payload = {"assignment_id": "24680", "fields": {
        "due_at": "2026-10-02T21:00:00Z", "lock_at": "2026-10-02T21:00:00Z"}}
    baseline = adapter.capture_baseline(payload, {"course_id": "42"})
    review = adapter.freeze_review(payload, {"course_id": "42"}, baseline)
    assert {"field": "due_at", "from": "2026-09-29T21:00:00Z", "to": "2026-10-02T21:00:00Z"} in review["changes"]
    assert {"field": "lock_at", "from": "2026-09-29T21:00:00Z", "to": "2026-10-02T21:00:00Z"} in review["changes"]


def test_capture_baseline_reports_a_canvas_error(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )
    adapter = AssignmentUpdateAdapter()
    payload = {"assignment_id": "missing", "fields": {"published": True}}
    baseline = adapter.capture_baseline(payload, {"course_id": "42"})
    assert "canvas_error" in baseline


def test_drift_detected_when_frozen_updated_at_differs_from_live(monkeypatch):
    """Law: drift blocks apply exactly when the live updated_at moved since preview."""
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )
    adapter = AssignmentUpdateAdapter()
    payload = {"assignment_id": "24680", "fields": {"published": True}}
    frozen_stale = {"assignment": {"updated_at": "2020-01-01T00:00:00Z"}}
    assert adapter.check_drift(payload, {"course_id": "42"}, frozen_stale) is True


def test_no_drift_when_frozen_updated_at_matches_live(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )
    adapter = AssignmentUpdateAdapter()
    payload = {"assignment_id": "24680", "fields": {"published": True}}
    frozen_current = {"assignment": {"updated_at": "2026-09-01T12:00:00Z"}}
    assert adapter.check_drift(payload, {"course_id": "42"}, frozen_current) is False


def test_drift_when_stored_baseline_has_no_frozen_assignment():
    adapter = AssignmentUpdateAdapter()
    assert adapter.check_drift(
        {"assignment_id": "24680", "fields": {"published": True}},
        {"course_id": "42"}, {},
    ) is True


def test_drift_when_fresh_read_errors(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )
    adapter = AssignmentUpdateAdapter()
    payload = {"assignment_id": "missing", "fields": {"published": True}}
    frozen = {"assignment": {"updated_at": "2026-09-01T12:00:00Z"}}
    assert adapter.check_drift(payload, {"course_id": "42"}, frozen) is True


# ── Freeze review ─────────────────────────────────────────────────────────

def test_freeze_review_shows_a_field_diff_row_per_changed_field(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.config.active_courses",
        _fake_courses,
    )
    adapter = AssignmentUpdateAdapter()
    payload = {"assignment_id": "24680", "fields": {
        "published": True, "due_at": "2026-09-11T23:59:00Z",
    }}
    baseline = {"assignment": {
        "name": "Found Poetry", "published": False, "due_at": None,
    }}
    review = adapter.freeze_review(payload, {"course_id": "42"}, baseline)
    assert review["course_name"] == "Fictional Course"
    assert review["assignment_name"] == "Found Poetry"
    assert {"field": "published", "from": False, "to": True} in review["changes"]
    assert {"field": "due_at", "from": None, "to": "2026-09-11T23:59:00Z"} in review["changes"]
    assert len(review["changes"]) == 2


# ── Execute ────────────────────────────────────────────────────────────────

def _claimed_context(op, target, adapter, payload):
    claim = claims.acquire_claim(
        target_key=target["target_key"],
        operation_id=op["operation_id"],
        payload_digest=models.sha256_dict(payload),
    )
    context = ExecutionContext(
        operation_id=op["operation_id"], target_key=target["target_key"], claim=claim,
    )
    return claim, context


def test_execute_sends_only_the_supplied_fields_in_the_put_body(tmp_path, monkeypatch):
    """Law: a field absent from the payload never appears in the PUT body, so
    description/points_possible/assignment_group_id can never be flattened."""
    _root(tmp_path, monkeypatch)
    sent = []

    def recording_send(method, path, payload, timeout=30):
        sent.append((method, path, payload))
        return _fake_canvas_send_ok(method, path, payload, timeout)

    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client._canvas_send",
        recording_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )

    adapter = AssignmentUpdateAdapter()
    payload = adapter.build_payload({
        "assignment_id": "24680", "due_at": "2026-09-11T23:59:00Z",
    })

    op = models.new_operation(
        operation_id="op-au-execute",
        kind=adapter.kind,
        source_ref=None,
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload,
        targets=[models.new_target(
            target_key=adapter.target_key(payload, "42"),
            idempotency_key=adapter.idempotency_key(payload, "42"),
            course_id="42",
        )],
    )
    operations.create_operation(op)
    target = op["targets"][0]
    baseline = adapter.capture_baseline(payload, target)
    claim, context = _claimed_context(op, target, adapter, payload)

    result = adapter.execute(payload, target, baseline, claim, context)

    assert result["state"] == "applied"
    assert result["returned_object_id"] == "24680"
    assert len(sent) == 1
    method, path, request = sent[0]
    assert method == "PUT"
    assert "assignments/24680" in path
    assert request == {"assignment": {"due_at": "2026-09-11T23:59:00Z"}}
    assert "published" not in request["assignment"]
    assert "description" not in request["assignment"]
    assert "points_possible" not in request["assignment"]
    assert "assignment_group_id" not in request["assignment"]


def test_execute_handles_canvas_rejection(tmp_path, monkeypatch):
    _root(tmp_path, monkeypatch)

    def fail_send(method, path, payload, timeout=30):
        return None, "HTTP 400: bad request"

    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client._canvas_send",
        fail_send,
    )
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )

    adapter = AssignmentUpdateAdapter()
    payload = adapter.build_payload({"assignment_id": "24680", "published": True})
    op = models.new_operation(
        operation_id="op-au-fail",
        kind=adapter.kind,
        source_ref=None,
        source_digest=adapter.source_digest(payload),
        normalized_payload=payload,
        targets=[models.new_target(
            target_key=adapter.target_key(payload, "42"),
            idempotency_key=adapter.idempotency_key(payload, "42"),
            course_id="42",
        )],
    )
    operations.create_operation(op)
    target = op["targets"][0]
    baseline = adapter.capture_baseline(payload, target)
    claim, context = _claimed_context(op, target, adapter, payload)

    result = adapter.execute(payload, target, baseline, claim, context)
    assert result["state"] == "failed"
    assert result["error_code"] == "canvas_rejected"


# ── Reconcile / retry ──────────────────────────────────────────────────────

def test_reconcile_confirms_from_a_live_get(monkeypatch):
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment_update.canvas_client.canvas_get",
        _fake_canvas_get,
    )
    adapter = AssignmentUpdateAdapter()
    result = adapter.reconcile(
        {"assignment_id": "24680", "fields": {"published": True}},
        {"course_id": "42"}, {},
    )
    assert result["state"] == "applied"
    assert result["returned_object_id"] == "24680"


def test_retry_selector_picks_unresolved():
    adapter = AssignmentUpdateAdapter()
    operation = {"targets": [
        {"target_key": "tk-1", "state": "applied"},
        {"target_key": "tk-2", "state": "blocked"},
    ]}
    selected = {t["target_key"] for t in adapter.retry_selector(operation)}
    assert selected == {"tk-2"}
