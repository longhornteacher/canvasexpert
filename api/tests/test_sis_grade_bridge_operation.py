"""High-risk laws for registered differentiated-family grade projection."""

import copy
import json

import pytest

from api import sis_grade_bridge
from api.operation_ledger import executor, operations, paths, receipts
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
        self.send_calls = []
        self.uncertain_grade = False

    def get(self, path, params=None, timeout=20):
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
        return [], None, True

    def send(self, method, path, request, timeout=30):
        self.send_calls.append((method, path, copy.deepcopy(request)))
        if self.uncertain_grade:
            return None, "connection timeout"
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
    assert "not registered" in result["error"]
    assert calls == []


@pytest.mark.parametrize(
    ("mutation", "error", "fields"),
    [
        (lambda fake: fake.assignments["source-a"].update(post_to_sis=True), "source_sis_sync_enabled", None),
        (lambda fake: fake.assignments["source-a"].update(omit_from_final_grade=False), "source_counts_toward_final_grade", None),
        (lambda fake: fake.assignments["bridge"].update(published=False), "registered_bridge_shape_drift", ["published"]),
        (lambda fake: fake.assignments["bridge"].update(description="changed"), "registered_bridge_shape_drift", ["description"]),
        (lambda fake: fake.assignments["bridge"].update(due_at="2026-10-02T23:59:00-05:00"), "registered_bridge_shape_drift", ["due_at"]),
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
        assert "bridge" not in wire.casefold() or "registered_bridge_shape_drift" in wire
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
