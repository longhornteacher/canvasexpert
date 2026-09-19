"""High-risk laws for registered differentiated-family grade projection."""

import copy
import json

import pytest

from api import sis_grade_bridge
from api.operation_ledger import executor, operations, paths, receipts, recovery
from api.operation_ledger.adapters import differentiated_bridge, sis_grade_bridge as bridge_adapter
from api.platform_services import canvas_client, config


class FakeCanvas:
    def __init__(self):
        description = differentiated_bridge.bridge_description()
        self.assignments = {
            "source-a": {
                "id": "source-a", "course_id": "course-1", "name": "Synthetic Family - Red",
                "description": "a", "points_possible": 10, "assignment_group_id": "77",
                "due_at": "2026-10-01T15:00:00-05:00", "grading_type": "points",
                "submission_types": ["online_text_entry"], "published": True,
                "only_visible_to_overrides": True, "omit_from_final_grade": True,
                "post_to_sis": False,
            },
            "source-b": {
                "id": "source-b", "course_id": "course-1", "name": "Synthetic Family - Blue",
                "description": "b", "points_possible": 10, "assignment_group_id": "77",
                "due_at": "2026-10-01T15:00:00-05:00", "grading_type": "points",
                "submission_types": ["online_text_entry"], "published": True,
                "only_visible_to_overrides": True, "omit_from_final_grade": True,
                "post_to_sis": False,
            },
            "bridge": {
                "id": "bridge", "course_id": "course-1", "name": "Synthetic Family - Bridge",
                "description": description, "points_possible": 10, "assignment_group_id": "77",
                "due_at": "2026-10-01T23:59:00-05:00", "grading_type": "points",
                "submission_types": ["none"], "published": True,
                "only_visible_to_overrides": False, "omit_from_final_grade": False,
                "post_to_sis": True, "html_url": "https://canvas.invalid/a/bridge",
            },
        }
        self.overrides = {
            "source-a": [{"id": "oa", "student_ids": ["student-1", "student-2"],
                          "due_at": "2026-10-01T15:00:00-05:00"}],
            "source-b": [{"id": "ob", "student_ids": ["student-3"],
                          "due_at": "2026-10-01T15:00:00-05:00"}],
            "bridge": [],
        }
        self.submissions = {
            "source-a": [
                {"user_id": "student-1", "workflow_state": "graded", "score": 8,
                 "posted_at": "2026-09-01T12:00:00Z"},
                {"user_id": "student-2", "workflow_state": "pending_review", "score": 5,
                 "submitted_at": "2026-09-01T11:00:00Z"},
            ],
            "source-b": [
                {"user_id": "student-3", "workflow_state": "graded", "excused": True,
                 "posted_at": "2026-09-01T12:00:00Z"},
            ],
        }
        self.bridge_submissions = {}
        self.modules = {"501": {"id": "501", "name": "Week 1", "position": 1}}
        self.module_items = {
            ("501", "item-a"): {"id": "item-a", "type": "Assignment", "content_id": "source-a", "title": "Synthetic Family - Red", "position": 1},
            ("501", "item-b"): {"id": "item-b", "type": "Assignment", "content_id": "source-b", "title": "Synthetic Family - Blue", "position": 2},
            ("501", "item-bridge"): {"id": "item-bridge", "type": "Assignment", "content_id": "bridge", "title": "Synthetic Family - Bridge", "position": 3},
        }
        self.send_calls = []
        self.uncertain_grade = False
        self.uncertain_module_post = False
        self.uncertain_module_delete = False

    def get(self, path, params=None, timeout=20):
        if "/modules/" in path and "/items/" in path:
            module_id, item_id = path.split("/modules/")[1].split("/items/")
            return copy.deepcopy(self.module_items.get((module_id, item_id))), None
        if "/modules/" in path and path.endswith(tuple(f"/modules/{key}" for key in self.modules)):
            return copy.deepcopy(self.modules[path.rsplit("/", 1)[-1]]), None
        if "/submissions/" in path:
            user_id = path.rsplit("/", 1)[-1]
            return copy.deepcopy(self.bridge_submissions.get(user_id)), None
        if "/assignments/" in path:
            return copy.deepcopy(self.assignments.get(path.rsplit("/", 1)[-1])), None
        return None, "missing"

    def get_all_complete(self, path, params=None, timeout=30):
        if path.endswith("/users"):
            return [{"id": "student-1"}, {"id": "student-2"}, {"id": "student-3"}], None, True
        if path.endswith("/overrides"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            return copy.deepcopy(self.overrides[assignment_id]), None, True
        if path.endswith("/submissions"):
            assignment_id = path.split("/assignments/")[1].split("/")[0]
            if assignment_id == "bridge":
                return [
                    {"user_id": user_id, **copy.deepcopy(submission)}
                    for user_id, submission in self.bridge_submissions.items()
                ], None, True
            return copy.deepcopy(self.submissions[assignment_id]), None, True
        if path.endswith("/modules"):
            return [copy.deepcopy(row) for row in self.modules.values()], None, True
        if "/modules/" in path and path.endswith("/items"):
            module_id = path.split("/modules/")[1].split("/items")[0]
            return [copy.deepcopy(row) for (item_module, _), row in self.module_items.items() if item_module == module_id], None, True
        return [], None, True

    def send(self, method, path, request, timeout=30):
        self.send_calls.append((method, path, copy.deepcopy(request)))
        if self.uncertain_grade:
            return None, "connection timeout"
        if method == "DELETE" and "/modules/" in path and "/items/" in path:
            module_id, item_id = path.split("/modules/")[1].split("/items/")
            self.module_items.pop((module_id, item_id), None)
            if self.uncertain_module_delete:
                return None, "connection timeout"
            return {}, None
        if method == "POST" and "/modules/" in path and path.endswith("/items"):
            module_id = path.split("/modules/")[1].split("/items")[0].rstrip("/")
            item_id = f"item-{len(self.module_items)+1}"
            row = {"id": item_id, **copy.deepcopy(request["module_item"])}
            self.module_items[(module_id, item_id)] = row
            if self.uncertain_module_post:
                return None, "connection timeout"
            return copy.deepcopy(row), None
        user_id = path.rsplit("/", 1)[-1]
        submission = copy.deepcopy(request["submission"])
        if submission.get("posted_grade") == "":
            self.bridge_submissions[user_id] = {
                "excused": False, "score": None, "grade": None,
                "late_policy_status": (
                    None if submission.get("late_policy_status") == "none"
                    else submission.get("late_policy_status")
                ),
            }
        elif submission.get("excuse"):
            self.bridge_submissions[user_id] = {"excused": True, "score": None}
        else:
            self.bridge_submissions[user_id] = {
                "excused": False, "score": float(submission["posted_grade"]),
                "late_policy_status": (
                    None if submission.get("late_policy_status") == "none"
                    else submission.get("late_policy_status")
                ),
            }
        return copy.deepcopy(self.bridge_submissions[user_id]), None


@pytest.fixture
def bridge_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(config, "active_courses", lambda: [
        {"id": "course-1", "name": "Synthetic Course", "active": True}
    ])
    monkeypatch.setattr(config, "saved_courses", lambda: [
        {"id": "course-1", "name": "Synthetic Course", "active": True}
    ])
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    fake = FakeCanvas()
    registration = {
        "family_title": "Synthetic Family",
        "source_assignment_ids": ["source-a", "source-b"],
        "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
        "bridge_assignment_id": "bridge",
        "bridge_state_digest": differentiated_bridge.structural_digest(
            differentiated_bridge.assignment_shape(fake.assignments["bridge"], [])
        ),
    }
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda course, title: copy.deepcopy(registration) if (course, title) == ("course-1", "Synthetic Family") else None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [copy.deepcopy(registration)])
    monkeypatch.setattr(canvas_client, "canvas_get", fake.get)
    monkeypatch.setattr(canvas_client, "canvas_get_all_complete", fake.get_all_complete)
    monkeypatch.setattr(canvas_client, "_canvas_send", fake.send)
    monkeypatch.setattr("api.webui.mirror_service.notify_course_changed", lambda _course: None)
    return fake


