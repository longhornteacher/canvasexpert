"""PII-negative tests for tiered AssignmentForge operation behavior."""
import copy
import json

import pytest

from api.operation_ledger import models, operations, paths, recovery
from api.operation_ledger.executor import _project_target_results, _receipt_targets
from api.operation_ledger.adapters.assignment import AssignmentAdapter
from api.operation_ledger.adapters.assignment_groups import (
    GroupResolutionError, resolve_assignment_groups,
)


TIERS = [
    {"label": "Support", "group": "Blue", "title": "Practice", "description": "support body"},
    {"label": "Extend", "group": "Gold", "title": "Practice", "description": "extend body"},
]


def _get_all(path, params=None, timeout=30):
    if "group_categories" in path:
        return [{"id": 10, "name": " Blue "}, {"id": 20, "name": "GOLD"}], None
    if "/groups/10/memberships" in path:
        return [{"user_id": 9001}, {"user_id": 9002}], None
    if "/groups/20/memberships" in path:
        return [{"user_id": 9003}], None
    if "/enrollments" in path:
        return [{"user_id": 9001}, {"user_id": 9002}, {"user_id": 9003}], None
    if "/assignments" in path:
        return [], None
    return [], None


def _resolved():
    return resolve_assignment_groups(
        "42", TIERS, canvas_get_all=_get_all, selected_category_id="7"
    )


def test_group_resolver_safe_snapshot_and_source_order():
    result = _resolved()
    assert [row["label"] for row in result["safe"]["tiers"]] == ["Support", "Extend"]
    assert [row["student_count"] for row in result["safe"]["tiers"]] == [2, 1]
    assert result["student_ids_by_group"] == {"10": ["9001", "9002"], "20": ["9003"]}
    safe_text = json.dumps(result["safe"])
    assert all(value not in safe_text for value in ("9001", "9002", "9003"))


def test_permission_fallback_filters_selected_category():
    calls = []
    def getter(path, params=None, timeout=30):
        calls.append(path)
        if "group_categories" in path:
            return None, "HTTP 403: forbidden"
        if path.endswith("/groups"):
            return [
                {"id": 10, "name": "Blue", "group_category_id": 7},
                {"id": 20, "name": "Gold", "group_category_id": 7},
                {"id": 30, "name": "Blue", "group_category_id": 8},
            ], None
        return _get_all(path, params, timeout)
    result = resolve_assignment_groups("42", TIERS, canvas_get_all=getter, selected_category_id="7")
    assert result["safe"]["selected_category_id"] == "7"
    assert "/api/v1/courses/42/groups" in calls


@pytest.mark.parametrize("case", ["missing", "ambiguous", "empty", "overlap", "coverage", "read"])
def test_group_resolution_fail_closed(case):
    if case == "missing":
        with pytest.raises(GroupResolutionError, match="Select"):
            resolve_assignment_groups("42", TIERS, canvas_get_all=_get_all, selected_category_id="")
        return
    def getter(path, params=None, timeout=30):
        data, error = _get_all(path, params, timeout)
        if case == "ambiguous" and "group_categories" in path:
            data.append({"id": 11, "name": "blue"})
        if case == "empty" and "/groups/10/memberships" in path:
            data = []
        if case == "overlap" and "/groups/20/memberships" in path:
            data = [{"user_id": 9002}, {"user_id": 9003}]
        if case == "coverage" and "/enrollments" in path:
            data.append({"user_id": 9004})
        if case == "read" and "group_categories" in path:
            return None, "HTTP 500"
        return data, error
    with pytest.raises(GroupResolutionError):
        resolve_assignment_groups("42", TIERS, canvas_get_all=getter, selected_category_id="7")


class Context:
    def __init__(self):
        self.steps = []
    def before_send(self, key, digest):
        step = models.new_step(key)
        step["state"] = "claimed"
        step["payload_digest"] = digest
        self._put(step)
        return copy.deepcopy(step)
    def checkpoint_step(self, step, returned_object_id=None, returned_object_url=None):
        step = copy.deepcopy(step)
        if returned_object_id is not None:
            step["returned_object_id"] = returned_object_id
        if returned_object_url is not None:
            step["returned_object_url"] = returned_object_url
        self._put(step)
        return step
    def _put(self, step):
        self.steps = [row for row in self.steps if row["step_key"] != step["step_key"]] + [copy.deepcopy(step)]


