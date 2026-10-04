"""Laws at merged MCP dispatch and names-only telemetry boundaries."""
from __future__ import annotations

import asyncio
import json

import pytest

from api import operational_log
from api.mcp_server import server, tools


@pytest.mark.parametrize("kind,owner,method", [
    ("content.page", tools.content_push, "apply_content_push"),
    (tools.content_push.ASSIGNMENT_UPDATE_KIND, tools.content_push, "apply_assignment_update"),
    (tools.grade_adjustment.KIND, tools.grade_adjustment, "apply_grade_adjustment"),
    (tools.attempts_grant.KIND, tools.attempts_grant, "apply_attempts_grant"),
    (tools.sis_grade_bridge.KIND, tools.sis_grade_bridge, "apply_sis_grade_bridge"),
])
@pytest.mark.parametrize("ok", [True, False])
def test_operation_dispatch_preserves_owner_result_and_exact_coordinates(monkeypatch, kind, owner, method, ok):
    result = {"ok": ok, "code": "synthetic_owner_code", "receipt": {"opaque": True}}
    calls = []
    monkeypatch.setattr(tools.operation_operations, "get_operation", lambda key: {"kind": kind})
    monkeypatch.setattr(owner, method, lambda *args: calls.append(args) or result)
    assert tools.apply_operation("op", "batch", "digest") == result
    assert calls == [("op", "batch", "digest")]


@pytest.mark.parametrize("operation,code", [(None, "operation_not_found"), ({"kind": "unsupported"}, "unsupported_operation_kind")])
def test_unknown_operation_fails_before_owner_dispatch(monkeypatch, operation, code):
    monkeypatch.setattr(tools.operation_operations, "get_operation", lambda key: operation)
    monkeypatch.setattr(tools.content_push, "apply_content_push", lambda *args: pytest.fail("write owner reached"))
    assert tools.apply_operation("op", "batch", "digest")["code"] == code


@pytest.mark.parametrize("call", [
    lambda: tools.get_course_content("c", "pages", full_descriptions=False),
    lambda: tools.get_course_content("c", "assignments", include_items=False),
    lambda: tools.get_course_content("c", "modules", include_unpublished=True),
    lambda: tools.preview_content_push("c", "assignment", variants=[]),
    lambda: tools.preview_content_push("c", "quiz", "one", variants=[]),
    lambda: tools.preview_sis_grade_bridge("c", "family", source_assignment_ids=[]),
    lambda: tools.refresh_mirror("c", include_comments=True, structure_only=True),
    lambda: tools.get_submissions("c", "a", offset=0),
    lambda: tools.transfer_work_item("w", "hand_off", confirm_stale=True),
    lambda: tools.set_score_curve_rule("c", rule_id="r"),
    lambda: tools.set_score_curve_rule("c", formula={}, rule_id="r", active=False),
    lambda: tools.set_score_curve_rule("c", active=False),
    lambda: tools.prepare_scoring_session("c", "a", mode="feedback_revision", late_policy="apply"),
    lambda: tools.get_scoring_packet("feedback-unknown", include_context=False),
    lambda: tools.stage_scoring_results("feedback-unknown", [], "d", grade_mode="post_score"),
    lambda: tools.stage_scoring_results("score-unknown", [], "d", attachment_file="file.pdf"),
    lambda: tools.apply_staged_scoring_results("feedback-unknown", "d", idempotency_key="key"),
])
def test_inapplicable_options_are_refused_before_side_effects(call):
    assert call()["code"] == "inapplicable_option"