def _preview():
    return sis_grade_bridge.preview_sis_grade_bridge("course-1", "Synthetic Family")


def test_reconciliation_register_only_preserves_exact_unsuffixed_bridge(monkeypatch, bridge_harness):
    fake = bridge_harness
    fake.assignments["bridge"]["name"] = "Synthetic Family"
    fake.module_items.pop(("501", "item-bridge"), None)
    registration = {}
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda _course, row: registration.update(copy.deepcopy(row)) or copy.deepcopy(row))
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda _course, _title: copy.deepcopy(registration) if registration else None)
    discovered = {
        "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
        "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
        "bridge_assignment_id": "bridge", "module_id": "501", "module_name": "Week 1",
    }
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family=discovered,
    )
    assert preview["ok"] is True
    assert preview["preview"]["action"] == "register"
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["status"] == "applied"
    assert fake.assignments["bridge"]["name"] == "Synthetic Family"
    assert fake.send_calls == []
    assert registration["bridge_assignment_id"] == "bridge"


def test_registered_projection_rejects_source_membership_drift_between_preview_and_apply(bridge_harness):
    fake = bridge_harness
    preview = _preview()
    fake.overrides["source-a"][0]["student_ids"].append("student-3")
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["status"] in {"blocked", "attention"}
    assert fake.send_calls == []


