"""MCP wrappers and last-mile privacy for SIS grade-bridge tools."""

from __future__ import annotations

import json

from api.mcp_server import server, tools
from api.operation_ledger import models, paths
from api.platform_services import config


def test_plain_tools_delegate_to_shared_use_case(monkeypatch):
    monkeypatch.setattr(
        tools.sis_grade_bridge, "list_sis_grade_bridges",
        lambda course_id: {"ok": True, "course_id": course_id, "bridges": []},
    )
    monkeypatch.setattr(
        tools.sis_grade_bridge, "preview_sis_grade_bridge",
        lambda course_id, title: {"ok": True, "course_id": course_id, "title": title},
    )
    monkeypatch.setattr(
        tools.sis_grade_bridge, "apply_sis_grade_bridge",
        lambda operation_id, batch_id, digest: {
            "ok": True, "coordinates": [operation_id, batch_id, digest]
        },
    )
    monkeypatch.setattr(
        tools.sis_grade_bridge, "confirm_sis_grade_bridge_passback",
        lambda operation_id, observed_at: {
            "ok": True, "evidence": [operation_id, observed_at]
        },
    )

    assert tools.list_sis_grade_bridges("course-x")["course_id"] == "course-x"
    preview = tools.preview_sis_grade_bridge("course-x", "Invented")
    assert preview["title"] == "Invented"
    assert preview["next"] == tools._NEXT_STEPS["preview_sis_grade_bridge"]
    assert tools.apply_sis_grade_bridge("op", "batch", "digest")["coordinates"] == [
        "op", "batch", "digest"
    ]
    assert tools.confirm_sis_grade_bridge_passback(
        "op", "2030-01-02T03:04:05-06:00"
    )["evidence"] == ["op", "2030-01-02T03:04:05-06:00"]


def test_mcp_preview_output_never_projects_private_student_rows(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(paths, "private_root", lambda: tmp_path / "private")
    monkeypatch.setattr(config, "active_courses", lambda: [
        {"id": "course-x", "name": "Invented Course", "active": True}
    ])
    monkeypatch.setattr(config, "get_sis_grade_bridge", lambda *_args: None)

    class FakeAdapter:
        kind = "gradebook.sis_bridge"

        def build_payload(self, request):
            return dict(request)

        def verify_targets(self, payload, targets):
            return [{
                "course_id": targets[0]["course_id"],
                "target_key": "target-key",
                "idempotency_key": "idempotency-key",
            }]

        def capture_baseline(self, payload, target):
            return {
                "source_assignment_ids": ["source-a", "source-b"],
                "source_titles": ["Invented - A", "Invented - B"],
                "active_student_ids": ["private-student-id"],
                "grade_entries": [{
                    "user_id": "private-student-id", "score": 91,
                }],
                "counts": {"eligible_final": 1},
                "warnings": [],
            }

        def freeze_payload(self, payload, baseline):
            return {**payload, "source_assignment_ids": baseline["source_assignment_ids"]}

        def source_digest(self, payload):
            return models.sha256_dict(payload)

        def initial_steps(self, payload, baseline):
            return [models.new_step("copy_grade:0")]

        def freeze_review(self, payload, target, baseline):
            return {
                "course_id": target["course_id"],
                "source_count": 2,
                "counts": dict(baseline["counts"]),
                "warnings": [],
            }

    monkeypatch.setattr(
        tools.sis_grade_bridge.registry, "get_adapter", lambda _kind: FakeAdapter()
    )

    result = tools.preview_sis_grade_bridge("course-x", "Invented")
    serialized = server._compact(result)

    assert json.loads(serialized)["ok"] is True
    assert result["next"] == tools._NEXT_STEPS["preview_sis_grade_bridge"]
    assert "private-student-id" not in serialized
    assert '"score":91' not in serialized


def test_server_instructions_lock_one_command_preauthorization():
    instructions = server._SERVER_INSTRUCTIONS

    assert "Asking for a write is the authorization" in instructions
    assert "only its named target and course" in instructions


def test_passback_evidence_rule_rides_with_its_own_tool():
    """The evidence rule lives on the tool, not in the always-loaded block: it
    only matters once a passback is actually ambiguous, and the block is paid
    for on every request."""
    tool = server.mcp._tool_manager._tools["confirm_sis_grade_bridge_passback"]
    # The docstring is hard-wrapped, so compare on collapsed whitespace.
    description = " ".join((tool.description or "").split())

    assert "Canvas Grade Sync row" in description
    assert "at or after the persisted" in description
    assert "Do not infer" in description
    assert "do not resend passback while confirming" in description
    assert "Grade Sync row" not in server._SERVER_INSTRUCTIONS
