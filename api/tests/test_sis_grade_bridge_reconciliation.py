"""Student-free, mirror-backed differentiated bridge reconciliation laws."""

import copy

from api import sis_grade_bridge
from api import course_catalog
from api.operation_ledger import operations, paths
from api.operation_ledger.adapters import differentiated_bridge
from api.platform_services import canvas_client, config


def _assignment(assignment_id, name, *, family="fam-1", tier=None, bridge=False, excluded=True):
    metadata = {}
    if family is not None:
        metadata["family_id"] = family
    if tier:
        metadata["tier"] = tier
    if bridge:
        metadata["bridge"] = True
    return {
        "id": assignment_id,
        "name": name,
        "metadata": metadata,
        "due_at": "2026-10-01T15:00:00-05:00",
        "description": "desc",
        "points_possible": 10,
        "assignment_group_id": "g",
        "omit_from_final_grade": excluded,
        "post_to_sis": bridge,
    }


def _messy_rows(*, points=(10, 10, 10), groups=("g", "g", "g")):
    titles = [
        "Outsiders - Chapter 7 SCRS - Blue",
        "The Outsiders - Chapter 7 SCRs Red",
        "Outsiders - Ch 7 SCRs - Silver",
    ]
    return [
        {
            **_assignment(
                assignment_id,
                title,
                family=None,
            ),
            "points_possible": points[index],
            "assignment_group_id": groups[index],
        }
        for index, (assignment_id, title) in enumerate(
            zip(("source-blue", "source-red", "source-silver"), titles)
        )
    ]


def _wire_proposed_snapshot(monkeypatch, tmp_path, rows, registrations=None):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(
        config,
        "active_courses",
        lambda: [{"id": "course-1", "name": "Synthetic Course", "active": True}],
    )
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)
    monkeypatch.setattr(config, "get_canvas_base", lambda: "https://canvas.invalid")
    monkeypatch.setattr(
        config,
        "list_sis_grade_bridges",
        lambda _course: copy.deepcopy(registrations or []),
    )
    monkeypatch.setattr(
        course_catalog,
        "read_catalog",
        lambda _course: {
            "catalog": {
                "updated_at": "2026-09-21T12:00:00Z",
                "assignments": {
                    "state": "current",
                    "records": {
                        str(row["id"]): copy.deepcopy(row) for row in rows
                    },
                },
                "modules": {"state": "current", "records": []},
            }
        },
    )


def _preview_proposed(monkeypatch, tmp_path, rows, **kwargs):
    _wire_proposed_snapshot(monkeypatch, tmp_path, rows)
    return sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1",
        "Chapter 7 reading",
        source_assignment_ids=[row["id"] for row in rows],
        **kwargs,
    )


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


def test_reconciliation_reports_missing_bridge_without_student_or_due_reads(monkeypatch):
    rows = [
        _assignment("a", "Checkpoint - Support", tier="Support"),
        _assignment("b", "Checkpoint - Core", tier="Core"),
    ]
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Amber", "Core": "Teal", "Accelerate": "", "Extend": "",
    })

    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=rows)

    row = result["matrix"][0]
    assert row["status"] == "missing"
    assert row["reasons"] == ["bridge_missing", "family_link_missing"]
    assert row["coverage"]["source"] == "mirror"
    assert "student" not in str(result).casefold()


def test_reconciliation_does_not_treat_mixed_due_dates_as_a_failure(monkeypatch):
    rows = [
        {**_assignment("a", "Base - Amber", tier="Support"), "due_at": "2026-10-01T15:00:00-05:00"},
        {**_assignment("b", "Base - Teal", tier="Core"), "due_at": "2026-12-01T15:00:00-05:00"},
    ]
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Amber", "Core": "Teal", "Accelerate": "", "Extend": "",
    })
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])

    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=rows)

    assert result["ok"] is True
    assert "mixed_effective_due_dates" not in result["matrix"][0]["reasons"]


def test_registered_orphan_is_blocked_without_private_rows(monkeypatch):
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [{
        "family_key": "orphan-key", "family_title": "Orphan",
        "source_assignment_ids": ["a", "b"],
        "source_titles": ["Orphan - Amber", "Orphan - Teal"],
        "bridge_assignment_id": "bridge", "bridge_state_digest": "a" * 64,
    }])

    result = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=[])

    assert result["matrix"][0]["status"] == "blocked"
    assert result["matrix"][0]["reasons"] == ["family_linked_but_not_discoverable"]
    assert "student" not in str(result).casefold()


def test_discovery_keeps_two_source_threshold_and_ambiguity_rules(monkeypatch):
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


def test_agent_proposed_grouping_recovers_the_real_messy_title_case(monkeypatch, tmp_path):
    rows = _messy_rows()
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Blue", "Core": "Red", "Accelerate": "Silver", "Extend": "",
    })

    matrix = sis_grade_bridge.reconcile_sis_grade_bridges("course-1", assignments=rows)

    assert len(matrix["matrix"]) == 3
    assert all(row["status"] == "incomplete" for row in matrix["matrix"])
    result = _preview_proposed(monkeypatch, tmp_path, rows)

    assert result["ok"] is True
    assert result["preview"]["source_titles"] == [row["name"] for row in rows]


