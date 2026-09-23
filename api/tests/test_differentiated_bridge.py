"""Law tests for api/operation_ledger/adapters/differentiated_bridge.py.

Correction 3: after CE's own verified create, a teacher's Canvas Live edits
to an unrestricted source's title, due date, and overrides are
authoritative. Recovery re-checks only the CE-owned invariants -- exact id,
published, grading_type, omit_from_final_grade, post_to_sis, and
points/assignment-group consistency across sources -- and never re-fetches
overrides for an unrestricted tier.
"""
import pytest

from api.operation_ledger.adapters import differentiated_bridge
from api.platform_services import canvas_client


def _base_row(**overrides):
    row = {
        "id": "101", "name": "Practice - Red", "published": True,
        "only_visible_to_overrides": False, "omit_from_final_grade": True,
        "post_to_sis": False, "grading_type": "points",
        "points_possible": 10.0, "assignment_group_id": "77",
    }
    row.update(overrides)
    return row


def _payload(**overrides):
    payload = {
        "unrestricted_tiers": True,
        "base_title": "Practice",
        "bridge_description": "<p>desc</p>",
        "bridge_due_at": None,
    }
    payload.update(overrides)
    return payload


def _install_rows(monkeypatch, rows):
    monkeypatch.setattr(
        canvas_client, "canvas_get",
        lambda path, params=None, timeout=20: (rows.get(path.rsplit("/", 1)[-1]), None),
    )


def test_unrestricted_sources_tolerate_a_rename_cleared_due_date_and_overrides(monkeypatch):
    """Law: a teacher's rename, cleared due date, and overrides never block,
    and overrides are never even fetched for an unrestricted tier."""
    rows = {
        "101": _base_row(id="101", name="ECR Prep 3: Red", due_at=None),
        "102": _base_row(id="102", name="ECR Prep 3: Blue", due_at=None),
    }
    _install_rows(monkeypatch, rows)

    def _boom(*_args, **_kwargs):
        raise AssertionError("overrides must not be fetched for an unrestricted tier")
    monkeypatch.setattr(canvas_client, "canvas_get_all", _boom)

    family, source_rows, error = differentiated_bridge._verified_family_sources(
        "42", _payload(due_at="2026-09-14T15:30:00-05:00"),
        ["101", "102"], ["Practice - Red", "Practice - Blue"],
    )

    assert error is None
    assert family["base_title"] == "Practice"
    assert [row["name"] for row in source_rows] == ["ECR Prep 3: Red", "ECR Prep 3: Blue"]


@pytest.mark.parametrize("unsafe_field", [
    pytest.param({"omit_from_final_grade": False}, id="counts-toward-final-grade"),
    pytest.param({"post_to_sis": True}, id="sis-sync-on"),
])
def test_ce_owned_invariants_still_fail_after_a_teacher_edit(monkeypatch, unsafe_field):
    """Law: a CE-owned invariant (never a teacher's business) still blocks,
    even though title/due_at/overrides no longer do."""
    rows = {
        "101": _base_row(id="101", **unsafe_field),
        "102": _base_row(id="102", name="Practice - Blue"),
    }
    _install_rows(monkeypatch, rows)
    monkeypatch.setattr(
        canvas_client, "canvas_get_all", lambda *_args, **_kwargs: ([], None))

    _family, _rows, error = differentiated_bridge._verified_family_sources(
        "42", _payload(), ["101", "102"], ["Practice - Red", "Practice - Blue"],
    )

    assert error == "source_final_shape_unverified"