def _payload(**extra):
    value = {
        "name": "Practice", "description": "base", "points": 10.0,
        "submission_types": ["online_text_entry"], "published": False,
        "post_to_sis": False, "tiers": copy.deepcopy(TIERS),
    }
    value.update(extra)
    return value


def _completed_step(key, returned_id, **extra):
    step = models.new_step(key)
    step.update({
        "state": "applied", "returned_object_id": str(returned_id),
        "outbound_started_at": "2026-01-01T00:00:00+00:00", **extra,
    })
    return step


def test_review_is_safe(monkeypatch):
    adapter = AssignmentAdapter()
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.config.active_courses", lambda: [{"id": 42, "name": "Course"}])
    baseline = {"group_snapshot": _resolved()["safe"], "existing_assignments": []}
    review = adapter.freeze_review(_payload(), {"course_id": "42"}, baseline)
    assert review["tiered"] is True and review["tier_count"] == 2
    assert review["only_visible_to_overrides"] is True
    assert review["tiers"] == [
        {"label": "Support", "group": "Blue", "student_count": 2},
        {"label": "Extend", "group": "Gold", "student_count": 1},
    ]
    assert "9001" not in json.dumps(review)


def test_two_tier_write_order_and_transient_ids(monkeypatch):
    adapter = AssignmentAdapter()
    resolved = _resolved()
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.resolve_assignment_groups", lambda *a, **k: resolved)
    writes = []
    def send(method, path, body, timeout=30):
        writes.append((path, copy.deepcopy(body)))
        if path.endswith("/overrides"):
            return {"id": 700 + len(writes)}, None
        return {"id": 100 + len(writes), "html_url": "https://canvas.invalid/a"}, None
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client._canvas_send", send)
    context = Context()
    result = adapter.execute(
        _payload(), {"course_id": "42", "steps": []},
        {"group_snapshot": resolved["safe"], "existing_assignments": []}, {}, context,
    )
    assert result["state"] == "applied"
    assert ["overrides" in path for path, _ in writes] == [False, True, False, True]
    assert writes[0][1]["assignment"]["only_visible_to_overrides"] is True
    assert writes[2][1]["assignment"]["only_visible_to_overrides"] is True
    assert writes[1][1]["assignment_override"]["student_ids"] == ["9001", "9002"]
    assert writes[3][1]["assignment_override"]["student_ids"] == ["9003"]
    durable = json.dumps(context.steps)
    assert all(value not in durable for value in ("9001", "9002", "9003"))


@pytest.mark.parametrize("failure_index", [0, 1, 2, 3])
def test_timeout_or_missing_id_stops_downstream(monkeypatch, failure_index):
    adapter = AssignmentAdapter()
    resolved = _resolved()
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.resolve_assignment_groups", lambda *a, **k: resolved)
    calls = []
    def send(method, path, body, timeout=30):
        index = len(calls); calls.append(path)
        if index == failure_index:
            return (None, "connection timeout") if index % 2 == 0 else ({}, None)
        return {"id": 100 + index}, None
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client._canvas_send", send)
    result = adapter.execute(_payload(), {"course_id": "42", "steps": []}, {"group_snapshot": resolved["safe"]}, {}, Context())
    assert result["state"] == "sent_unknown"
    assert len(calls) == failure_index + 1


def test_definitive_second_tier_failure_is_partial(monkeypatch):
    adapter = AssignmentAdapter(); resolved = _resolved()
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.resolve_assignment_groups", lambda *a, **k: resolved)
    calls = []
    def send(method, path, body, timeout=30):
        calls.append(path)
        if len(calls) == 3:
            return None, "HTTP 400: rejected"
        return {"id": 100 + len(calls)}, None
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client._canvas_send", send)
    result = adapter.execute(_payload(), {"course_id": "42", "steps": []}, {"group_snapshot": resolved["safe"]}, {}, Context())
    assert result["state"] == "partial"
    assert [step["returned_object_id"] for step in result["steps"][:2]] == ["101", "102"]


