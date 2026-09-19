"""Examples and laws for student-free differentiated bridge reconciliation."""

import pytest

from api import sis_grade_bridge
from api.operation_ledger.adapters import differentiated_bridge, sis_grade_bridge as bridge_adapter
from api.platform_services import config


def _assignment(assignment_id, name, *, family="fam-1", tier=None, bridge=False, excluded=True):
    metadata = {"family_id": family}
    if tier:
        metadata["tier"] = tier
    if bridge:
        metadata["bridge"] = True
    return {
        "id": assignment_id, "name": name, "metadata": metadata,
        "due_at": "2026-10-01T15:00:00-05:00", "description": "desc",
        "omit_from_final_grade": excluded, "post_to_sis": bridge,
    }


def test_discovery_prefers_stable_family_and_tier_metadata_over_title():
    rows = [
        _assignment("a", "Renamed thing", tier="Support"),
        _assignment("b", "Renamed thing", tier="Core"),
        _assignment("bridge", "Renamed thing - Bridge", bridge=True, excluded=False),
    ]

    family = differentiated_bridge.discover_families(rows)[0]

    assert family["family_key"] == "fam-1"
    assert family["identity_source"] == "metadata"
    assert family["source_assignment_ids"] == ["a", "b"]
    assert family["bridge_assignment_ids"] == ["bridge"]
    assert family["grading_excluded"] is True


def test_reconciliation_matrix_is_student_free_and_marks_drift(monkeypatch):
    rows = [
        _assignment("a", "Checkpoint - Support", tier="Support"),
        _assignment("b", "Checkpoint - Core", tier="Core"),
        {**_assignment("bridge", "Checkpoint - Bridge", bridge=True, excluded=False),
         "description": "teacher changed", "due_at": "2026-10-01T23:59:00-05:00"},
    ]
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [{
        "family_key": "fam-1", "family_title": "Checkpoint",
        "source_assignment_ids": ["a", "b"], "source_titles": [],
        "bridge_assignment_id": "bridge", "bridge_state_digest": "a" * 64,
    }])
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")

    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=rows)

    assert result["ok"] is True
    row = result["matrix"][0]
    assert row["status"] == "drifted"
    assert row["drift_fields"] == ["description"]
    assert row["grading_excluded"] is True
    assert row["bridge_eligible"] is True
    assert "student" not in str(result).casefold()


def test_reconciliation_matrix_reports_missing_registration_without_private_rows(monkeypatch):
    rows = [
        _assignment("a", "Checkpoint - Support", tier="Support"),
        _assignment("b", "Checkpoint - Core", tier="Core"),
    ]
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])

    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=rows)

    assert result["matrix"][0]["status"] == "missing"
    assert result["matrix"][0]["bridge_assignment_id"] is None
    assert result["matrix"][0]["reasons"] == ["family_link_missing"]


def test_discovery_uses_casefolded_configured_non_color_tags_and_two_source_threshold(monkeypatch):
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Amber", "Core": "Teal", "Accelerate": "", "Extend": "",
    })
    rows = [
        {"id": "a", "name": "Base - amber"},
        {"id": "b", "name": "base - TEAL"},
    ]
    family = differentiated_bridge.discover_families(rows, tier_tags=config.get_tier_tags())[0]
    assert family["source_assignment_ids"] == ["a", "b"]
    assert family["source_count"] == 2

    one = differentiated_bridge.discover_families(rows[:1], tier_tags=config.get_tier_tags())[0]
    assert one["source_count"] == 1


def test_registered_orphan_is_blocked_attention_without_private_rows(monkeypatch):
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [{
        "family_key": "orphan-key", "family_title": "Orphan",
        "source_assignment_ids": ["a", "b"], "source_titles": ["Orphan - Amber", "Orphan - Teal"],
        "bridge_assignment_id": "bridge", "bridge_state_digest": "a" * 64,
    }])
    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=[])
    assert result["matrix"][0]["status"] == "blocked"
    assert result["matrix"][0]["reasons"] == ["family_linked_but_not_discoverable"]
    assert "student" not in str(result).casefold()


def test_unregistered_projection_returns_reconciliation_guidance(monkeypatch):
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1"}])
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    result = sis_grade_bridge.preview_sis_grade_bridge("course-1", "Existing Family")
    assert result["ok"] is False
    assert result["code"] == "family_link_required"
    assert "reconcile_sis_grade_bridges" in result["user_action"]
    assert "QuizForge" not in result["user_action"]