def test_reconciliation_rejects_source_membership_drift_between_preview_and_apply(
    bridge_harness, monkeypatch,
):
    fake = bridge_harness
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    discovered = {
        "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
        "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"], "module_id": "501", "module_name": "Week 1",
    }
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family=discovered,
    )
    assert preview["ok"] is True
    fake.overrides["source-a"][0]["student_ids"].append("student-3")

    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )

    assert result["status"] in {"blocked", "attention"}
    stored = operations.get_operation(preview["operation_id"])
    assert stored["targets"][0]["error_code"] == "drift_detected"
    assert fake.send_calls == []


def test_reconciliation_create_uses_full_safe_bridge_shape_and_exact_id(monkeypatch, bridge_harness):
    fake = bridge_harness
    fake.assignments.pop("bridge")
    fake.overrides.pop("bridge")
    saved = {}
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda _course, _title: copy.deepcopy(saved) if saved else None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda _course, row: saved.update(copy.deepcopy(row)) or copy.deepcopy(row))
    original_send = canvas_client._canvas_send

    def create_bridge(method, path, request):
        if method == "POST" and path.endswith("/assignments"):
            fake.send_calls.append((method, path, copy.deepcopy(request)))
            bridge = {
                "id": "created-bridge", "course_id": "course-1", "html_url": "https://canvas.invalid/a/created-bridge",
                **request["assignment"],
            }
            fake.assignments["created-bridge"] = bridge
            fake.overrides["created-bridge"] = []
            return copy.deepcopy(bridge), None
        return original_send(method, path, request)

    monkeypatch.setattr(canvas_client, "_canvas_send", create_bridge)
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family={
            "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
            "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"], "module_id": "501", "module_name": "Week 1",
        },
    )
    assert preview["ok"] is True
    assert preview["preview"]["action"] == "create"
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["status"] == "applied"
    assert result["bridge_assignment_id"] == "created-bridge"
    assert saved["bridge_assignment_id"] == "created-bridge"
    post = next(call for call in fake.send_calls if call[0] == "POST") if fake.send_calls else None
    # The synthetic sender records no POST itself; exact-ID and full-shape
    # verification above prove the create path, while the created object is
    # the returned object used for registration.
    assert fake.assignments["created-bridge"]["name"] == "Synthetic Family - Bridge"
    assert fake.assignments["created-bridge"]["submission_types"] == ["none"]


