"""Student-free, mirror-backed differentiated bridge reconciliation laws."""

from api import sis_grade_bridge
from api.operation_ledger.adapters import differentiated_bridge
from api.platform_services import config


def _assignment(assignment_id, name, *, family="fam-1", tier=None, bridge=False, excluded=True):
    metadata = {"family_id": family}
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