def test_retry_verifies_exact_ids_and_resumes_without_duplicates(monkeypatch):
    adapter = AssignmentAdapter(); resolved = _resolved()
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.resolve_assignment_groups", lambda *a, **k: resolved)
    existing_steps = []
    for key, returned_id in (("create_tier_assignment:0", "101"), ("create_tier_override:0", "102")):
        step = models.new_step(key); step["state"] = "applied"; step["returned_object_id"] = returned_id
        existing_steps.append(step)
    gets = []
    def get(path, params=None, timeout=20):
        gets.append(path)
        return {"id": path.rsplit("/", 1)[-1], "html_url": "https://canvas.invalid/a"}, None
    writes = []
    def send(method, path, body, timeout=30):
        writes.append(path)
        return {"id": 200 + len(writes)}, None
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client.canvas_get", get)
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client._canvas_send", send)
    result = adapter.execute(
        _payload(), {"course_id": "42", "steps": existing_steps},
        {"group_snapshot": resolved["safe"]}, {}, Context(),
    )
    assert result["state"] == "applied"
    assert any("assignments/101" in path for path in gets)
    assert any("overrides/102" in path for path in gets)
    assert len(writes) == 2


def test_same_title_drift_allows_known_ids_and_blocks_unknown(monkeypatch):
    adapter = AssignmentAdapter(); resolved = _resolved()
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.resolve_assignment_groups", lambda *a, **k: resolved)
    rows = [{"id": 101, "name": "Practice"}]
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client.canvas_get_all", lambda *a, **k: (rows, None))
    step = models.new_step("create_tier_assignment:0"); step["state"] = "applied"; step["returned_object_id"] = "101"
    target = {"course_id": "42", "steps": [step]}
    stored = {"group_snapshot": resolved["safe"], "existing_assignments": []}
    assert adapter.check_drift(_payload(), target, stored) is False
    rows.append({"id": 999, "name": "Practice"})
    assert adapter.check_drift(_payload(), target, stored) is True


def test_safe_step_projections_are_allowlisted():
    step = {
        "step_key": "create_tier_override:0", "state": "applied",
        "returned_object_id": "102", "returned_object_url": None,
        "error_code": None, "payload_digest": "secret-digest",
        "private_diagnostic": "private", "student_ids": ["9001"],
    }
    target = {
        "target_key": "safe-key", "state": "applied", "steps": [step],
        "course_id": "42", "private_diagnostic": "private",
    }
    for projection in (_receipt_targets([target]), _project_target_results([target])):
        rendered = json.dumps(projection)
        assert "create_tier_override:0" in rendered
        assert all(value not in rendered for value in ("9001", "secret-digest", "private", '"course_id"'))


def test_module_created_once_for_tier_assignments(monkeypatch):
    adapter = AssignmentAdapter(); resolved = _resolved()
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.resolve_assignment_groups", lambda *a, **k: resolved)
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.config.active_courses", lambda: [{"id": 42, "name": "Course"}])
    monkeypatch.setattr("api.operation_ledger.adapters.assignment._read_modules", lambda course_id: ([], None))
    sends = []
    def send(method, path, body, timeout=30):
        sends.append(path)
        return {"id": 300 + len(sends)}, None
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client._canvas_send", send)
    result = adapter.execute(
        _payload(module_name="Unit"),
        {"course_id": "42", "steps": []}, {"group_snapshot": resolved["safe"]}, {}, Context(),
    )
    assert result["state"] == "applied"
    assert sum(path.endswith("/modules") for path in sends) == 1
    assert sum("/items" in path for path in sends) == 2
    assert len([step for step in result["steps"] if step["step_key"].startswith("create_tier_assignment:")]) == 2


def test_reconcile_verifies_all_tier_dependency_ids(monkeypatch):
    adapter = AssignmentAdapter()
    payload = _payload(module_name="Unit")
    steps = [_completed_step("create_module", "500")]
    for index, (assignment_id, override_id, item_id) in enumerate(((101, 201, 301), (102, 202, 302))):
        steps.extend([
            _completed_step(f"create_tier_assignment:{index}", assignment_id),
            _completed_step(f"create_tier_override:{index}", override_id),
            _completed_step(f"attach_module:{index}", item_id, module_id="500"),
        ])
    def get(path, params=None, timeout=20):
        tail = path.rsplit("/", 1)[-1]
        if "/items/" in path:
            assignment_id = "101" if tail == "301" else "102"
            return {"id": tail, "type": "Assignment", "content_id": assignment_id}, None
        return {"id": tail, "html_url": "https://canvas.invalid/a"}, None
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.canvas_client.canvas_get", get)
    result = adapter.reconcile(payload, {"course_id": "42", "steps": steps}, {})
    assert result["state"] == "applied"
    assert [step["step_key"] for step in result["steps"]] == [
        "create_module", "create_tier_assignment:0", "create_tier_override:0",
        "attach_module:0", "create_tier_assignment:1",
        "create_tier_override:1", "attach_module:1",
    ]
    assert all(step["state"] == "applied" for step in result["steps"])
    rendered = json.dumps(result)
    assert all(value not in rendered for value in ("9001", "payload_digest", "private_diagnostic", "module_id"))