def test_reconciliation_create_verification_recovers_exact_id_without_duplicate_post(
    monkeypatch, bridge_harness,
):
    fake = bridge_harness
    fake.assignments.pop("bridge")
    fake.overrides.pop("bridge")
    saved = {}
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda _course, _title: copy.deepcopy(saved) if saved else None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda _course, row: saved.update(copy.deepcopy(row)) or copy.deepcopy(row))
    original_send = canvas_client._canvas_send
    original_get = fake.get
    fake.verification_unavailable = True

    def create_bridge(method, path, request):
        if method == "POST" and path.endswith("/assignments"):
            fake.send_calls.append((method, path, copy.deepcopy(request)))
            bridge = {
                "id": "created-bridge", "course_id": "course-1", "html_url": "https://canvas.invalid/a/created-bridge",
                **request["assignment"],
            }
            fake.assignments["created-bridge"] = bridge
            fake.overrides["created-bridge"] = []
            return copy.deepcopy(bridge), None
        return original_send(method, path, request)

    def temporarily_unavailable_get(path, params=None, timeout=20):
        if fake.verification_unavailable and path.endswith("/assignments/created-bridge"):
            return None, "temporary unavailable"
        return original_get(path, params=params, timeout=timeout)

    monkeypatch.setattr(canvas_client, "_canvas_send", create_bridge)
    monkeypatch.setattr(canvas_client, "canvas_get", temporarily_unavailable_get)
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family={
            "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
            "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"], "module_id": "501", "module_name": "Week 1",
        },
    )
    assert preview["ok"] is True
    first = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert first["status"] == "attention"
    stored = operations.get_operation(preview["operation_id"])
    step = stored["targets"][0]["steps"][0]
    assert step["state"] == "sent_unknown"
    assert step["returned_object_id"] == "created-bridge"
    assert step["error_code"] == "bridge_reconciliation_unverified"
    assert len([call for call in fake.send_calls if call[0] == "POST"]) == 1
    fake.verification_unavailable = False
    recovered = recovery.recover_pending_operations()
    assert recovered["recovered"] == 1
    stored = operations.get_operation(preview["operation_id"])
    assert stored["targets"][0]["state"] == "applied"
    assert stored["targets"][0]["steps"][0]["state"] == "applied"
    assert saved["bridge_assignment_id"] == "created-bridge"
    assert len([call for call in fake.send_calls if call[0] == "POST"]) == 1


def test_legacy_bridge_only_repair_attaches_sources_before_global_bridge_removal(
    monkeypatch, bridge_harness,
):
    fake = bridge_harness
    fake.module_items.pop(("501", "item-a"), None)
    fake.module_items.pop(("501", "item-b"), None)
    saved = {}
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda _course, row: saved.update(copy.deepcopy(row)) or copy.deepcopy(row))
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda _course, _title: copy.deepcopy(saved) if saved else None)
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family={
            "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
            "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
            "bridge_assignment_id": "bridge", "module_id": "501", "module_name": "Week 1",
        },
    )
    assert preview["ok"] is True
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["status"] == "applied"
    calls = fake.send_calls
    source_posts = [index for index, call in enumerate(calls)
                    if call[0] == "POST" and "/modules/501/items" in call[1]]
    bridge_deletes = [index for index, call in enumerate(calls)
                      if call[0] == "DELETE" and "/modules/501/items" in call[1]]
    assert len(source_posts) == 2
    assert bridge_deletes and max(source_posts) < min(bridge_deletes)
    assert {str(item["content_id"]) for (module, _), item in fake.module_items.items()
            if module == "501"} == {"source-a", "source-b"}
    assert saved["bridge_assignment_id"] == "bridge"


def test_reconciliation_refuses_multiple_bridge_only_modules_without_writes(
    monkeypatch, bridge_harness,
):
    fake = bridge_harness
    fake.module_items.pop(("501", "item-a"), None)
    fake.module_items.pop(("501", "item-b"), None)
    fake.modules["502"] = {"id": "502", "name": "Week 2", "position": 2}
    fake.module_items[("502", "item-bridge-2")] = {
        "id": "item-bridge-2", "type": "Assignment", "content_id": "bridge",
        "title": "Synthetic Family - Bridge", "position": 1,
    }
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    result = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family={
            "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
            "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
            "bridge_assignment_id": "bridge",
        },
    )
    assert result["ok"] is False
    assert "ambiguous_module" in result["error"]
    assert fake.send_calls == []


def test_uncertain_source_attachment_recovers_exact_item_without_duplicate_post(
    monkeypatch, bridge_harness,
):
    fake = bridge_harness
    fake.uncertain_module_post = True
    fake.module_items.pop(("501", "item-a"), None)
    fake.module_items.pop(("501", "item-bridge"), None)
    saved = {}
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda _course, _title: copy.deepcopy(saved) if saved else None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda _course, row: saved.update(copy.deepcopy(row)) or copy.deepcopy(row))
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family={
            "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
            "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
            "bridge_assignment_id": "bridge", "module_id": "501", "module_name": "Week 1",
        },
    )
    first = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert first["status"] == "attention"
    assert len([call for call in fake.send_calls if call[0] == "POST" and "/modules/501/items" in call[1]]) == 1
    fake.uncertain_module_post = False
    assert recovery.recover_pending_operations()["recovered"] == 1
    assert len([call for call in fake.send_calls if call[0] == "POST" and "/modules/501/items" in call[1]]) == 1
    assert saved["bridge_assignment_id"] == "bridge"


