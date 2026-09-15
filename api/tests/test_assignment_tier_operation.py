"""Contract tests for differentiated AssignmentForge family delivery."""

import copy
import json

import pytest

from api.operation_ledger import models
from api.operation_ledger.adapters.assignment import AssignmentAdapter
from api.operation_ledger.adapters.assignment_groups import GroupResolutionError, resolve_assignment_groups
from api.platform_services import canvas_client, config


TIERS = [
    {"label": "Support", "group": "Blue", "description": "support body"},
    {"label": "Extend", "group": "Gold", "description": "extend body"},
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
    return [], None


def _resolved():
    return resolve_assignment_groups("42", TIERS, canvas_get_all=_get_all, selected_category_id="7")


def test_group_resolver_safe_snapshot_and_source_order():
    result = _resolved()
    assert [row["label"] for row in result["safe"]["tiers"]] == ["Support", "Extend"]
    assert [row["student_count"] for row in result["safe"]["tiers"]] == [2, 1]
    assert result["student_ids_by_group"] == {"10": ["9001", "9002"], "20": ["9003"]}
    assert all(value not in json.dumps(result["safe"]) for value in ("9001", "9002", "9003"))


@pytest.mark.parametrize("case", ["missing", "ambiguous", "empty", "overlap", "coverage", "read"])
def test_group_resolution_law_fails_closed(case):
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
        step = next((copy.deepcopy(row) for row in self.steps if row["step_key"] == key), models.new_step(key))
        step.update({"state": "claimed", "payload_digest": digest,
                     "outbound_started_at": "2026-09-14T12:00:00+00:00"})
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
        self.steps = [row for row in self.steps if row["step_key"] != step["step_key"]]
        self.steps.append(copy.deepcopy(step))


class FakeCanvas:
    def __init__(self):
        self.assignments = {}
        self.overrides = {}
        self.modules = {"501": {"id": "501", "name": "Week 1"}}
        self.module_items = {}
        self.sends = []
        self.reads = []
        self.next_id = 100

    def send(self, method, path, body, timeout=30):
        self.sends.append((method, path, copy.deepcopy(body)))
        self.next_id += 1
        if method == "POST" and path.endswith("/assignments"):
            assignment_id = str(self.next_id)
            row = {"id": assignment_id, "course_id": "42", "assignment_group_id": "77",
                   "html_url": f"https://canvas.invalid/a/{assignment_id}",
                   **copy.deepcopy(body["assignment"])}
            self.assignments[assignment_id] = row
            self.overrides[assignment_id] = []
            return copy.deepcopy(row), None
        if method == "POST" and path.endswith("/overrides"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            override = {"id": str(self.next_id), **copy.deepcopy(body["assignment_override"])}
            self.overrides[assignment_id].append(override)
            return copy.deepcopy(override), None
        if method == "POST" and path.endswith("/items"):
            module_id = path.split("/modules/")[1].split("/")[0]
            item = {"id": str(self.next_id), **copy.deepcopy(body["module_item"])}
            self.module_items[(module_id, item["id"])] = item
            return copy.deepcopy(item), None
        if method == "PUT" and "/assignments/" in path:
            assignment_id = path.rsplit("/", 1)[-1]
            self.assignments[assignment_id].update(copy.deepcopy(body["assignment"]))
            return copy.deepcopy(self.assignments[assignment_id]), None
        raise AssertionError((method, path, body))

    def get(self, path, params=None, timeout=20):
        self.reads.append(path)
        if "/modules/" in path and "/items/" in path:
            module_id, item_id = path.split("/modules/")[1].split("/items/")
            return copy.deepcopy(self.module_items.get((module_id, item_id))), None
        if "/assignments/" in path and "/overrides/" in path:
            assignment_id, override_id = path.split("/assignments/")[1].split("/overrides/")
            row = next((row for row in self.overrides.get(assignment_id, []) if str(row["id"]) == override_id), None)
            return copy.deepcopy(row), None
        if "/assignments/" in path:
            return copy.deepcopy(self.assignments.get(path.rsplit("/", 1)[-1])), None
        return [], None

    def get_all(self, path, params=None, timeout=30):
        self.reads.append(path)
        if path.endswith("/assignment_groups"):
            return [{"id": 77, "name": "Coursework"}], None
        if path.endswith("/modules"):
            return list(copy.deepcopy(self.modules).values()), None
        if path.endswith("/overrides"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            return copy.deepcopy(self.overrides.get(assignment_id, [])), None
        if path.endswith("/assignments"):
            search = str((params or {}).get("search_term") or "").casefold()
            return [copy.deepcopy(row) for row in self.assignments.values() if row["name"].casefold() == search], None
        return [], None


def _authoring_data():
    return {"title": "Practice", "description": "base", "points": 10, "tiers": copy.deepcopy(TIERS)}


def _build(monkeypatch, **request_overrides):
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.parse_file", lambda _path: (_authoring_data(), []))
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.tier_payloads", lambda data: copy.deepcopy(data["tiers"]))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue", "Accelerate": "Silver", "Extend": "Gold",
    })
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    request = {"path": "synthetic.txt", "due_at": "2026-09-14T15:30:00-05:00",
               "module_name": "Week 1", "assignment_group_name": "Coursework"}
    request.update(request_overrides)
    return AssignmentAdapter().build_payload(request)


def test_prepare_accepts_content_only_tiers_without_module_or_due(monkeypatch):
    payload = _build(monkeypatch, due_at="", module_name="")
    assert [row["label"] for row in payload["tiers"]] == ["Support", "Extend"]
    assert all("group" not in row for row in payload["tiers"])
    assert "module_name" not in payload


def test_prepare_preserves_ordinary_dates_for_each_tier(monkeypatch):
    payload = _build(
        monkeypatch,
        due_at="2026-09-14T15:30:00-05:00",
        unlock_at="2026-09-01T08:00:00-05:00",
        lock_at="2026-09-30T23:59:00-05:00",
    )
    assert payload["due_at"] == "2026-09-14T15:30:00-05:00"
    assert payload["unlock_at"] == "2026-09-01T08:00:00-05:00"
    assert payload["lock_at"] == "2026-09-30T23:59:00-05:00"


def test_prepare_requires_unique_public_tags(monkeypatch):
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.parse_file", lambda _path: (_authoring_data(), []))
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.tier_payloads", lambda data: copy.deepcopy(data["tiers"]))
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Red", "Core": "Blue", "Accelerate": "Silver", "Extend": " red ",
    })
    with pytest.raises(ValueError, match="unique"):
        AssignmentAdapter().build_payload({"path": "synthetic.txt", "due_at": "2026-09-14T15:30:00-05:00", "module_name": "Week 1"})


