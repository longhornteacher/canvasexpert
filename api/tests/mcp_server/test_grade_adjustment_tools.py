"""MCP boundary contracts for reviewed existing-grade adjustments."""
from __future__ import annotations

import json

from api.mcp_server import contract, server, tools


def test_grade_adjustment_tools_are_registered_with_the_versioned_shape():
    schema = contract.load_contract()
    by_name = {item["name"]: item for item in schema["tools"]}

    assert {"preview_grade_adjustment", "apply_grade_adjustment"} <= set(by_name)
    assert by_name["preview_grade_adjustment"]["required"] == [
        "course_id", "assignment_id", "adjustment",
    ]
    assert by_name["apply_grade_adjustment"]["required"] == [
        "operation_id", "batch_id", "review_digest",
    ]
    live_names = set(server.mcp._tool_manager._tools)
    assert {"preview_grade_adjustment", "apply_grade_adjustment"} <= live_names


def test_preview_wrapper_keeps_pseudonyms_and_adds_bounded_next_step(monkeypatch):
    payload = {
        "ok": True,
        "operation_id": "op-synthetic",
        "batch_id": "batch-synthetic",
        "review_digest": "digest-synthetic",
        "preview": {"changed": [{"pseudonym": "Pikachu", "before": 7, "after": 9}]},
    }
    monkeypatch.setattr(tools.grade_adjustment,
                        "preview_grade_adjustment",
                        lambda course_id, assignment_id, adjustment: payload)

    result = tools.preview_grade_adjustment(
        "course-1", "assignment-1", {"kind": "rule", "model": "flat_bump"})

    assert result["ok"] is True
    assert result["preview"]["changed"][0]["pseudonym"] == "Pikachu"
    assert "apply_grade_adjustment" in result["next"]
    assert "student-1" not in json.dumps(result)


def test_server_apply_wrapper_is_thin_and_serializes_compactly(monkeypatch):
    monkeypatch.setattr(
        tools.grade_adjustment, "apply_grade_adjustment",
        lambda operation_id, batch_id, review_digest: {
            "ok": True, "operation_id": operation_id, "status": "applied",
            "counts": {"adjusted": 1, "skipped_changed": 0,
                       "failed": 0, "unverified": 0},
        },
    )
    monkeypatch.setattr(tools, "final_response_gate", lambda payload: payload)

    wire = server.apply_grade_adjustment("op-synthetic", "batch-synthetic", "digest-synthetic")
    result = json.loads(wire)

    assert result["ok"] is True
    assert result["status"] == "applied"
    assert " " not in wire