def test_exact_unsuffixed_bridge_is_adopted_without_title_rename(monkeypatch):
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Amber", "Core": "Teal", "Accelerate": "", "Extend": "",
    })
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    common = {
        "course_id": "course-1", "due_at": "2026-10-01T15:00:00-05:00",
        "points_possible": 10, "assignment_group_id": "g", "grading_type": "points",
    }
    source = lambda aid, name: {
        **common, "id": aid, "name": name, "published": True,
        "only_visible_to_overrides": True, "omit_from_final_grade": True,
        "post_to_sis": False, "submission_types": ["online_text_entry"],
    }
    rows = [source("a", "Base - Amber"), source("b", "Base - Teal"), {
        **common, "id": "bridge", "name": "Base", "description": differentiated_bridge.bridge_description(),
        "due_at": "2026-10-01T23:59:00-05:00", "published": True,
        "only_visible_to_overrides": False, "omit_from_final_grade": False,
        "post_to_sis": True, "submission_types": ["none"], "overrides": [],
    }]
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=rows)
    row = result["matrix"][0]
    assert row["bridge_assignment_id"] == "bridge"
    assert row["action"] == "link"
    assert row["status"] == "missing"


def test_global_module_membership_rejects_source_duplication_and_bridge_anywhere():
    state = {"modules": [
        {"id": "m1", "items": [
            {"id": "i-a", "content_id": "a"},
            {"id": "i-b", "content_id": "b"},
        ]},
        {"id": "m2", "items": [{"id": "i-a-copy", "content_id": "a"}]},
    ]}
    with pytest.raises(ValueError, match="source_module_membership_invalid"):
        bridge_adapter._validate_module_membership(state, "m1", ["a", "b"], "bridge")

    state["modules"][1]["items"] = [{"id": "i-bridge", "content_id": "bridge"}]
    with pytest.raises(ValueError, match="bridge_module_item_present"):
        bridge_adapter._validate_module_membership(state, "m1", ["a", "b"], "bridge")


def test_ambiguous_family_identity_and_one_source_fail_closed(monkeypatch):
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Amber", "Core": "Teal", "Accelerate": "", "Extend": "",
    })
    ambiguous = [
        _assignment("a1", "Base - Amber", family="family-a", tier="Support"),
        _assignment("a2", "Base - Teal", family="family-a", tier="Core"),
        _assignment("b1", "Base - Amber", family="family-b", tier="Support"),
        _assignment("b2", "Base - Teal", family="family-b", tier="Core"),
    ]
    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=ambiguous)
    assert result["matrix"]
    assert all(row["status"] == "blocked" for row in result["matrix"])
    assert all("ambiguous_family_identity" in row["reasons"] for row in result["matrix"])

    one = sis_grade_bridge.reconcile_sis_grade_bridges(
        "course-1", assignments=[_assignment("only", "Base - Amber")]
    )
    assert one["matrix"][0]["status"] == "incomplete"
    assert "two_source_threshold_not_met" in one["matrix"][0]["reasons"]


def test_live_coverage_overlap_is_incomplete_and_student_free(monkeypatch):
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Amber", "Core": "Teal", "Accelerate": "", "Extend": "",
    })
    rows = [
        {**_assignment("a", "Base - Amber"), "published": True, "grading_type": "points",
         "only_visible_to_overrides": True, "omit_from_final_grade": True, "post_to_sis": False,
         "points_possible": 10, "assignment_group_id": "g", "due_at": "2026-10-01T15:00:00-05:00"},
        {**_assignment("b", "Base - Teal"), "published": True, "grading_type": "points",
         "only_visible_to_overrides": True, "omit_from_final_grade": True, "post_to_sis": False,
         "points_possible": 10, "assignment_group_id": "g", "due_at": "2026-10-01T15:00:00-05:00"},
    ]
    monkeypatch.setattr(sis_grade_bridge, "_course_assignments", lambda _course: rows)
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    def canvas_rows(path, _params):
        if path.endswith("/users"):
            return ([{"id": "student-1"}, {"id": "student-2"}], None, True)
        aid = path.split("/assignments/")[1].split("/")[0]
        return ([{"id": "override", "student_ids": ["student-1"]}], None, True) if aid in {"a", "b"} else ([], None, True)
    monkeypatch.setattr(sis_grade_bridge.canvas_client, "canvas_get_all_complete", canvas_rows)
    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1")
    row = result["matrix"][0]
    assert row["status"] == "incomplete"
    assert "source_member_coverage_overlaps" in row["reasons"]
    assert "student-1" not in str(result)