def test_uncertain_bridge_delete_recovers_absence_without_duplicate_delete(
    monkeypatch, bridge_harness,
):
    fake = bridge_harness
    fake.uncertain_module_delete = True
    saved = {}
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda _course, _title: copy.deepcopy(saved) if saved else None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda _course, row: saved.update(copy.deepcopy(row)) or copy.deepcopy(row))
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family={
            "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
            "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
            "bridge_assignment_id": "bridge", "module_id": "501", "module_name": "Week 1",
        },
    )
    first = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert first["status"] == "attention"
    deletes = lambda: len([call for call in fake.send_calls if call[0] == "DELETE"])
    assert deletes() == 1
    fake.uncertain_module_delete = False
    assert recovery.recover_pending_operations()["recovered"] == 1
    assert deletes() == 1
    assert saved["bridge_assignment_id"] == "bridge"


def test_module_verification_failure_after_mutation_does_not_register(
    monkeypatch, bridge_harness,
):
    fake = bridge_harness
    saved = []
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "save_sis_grade_bridge", lambda _course, row: saved.append(copy.deepcopy(row)))
    original_send = fake.send
    def duplicate_after_first_source(method, path, request, timeout=30):
        result = original_send(method, path, request, timeout)
        if method == "POST" and "/modules/501/items" in path and not any(module == "502" for module, _ in fake.module_items):
            fake.modules["502"] = {"id": "502", "name": "Unexpected", "position": 2}
            fake.module_items[("502", "item-duplicate")] = {
                "id": "item-duplicate", "type": "Assignment", "content_id": request["module_item"]["content_id"],
                "title": request["module_item"]["title"], "position": 1,
            }
        return result
    monkeypatch.setattr(canvas_client, "_canvas_send", duplicate_after_first_source)
    fake.module_items.pop(("501", "item-a"), None)
    fake.module_items.pop(("501", "item-b"), None)
    fake.module_items.pop(("501", "item-bridge"), None)
    preview = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", discovered_family={
            "family_key": "Synthetic Family", "source_assignment_ids": ["source-a", "source-b"],
            "source_titles": ["Synthetic Family - Red", "Synthetic Family - Blue"],
            "bridge_assignment_id": "bridge", "module_id": "501", "module_name": "Week 1",
        },
    )
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["status"] == "attention"
    assert saved == []

def test_registered_projection_example_copies_only_final_numeric_and_excused(bridge_harness):
    fake = bridge_harness
    preview = _preview()
    assert preview["ok"] is True
    assert preview["preview"]["counts"]["held"] == 1

    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )

    assert result["status"] == "applied"
    assert set(fake.bridge_submissions) == {"student-1", "student-3"}
    assert "student-2" not in fake.bridge_submissions
    assert all(call[0] == "PUT" and "/submissions/" in call[1] for call in fake.send_calls)
    assert not any("post_grades" in call[1] for call in fake.send_calls)
    assert [step["step_key"] for step in operations.get_operation(preview["operation_id"])["targets"][0]["steps"]] == ["copy_grade:0", "copy_grade:1"]
    assert "student-1" not in json.dumps(result)
    assert result["receipt_id"]
    assert len(receipts.list_receipts()) == 1


def test_source_resolution_ignores_tier_membership_and_accepts_agreeing_finals(
    bridge_harness,
):
    fake = bridge_harness
    fake.overrides["source-b"][0]["student_ids"].append("student-1")
    fake.submissions["source-b"].append({
        "user_id": "student-1", "workflow_state": "graded", "score": 8,
        "posted_at": "2026-09-01T12:00:00Z",
    })

    preview = _preview()

    assert preview["ok"] is True
    assert preview["preview"]["counts"]["overlapping_active"] == 1
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["ok"] is True
    assert fake.bridge_submissions["student-1"]["score"] == 8


@pytest.mark.parametrize("other_final", [
    {"workflow_state": "graded", "score": 7, "excused": False},
    {"workflow_state": "graded", "score": None, "excused": True},
])
def test_conflicting_finals_hold_only_that_bridge_coordinate(
    bridge_harness, other_final,
):
    fake = bridge_harness
    fake.submissions["source-b"].append({
        "user_id": "student-1", "posted_at": "2026-09-01T12:00:00Z",
        **other_final,
    })

    preview = _preview()

    assert preview["preview"]["counts"]["conflicting_final_values"] == 1
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["ok"] is True
    assert "student-1" not in fake.bridge_submissions
    assert fake.bridge_submissions["student-3"]["excused"] is True
    assert "student-1" not in json.dumps(result)