def test_differentiated_assignment_drafts_are_independent_and_student_free(monkeypatch):
    payload = _build(monkeypatch)
    fake = FakeCanvas()
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all", fake.get_all)

    baseline = AssignmentAdapter().capture_baseline(payload, {"course_id": "42", "steps": []})
    assert baseline == {"existing_assignments": []}
    context = Context()
    result = AssignmentAdapter().execute(payload, {"course_id": "42", "steps": []}, baseline, {}, context)

    assert result["state"] == "applied"
    sources = [row for row in fake.assignments.values() if row["name"] != "Practice"]
    assert [row["name"] for row in sources] == ["Practice - Red", "Practice - Gold"]
    assert all(not row["published"] and not row["only_visible_to_overrides"] for row in sources)
    assert all(not rows for rows in fake.overrides.values())
    assert not fake.module_items
    assert not any(any(term in path for term in ("group_categories", "/groups/", "/enrollments"))
                   for path in fake.reads)
    assert not any("/overrides" in path or "/modules" in path for _method, path, _body in fake.sends)
    assert all(value not in json.dumps(context.steps) for value in ("9001", "9002", "9003"))

    sends_before = len(fake.sends)
    retry = AssignmentAdapter().execute(payload, {"course_id": "42", "steps": copy.deepcopy(context.steps)}, baseline, {}, Context())
    assert retry["state"] == "applied"
    assert len(fake.sends) == sends_before


def test_whole_class_payload_has_no_bridge(monkeypatch):
    data = {"title": "Whole", "description": "body", "points": 10}
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.parse_file", lambda _path: (data, []))
    monkeypatch.setattr("api.operation_ledger.adapters.assignment.af.tier_payloads", lambda _data: [])
    payload = AssignmentAdapter().build_payload({"path": "whole.txt"})
    assert "tiers" not in payload
    assert "bridge_due_at" not in payload