@pytest.mark.parametrize("include", [["sections", "groups"], ["groups", "sections"], ["groups", "sections", "groups"]])
def test_roster_include_is_order_independent_and_keeps_safe_projection_metadata(monkeypatch, include):
    sections = {"ok": True, "sections": {"columns": [], "rows": []}, "freshness": {"section": "roster"}}
    groups = {"ok": True, "group_sets": [{"name": "Public Set", "groups": []}], "freshness": {"section": "groups"}}
    monkeypatch.setattr(tools, "_roster_sections", lambda course: sections)
    monkeypatch.setattr(tools, "_roster_groups", lambda course: groups)
    result = tools.get_roster("c", include=include)
    assert result["sections"] == sections["sections"]
    assert result["group_sets"] == groups["group_sets"]
    assert result["freshness"] == {"sections": sections["freshness"], "groups": groups["freshness"]}


def test_revision_dispatch_never_tries_the_scoring_store(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "_load_scoring_assignment_session", lambda sid: pytest.fail("scoring store reached"))
    monkeypatch.setattr(tools, "_feedback_revision_call", lambda operation, *args, **kwargs: calls.append(operation) or {"ok": False, "code": "work_item_not_found"})
    sid = "feedback-unknown"
    assert tools.get_scoring_packet(sid)["ok"] is False
    assert tools.stage_scoring_results(sid, [], "d")["ok"] is False
    assert tools.get_scoring_preview(sid)["ok"] is False
    assert tools.apply_staged_scoring_results(sid, "d")["ok"] is False
    assert calls == ["packet", "stage", "preview", "apply"]
    assert tools.refresh_scoring_session(sid)["code"] == "revision_session_unsupported"
    assert tools.reset_scoring_review(sid)["code"] == "revision_session_unsupported"


@pytest.mark.parametrize("outcome", ["ok", "refused", "error"])
def test_registered_call_logs_only_names_timing_and_allowed_outcome(monkeypatch, outcome):
    sensitive = "Synthetic Student 999000 submission secret text"
    def delegate(*args, **kwargs):
        if outcome == "error":
            raise RuntimeError(sensitive)
        return {"ok": outcome == "ok", "code": sensitive, "result": sensitive}
    monkeypatch.setattr(tools, "get_course_content", delegate)
    monkeypatch.setattr(tools, "final_response_gate", lambda payload: payload)
    before = len(operational_log.tail())
    if outcome == "error":
        with pytest.raises(Exception):
            asyncio.run(server.mcp.call_tool("get_course_content", {"course_id": sensitive, "kind": "assignments"}))
    else:
        asyncio.run(server.mcp.call_tool("get_course_content", {"course_id": sensitive, "kind": "assignments"}))
    records = operational_log.tail()[before:]
    assert len(records) == 1
    record = records[0]
    expected = {"timestamp", "app_version", "event", "outcome", "duration_ms"}
    if outcome == "error":
        expected.add("error_class")
        assert record["error_class"] == "RuntimeError"
    assert set(record) == expected
    assert record["event"] == "mcp.tool.get_course_content"
    assert record["outcome"] == outcome
    assert sensitive not in json.dumps(record)


def test_protocol_preserves_inapplicable_result_row_fields_for_owner_validation(monkeypatch):
    received = []
    monkeypatch.setattr(tools, "stage_scoring_results", lambda **kwargs: received.append(kwargs["results"]) or {"ok": False, "code": "row_refused"})
    monkeypatch.setattr(tools, "final_response_gate", lambda payload: payload)
    row = {"pseudonym": "Pikachu", "comment_key": "key", "feedback": "Exact text", "score": 10}
    asyncio.run(server.mcp.call_tool("stage_scoring_results", {"scoring_session_id": "feedback-unknown", "results": [row], "expected_packet_digest": "digest"}))
    assert received == [[row]]


def test_registered_argument_validation_failure_logs_once_without_arguments():
    before = len(operational_log.tail())
    with pytest.raises(Exception):
        asyncio.run(server.mcp.call_tool("get_course_content", {"course_id": "Synthetic Student 999000"}))
    records = operational_log.tail()[before:]
    assert len(records) == 1
    assert records[0]["event"] == "mcp.tool.get_course_content"
    assert records[0]["outcome"] == "error"
    assert records[0]["error_class"] == "ValidationError"
    assert "Synthetic Student" not in json.dumps(records)