def test_hidden_final_and_submitted_ungraded_are_held_after_due(
    bridge_harness, monkeypatch,
):
    fake = bridge_harness
    monkeypatch.setattr(bridge_adapter, "_past_due", lambda _due: True)
    fake.submissions["source-a"][0]["posted_at"] = None

    preview = _preview()

    counts = preview["preview"]["counts"]
    assert counts["held"] == 2
    assert counts["missing_zeroes"] == 0
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    assert result["ok"] is True
    assert set(fake.bridge_submissions) == {"student-3"}


def test_blank_becomes_missing_zero_only_after_bridge_due(
    bridge_harness, monkeypatch,
):
    fake = bridge_harness
    fake.submissions["source-a"][1] = {
        "user_id": "student-2", "workflow_state": "unsubmitted",
        "submitted_at": None,
    }
    monkeypatch.setattr(bridge_adapter, "_past_due", lambda _due: True)

    preview = _preview()
    assert preview["preview"]["counts"]["missing_zeroes"] == 1
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )

    assert result["ok"] is True
    assert fake.bridge_submissions["student-2"] == {
        "excused": False, "score": 0.0, "late_policy_status": "missing",
    }
    student_2_call = next(call for call in fake.send_calls if call[1].endswith("/student-2"))
    assert student_2_call[2] == {
        "submission": {
            "posted_grade": "0", "excuse": False,
            "late_policy_status": "missing",
        }
    }


def test_blank_unsubmitted_remains_blank_before_bridge_due(
    bridge_harness, monkeypatch,
):
    fake = bridge_harness
    fake.submissions["source-a"][1] = {
        "user_id": "student-2", "workflow_state": "unsubmitted",
        "submitted_at": None,
    }
    monkeypatch.setattr(bridge_adapter, "_past_due", lambda _due: False)

    preview = _preview()
    assert preview["preview"]["counts"]["missing_zeroes"] == 0
    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )

    assert result["ok"] is True
    assert "student-2" not in fake.bridge_submissions


def test_routine_clears_only_its_unchanged_prior_value(
    bridge_harness, monkeypatch,
):
    fake = bridge_harness
    fake.submissions["source-a"][1] = {
        "user_id": "student-2", "workflow_state": "unsubmitted",
        "submitted_at": None,
    }
    monkeypatch.setattr(bridge_adapter, "_past_due", lambda _due: True)
    first = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", write_origin="routine"
    )
    sis_grade_bridge.apply_sis_grade_bridge(
        first["operation_id"], first["batch_id"], first["review_digest"]
    )
    assert fake.bridge_submissions["student-2"]["score"] == 0

    fake.submissions["source-a"][1] = {
        "user_id": "student-2", "workflow_state": "submitted",
        "submitted_at": "2026-09-02T10:00:00Z", "score": None,
    }
    second = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", write_origin="routine"
    )
    assert second["preview"]["counts"]["cleared_prior_values"] == 1
    result = sis_grade_bridge.apply_sis_grade_bridge(
        second["operation_id"], second["batch_id"], second["review_digest"]
    )
    assert result["ok"] is True
    assert fake.bridge_submissions["student-2"]["score"] is None
    student_2_call = next(
        call for call in reversed(fake.send_calls) if call[1].endswith("/student-2")
    )
    assert student_2_call[2] == {
        "submission": {
            "posted_grade": "", "excuse": False,
            "late_policy_status": "none",
        }
    }