def test_agent_proposed_grouping_never_calls_canvas_live(monkeypatch, tmp_path):
    rows = _messy_rows()
    _wire_proposed_snapshot(monkeypatch, tmp_path, rows)

    def fail_live(*_args, **_kwargs):
        raise AssertionError("proposed grouping reached CanvasLive")

    for name in ("canvas_get", "canvas_get_all", "canvas_get_all_complete", "_canvas_send"):
        if hasattr(canvas_client, name):
            monkeypatch.setattr(canvas_client, name, fail_live)

    result = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "Chapter 7 reading",
        source_assignment_ids=[row["id"] for row in rows],
    )

    assert result["ok"] is True


def test_agent_proposed_grouping_uses_mirror_titles_not_caller_assignments(monkeypatch, tmp_path):
    rows = _messy_rows()
    result = _preview_proposed(
        monkeypatch,
        tmp_path,
        rows,
        assignments=[{**row, "name": "Caller supplied title"} for row in rows],
    )

    assert result["ok"] is True
    assert result["preview"]["source_titles"] == [row["name"] for row in rows]


def test_agent_proposed_grouping_refuses_mixed_points_without_persisting(monkeypatch, tmp_path):
    rows = _messy_rows(points=(10, 20, 10))
    result = _preview_proposed(monkeypatch, tmp_path, rows)

    assert result["ok"] is False
    assert result["code"] == "mixed_points_possible"
    assert result["points_possible"] == [10, 20]
    assert operations.list_operations() == []


def test_agent_proposed_grouping_refuses_mixed_assignment_groups(monkeypatch, tmp_path):
    rows = _messy_rows(groups=("g1", "g2", "g1"))
    result = _preview_proposed(monkeypatch, tmp_path, rows)

    assert result["ok"] is False
    assert result["code"] == "mixed_assignment_groups"
    assert result["assignment_group_ids"] == ["g1", "g2"]


def test_agent_proposed_grouping_refuses_title_and_source_link_collisions(monkeypatch, tmp_path):
    rows = _messy_rows()
    _wire_proposed_snapshot(
        monkeypatch,
        tmp_path,
        rows,
        registrations=[{
            "family_title": "Existing Family",
            "source_assignment_ids": ["other-source"],
            "bridge_assignment_id": "other-bridge",
        }],
    )
    monkeypatch.setattr(
        config,
        "list_sis_grade_bridges",
        lambda _course: [{
            "family_title": "Chapter 7 reading",
            "source_assignment_ids": ["other-source"],
            "bridge_assignment_id": "other-bridge",
        }],
    )
    title_collision = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "CHAPTER 7 READING",
        source_assignment_ids=[row["id"] for row in rows],
    )
    assert title_collision["code"] == "family_already_linked"
    assert title_collision["conflicting_family_title"] == "Chapter 7 reading"

    monkeypatch.setattr(
        config,
        "list_sis_grade_bridges",
        lambda _course: [{
            "family_title": "Existing Family",
            "source_assignment_ids": ["source-red"],
            "bridge_assignment_id": "other-bridge",
        }],
    )
    source_collision = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "Chapter 7 reading",
        source_assignment_ids=[row["id"] for row in rows],
    )
    assert source_collision["code"] == "family_already_linked"
    assert source_collision["conflicting_family_title"] == "Existing Family"


def test_agent_proposed_grouping_deduplicates_ids_before_threshold_check(monkeypatch):
    monkeypatch.setattr(config, "active_courses", lambda: [{"id": "course-1"}])

    result = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "Chapter 7 reading", source_assignment_ids=["source-blue", "source-blue"]
    )

    assert result == {
        "ok": False,
        "code": "two_source_threshold_not_met",
        "error": "A proposed family must contain at least two distinct source assignments.",
    }


def test_agent_proposed_grouping_refuses_missing_source_ids(monkeypatch, tmp_path):
    rows = _messy_rows()
    _wire_proposed_snapshot(monkeypatch, tmp_path, rows)

    result = sis_grade_bridge.preview_sis_grade_bridge_reconciliation(
        "course-1", "Chapter 7 reading",
        source_assignment_ids=["source-blue", "missing-source", "source-silver"],
    )

    assert result["ok"] is False
    assert result["code"] == "source_exact_id_unverified"
    assert result["missing_assignment_ids"] == ["missing-source"]


def test_agent_proposed_grouping_preserves_family_link_identity(monkeypatch):
    rows = _messy_rows()
    registrations = []
    monkeypatch.setattr(config, "get_tier_tags", lambda: {
        "Support": "Blue", "Core": "Red", "Accelerate": "Silver", "Extend": "",
    })
    monkeypatch.setattr(config, "list_sis_grade_bridges", lambda _course: registrations)

    registration = {
        "family_title": "Chapter 7 reading",
        "source_assignment_ids": [row["id"] for row in rows],
        "source_titles": [row["name"] for row in rows],
        "bridge_assignment_id": "bridge-1",
        "bridge_state_digest": "a" * 64,
    }
    registrations.append(registration)

    families = differentiated_bridge.discover_families(rows, registrations)

    assert len(families) == 1
    assert families[0]["identity_source"] == "family_link"
    assert families[0]["source_assignment_ids"] == [row["id"] for row in rows]
