"""Shared SIS grade-bridge use-case and synced-registration contracts."""

from __future__ import annotations

import copy

from api import sis_grade_bridge
from api.operation_ledger import models, operations, paths
from api.platform_services import config
from api.platform_services.config import sis_grade_bridge as bridge_config


def test_synced_registration_is_student_free_and_replaces_exact_family(monkeypatch):
    state = {}
    monkeypatch.setattr(bridge_config._io_mod, "_synced_state", lambda: copy.deepcopy(state))

    def modify(mutator):
        updated = mutator(state)
        return copy.deepcopy(updated if updated is not None else state)

    monkeypatch.setattr(bridge_config._io_mod, "_modify_synced", modify)
    registration = {
        "family_title": "Invented Checkpoint",
        "source_assignment_ids": ["source-1", "source-2"],
        "source_titles": ["Invented Checkpoint - A", "Invented Checkpoint - B"],
        "bridge_assignment_id": "bridge-1",
        "bridge_state_digest": "a" * 64,
        "student_ids": ["must-not-persist"],
        "grades": [100],
    }

    saved = bridge_config.save_sis_grade_bridge("course-1", registration)

    assert set(saved) == {
        "family_title", "source_assignment_ids", "source_titles",
        "bridge_assignment_id", "bridge_state_digest",
    }
    assert bridge_config.get_sis_grade_bridge(
        "course-1", "Invented Checkpoint"
    ) == saved
    assert bridge_config.list_sis_grade_bridges("course-1") == [saved]
    assert "student_ids" not in str(state)
    assert "grades" not in str(state)


def test_apply_rejects_non_bridge_operation_before_executor(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    operation = models.new_operation(
        operation_id="op-other",
        kind="content.page",
        source_ref=None,
        source_digest="digest",
        normalized_payload={},
        targets=[models.new_target(
            target_key="target", idempotency_key="idem", course_id="course-1"
        )],
    )
    operations.create_operation(operation)

    result = sis_grade_bridge.apply_sis_grade_bridge(
        "op-other", "batch-other", "digest-other"
    )

    assert result == {"ok": False, "error": "bridge operation was not found"}


def test_apply_coordinates_are_exact_and_digest_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    operation = models.new_operation(
        operation_id="op-bridge",
        kind="gradebook.sis_bridge",
        source_ref=None,
        source_digest="digest",
        normalized_payload={},
        targets=[models.new_target(
            target_key="target", idempotency_key="idem", course_id="course-1"
        )],
    )
    operations.create_operation(operation)

    result = sis_grade_bridge.apply_sis_grade_bridge(
        "op-bridge", "wrong-batch", "wrong-digest"
    )

    assert result["ok"] is False
    assert "review batch or digest" in result["error"]