def test_reconcile_partial_tiers_stays_unresolved_without_send(monkeypatch):
    adapter = AssignmentAdapter()
    steps = [
        _completed_step("create_tier_assignment:0", "101"),
        _completed_step("create_tier_override:0", "201"),
        models.new_step("create_tier_assignment:1"),
    ]
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        lambda path, params=None, timeout=20: ({"id": path.rsplit("/", 1)[-1]}, None),
    )
    result = adapter.reconcile(_payload(), {"course_id": "42", "steps": steps}, {})
    assert result["state"] == "pending"
    assert [step["step_key"] for step in result["steps"]] == [
        "create_tier_assignment:0", "create_tier_override:0",
    ]


def test_restart_recovery_proves_tiers_and_authorizes_no_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    adapter = AssignmentAdapter()
    payload = _payload()
    steps = [
        _completed_step("create_tier_assignment:0", "101"),
        _completed_step("create_tier_override:0", "201"),
        _completed_step("create_tier_assignment:1", "102"),
        _completed_step("create_tier_override:1", "202"),
    ]
    target = models.new_target(
        target_key="tier-recovery", idempotency_key="tier-recovery-idem",
        course_id="42", baseline={"group_snapshot": _resolved()["safe"]},
        steps=steps,
    )
    target["state"] = "sent_unknown"
    op = models.new_operation(
        operation_id="op-tier-recovery", kind=adapter.kind, source_ref=None,
        source_digest=adapter.source_digest(payload), normalized_payload=payload,
        targets=[target],
    )
    operations.create_operation(op)
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        lambda path, params=None, timeout=20: ({"id": path.rsplit("/", 1)[-1]}, None),
    )
    sends = []
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client._canvas_send",
        lambda *args, **kwargs: sends.append(args) or ({"id": "unexpected"}, None),
    )
    summary = recovery.recover_pending_operations()
    stored = operations.get_operation("op-tier-recovery")
    assert summary == {"recovered": 1, "still_unknown": 0, "reset_to_pending": 0}
    assert stored["targets"][0]["state"] == "applied"
    assert all(step["state"] == "applied" for step in stored["targets"][0]["steps"])
    assert adapter.retry_selector(stored) == []
    assert sends == []
    assert "9001" not in json.dumps(stored)


def test_restart_recovery_partial_tiers_does_not_authorize_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    adapter = AssignmentAdapter(); payload = _payload()
    steps = [
        _completed_step("create_tier_assignment:0", "101"),
        _completed_step("create_tier_override:0", "201"),
        models.new_step("create_tier_assignment:1"),
    ]
    target = models.new_target(
        target_key="tier-partial", idempotency_key="tier-partial-idem",
        course_id="42", baseline={"group_snapshot": _resolved()["safe"]}, steps=steps,
    )
    target["state"] = "sent_unknown"
    operations.create_operation(models.new_operation(
        operation_id="op-tier-partial", kind=adapter.kind, source_ref=None,
        source_digest=adapter.source_digest(payload), normalized_payload=payload,
        targets=[target],
    ))
    monkeypatch.setattr(
        "api.operation_ledger.adapters.assignment.canvas_client.canvas_get",
        lambda path, params=None, timeout=20: ({"id": path.rsplit("/", 1)[-1]}, None),
    )
    summary = recovery.recover_pending_operations()
    stored = operations.get_operation("op-tier-partial")
    assert summary["still_unknown"] == 1
    assert stored["targets"][0]["state"] == "sent_unknown"
    assert stored["targets"][0]["steps"][2]["state"] == "pending"