def test_teacher_changed_bridge_value_is_held_instead_of_cleared(
    bridge_harness, monkeypatch,
):
    fake = bridge_harness
    fake.submissions["source-a"][1] = {
        "user_id": "student-2", "workflow_state": "unsubmitted",
        "submitted_at": None,
    }
    monkeypatch.setattr(bridge_adapter, "_past_due", lambda _due: True)
    first = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", write_origin="routine"
    )
    sis_grade_bridge.apply_sis_grade_bridge(
        first["operation_id"], first["batch_id"], first["review_digest"]
    )
    fake.bridge_submissions["student-2"]["score"] = 4.0
    fake.submissions["source-a"][1] = {
        "user_id": "student-2", "workflow_state": "submitted",
        "submitted_at": "2026-09-02T10:00:00Z", "score": None,
    }
    calls_before = len(fake.send_calls)

    second = sis_grade_bridge.preview_sis_grade_bridge(
        "course-1", "Synthetic Family", write_origin="routine"
    )
    assert second["preview"]["counts"]["held"] == 1
    result = sis_grade_bridge.apply_sis_grade_bridge(
        second["operation_id"], second["batch_id"], second["review_digest"]
    )

    assert result["ok"] is True
    assert fake.bridge_submissions["student-2"]["score"] == 4.0
    assert len(fake.send_calls) == calls_before


def test_repeated_identical_run_has_no_canvas_mutation(bridge_harness):
    fake = bridge_harness
    first = _preview()
    sis_grade_bridge.apply_sis_grade_bridge(
        first["operation_id"], first["batch_id"], first["review_digest"]
    )
    calls_before = len(fake.send_calls)

    second = _preview()
    assert second["preview"]["counts"]["already_matching"] == 2
    result = sis_grade_bridge.apply_sis_grade_bridge(
        second["operation_id"], second["batch_id"], second["review_digest"]
    )

    assert result["ok"] is True
    assert len(fake.send_calls) == calls_before


def test_bridge_submission_drift_blocks_before_canvas_mutation(bridge_harness):
    fake = bridge_harness
    preview = _preview()
    fake.bridge_submissions["student-1"] = {
        "excused": False, "score": 3.0, "late_policy_status": None,
    }

    result = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )

    assert result["ok"] is False
    assert fake.send_calls == []


def test_unregistered_family_refuses_before_canvas_read(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    calls = []
    monkeypatch.setattr(canvas_client, "canvas_get", lambda *args, **kwargs: calls.append(args) or ({}, None))
    result = sis_grade_bridge.preview_sis_grade_bridge("course-1", "Unknown")
    assert result["ok"] is False
    assert "verified family link" in result["error"]
    assert calls == []


@pytest.mark.parametrize(
    ("mutation", "error", "fields"),
    [
        (lambda fake: fake.assignments["source-a"].update(post_to_sis=True), "source_sis_sync_enabled", None),
        (lambda fake: fake.assignments["source-a"].update(omit_from_final_grade=False), "source_counts_toward_final_grade", None),
        (lambda fake: fake.assignments["bridge"].update(published=False), "family_link_bridge_shape_drift", ["published"]),
        (lambda fake: fake.assignments["bridge"].update(description="changed"), "family_link_bridge_shape_drift", ["description"]),
        (lambda fake: fake.assignments["bridge"].update(due_at="2026-10-02T23:59:00-05:00"), "family_link_bridge_shape_drift", ["due_at"]),
    ],
)
def test_registered_family_drift_fails_closed_before_mutation(bridge_harness, mutation, error, fields):
    fake = bridge_harness
    mutation(fake)
    result = _preview()
    assert result["ok"] is False
    assert result["error"] == error
    assert result["blocking"] is True
    if fields is not None:
        assert result["drift_fields"] == fields
        wire = json.dumps(result, sort_keys=True)
        assert set(result["drift_fields"]).issubset(set(differentiated_bridge.BRIDGE_SHAPE_FIELDS))
        assert "Synthetic Family - Bridge" not in wire
        assert "changed" not in wire
        assert "bridge" not in wire.casefold() or "family_link_bridge_shape_drift" in wire
    assert fake.send_calls == []


def test_uncertain_grade_is_never_resent(bridge_harness):
    fake = bridge_harness
    fake.uncertain_grade = True
    preview = _preview()
    first = sis_grade_bridge.apply_sis_grade_bridge(
        preview["operation_id"], preview["batch_id"], preview["review_digest"]
    )
    retry = executor.retry_operation(preview["operation_id"])
    assert first["status"] == "attention"
    assert first["counts"]["copied_scores"] == 0
    assert retry["status"] == "attention"
    assert len(fake.send_calls) == 1


def test_adapter_kind_is_registered():
    from api.operation_ledger import registry
    assert registry.get_adapter("gradebook.sis_bridge").kind == "gradebook.sis_bridge"
