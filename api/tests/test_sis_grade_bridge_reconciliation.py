"""Examples and laws for student-free differentiated bridge reconciliation."""

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
    assert result["matrix"][0]["reasons"] == ["registration_missing"]
