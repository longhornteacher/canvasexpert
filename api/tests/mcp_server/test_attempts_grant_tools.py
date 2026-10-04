"""MCP boundary contracts for the reviewed attempts-grant pair."""
from __future__ import annotations

import json

from api.mcp_server import contract, server, tools


def test_attempts_grant_tools_are_registered_with_the_versioned_shape():
    by_name = {item["name"]: item for item in contract.load_contract()["tools"]}

    assert by_name["preview_attempts_grant"]["required"] == [
        "course_id", "assignment_id", "grant",
    ]
    assert by_name["preview_attempts_grant"]["properties"]["grant"] == "object"
    assert by_name["apply_operation"]["required"] == [
        "operation_id", "batch_id", "review_digest",
    ]
    assert {"preview_attempts_grant", "apply_operation"} <= set(
        server.mcp._tool_manager._tools)


def test_preview_wrapper_keeps_pseudonyms_and_says_apply_only_on_direct_instruction(
        monkeypatch):
    payload = {
        "ok": True, "operation_id": "op-synthetic", "batch_id": "batch-synthetic",
        "review_digest": "digest-synthetic",
        "preview": {"students": [{"pseudonym": "Pikachu", "before_extra": 0,
                                  "after_extra": 2}]},
    }
    monkeypatch.setattr(tools.attempts_grant, "preview_attempts_grant",
                        lambda course_id, assignment_id, grant: payload)

    result = tools.preview_attempts_grant("course-1", "assignment-1",
                                          {"students": ["Pikachu"], "extra_attempts": 2})

    assert result["preview"]["students"][0]["pseudonym"] == "Pikachu"
    assert "apply_operation" in result["next"]
    assert "direct instruction" in result["next"]
    refusal = {"ok": False, "code": "invalid_grant", "error": "synthetic."}
    monkeypatch.setattr(tools.attempts_grant, "preview_attempts_grant",
                        lambda *args: refusal)
    assert tools.preview_attempts_grant("course-1", "assignment-1", {}) == refusal


def test_server_apply_wrapper_is_thin_and_serializes_compactly(monkeypatch):
    monkeypatch.setattr(
        tools.attempts_grant, "apply_attempts_grant",
        lambda operation_id, batch_id, review_digest: {
            "ok": True, "operation_id": operation_id, "status": "applied",
            "counts": {"granted": 1, "skipped": 0, "failed": 0},
        },
    )
    monkeypatch.setattr(tools, "final_response_gate", lambda payload: payload)

    monkeypatch.setattr(tools.operation_operations, "get_operation", lambda _id: {"kind": "gradebook.attempts_grant"})
    wire = server.apply_operation('op-synthetic', 'batch-synthetic', 'digest-synthetic')

    assert json.loads(wire)["status"] == "applied" and " " not in wire


def test_an_interrupted_per_student_grant_resumed_returns_the_pseudonym_rows(attempts_world):
    """Example: resume_operation projects this kind like apply does, pseudonyms only."""
    window = {"due_at": "2099-01-10T05:00:00Z", "lock_at": "2099-01-11T05:00:00Z"}
    world = attempts_world("regular")
    preview = world.preview({"students": ["Pikachu", "Eevee"], "extra_attempts": 2,
                             "reopen": window})
    world.canvas.fault("POST", "/extensions", "timed out", lands=True)
    assert world.apply(preview)["status"] == "attention"

    resumed = tools.resume_operation(preview["operation_id"])

    assert resumed["ok"] is True and resumed["status"] == "applied"
    assert resumed["counts"] == {"granted": 3, "skipped": 0, "failed": 0}
    rows = {row.get("pseudonym", row["item"]): row for row in resumed["outcomes"]}
    assert rows["Eevee"]["before_extra"] == 0 and rows["Eevee"]["after_extra"] == 2
    assert rows["reopen"]["outcome"] == "granted"
    wire = json.dumps(resumed)
    forbidden = (list(world.vault.by_id) + ["Real Name"]
                 + [str(row["id"]) for row in world.canvas.overrides])
    assert all(token not in wire for token in forbidden)
