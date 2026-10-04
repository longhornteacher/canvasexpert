"""Guards on the always-loaded MCP surface: the instruction block and the
weight of the tool listing.

The listing and instruction block are delivered to connected clients, so their
serialized wire size is measured. Client and model token treatment varies. The
instruction block has been observed truncating mid-sentence in a real client,
so its ordering matters: routing and write rules come before discovery hints,
because the tail is what gets cut.
"""
import asyncio
import json

from api import feedback_vault
from api.mcp_server import server, tools


# v79 adds the two quiz_settings inputs.
INSTRUCTION_BUDGET = 2303
LISTING_BUDGET = 14868
DESCRIPTION_BUDGET = 343

RESULT_NEXT_TOOLS = {
    "get_scoring_packet",
    "get_scoring_preview",
    "prepare_scoring_session",
    "refresh_scoring_session",
    "preview_sis_grade_bridge",
    "preview_roster_student_change",
    "preview_content_push",
    "preview_assignment_update",
}


def test_instruction_block_stays_within_budget():
    length = len(server._SERVER_INSTRUCTIONS)

    assert length <= INSTRUCTION_BUDGET, (
        f"server instructions are {length} chars, over the {INSTRUCTION_BUDGET} "
        "budget; trim something rather than raising the cap"
    )


def test_chat_scoring_uses_one_assignment_type_neutral_stage_apply_flow():
    instructions = server._SERVER_INSTRUCTIONS

    assert "prepare_scoring_session" in instructions
    assert "get_scoring_packet" in instructions
    assert "stage_scoring_results" in instructions
    assert "apply_staged_scoring_results" in instructions
    assert "get_scoring_preview" in instructions
    assert "agent_commentary" in instructions
    assert "PowerGrader" not in instructions
    assert "OpenRouter" not in instructions


def test_chat_side_canvas_landing_is_still_offered():
    instructions = server._SERVER_INSTRUCTIONS

    assert "apply_staged_scoring_results" in instructions
    assert "resubmit unchanged results" in instructions
    assert "bounded question" in instructions


def test_the_agent_refreshes_itself_and_the_old_permission_rules_are_gone():
    instructions = server._SERVER_INSTRUCTIONS

    assert "mirror_refresh_in_progress" not in instructions
    assert "at most four total calls" not in instructions
    assert "refresh_mirror yourself" in instructions
    for tool in ("refresh_mirror", "structure_only=true"):
        assert tool in instructions
    assert "explicit teacher request" not in instructions
    assert "never read back" not in instructions


def test_scoring_preparation_wait_and_open_session_rules_are_explicit():
    instructions = server._SERVER_INSTRUCTIONS

    assert "use_existing_mirror=true" in instructions
    assert "scoring_session_already_open" in instructions
    assert "continue that session" in instructions
    assert "continue that session" in instructions


def test_preview_commentary_and_warn_before_push_rules_are_stated_early():
    """A client that cuts the tail must still deliver the preview and push rules,
    so they sit ahead of the cross-device, content and attachment hints."""
    instructions = server._SERVER_INSTRUCTIONS

    assert "Agent commentary (teacher only)" in instructions
    assert "exactly as returned" in instructions
    assert "push_content_live" in instructions
    assert "wait for the teacher's go" in instructions
    assert (instructions.index("wait for the teacher's go")
            < instructions.index("Across devices"))
    # Claude Code cuts the block near 2,050 characters: the push rule has to end
    # inside that, or the one rule that protects Canvas is the part that is lost.
    push_rule = "apply_staged_scoring_results only when told to push."
    assert instructions.index(push_rule) + len(push_rule) <= 2048


def test_write_rules_precede_the_discovery_hints():
    """If a client truncates the tail, lose the product-guide nudge, not the
    rule that bounds how far one teacher request reaches."""
    instructions = server._SERVER_INSTRUCTIONS

    assert (instructions.index("selected discovery rows together")
            < instructions.index("get_product_guide"))


def test_no_generated_schema_titles_reach_the_client():
    """pydantic labels every property with a title made from its own name, and
    every tool with <name>Arguments / <name>Outputs. It is 22% of the listing
    and says nothing the property key did not. Asserted on the real list_tools
    payload, not on our own copy of the schemas."""
    listed = asyncio.run(server.mcp.list_tools())
    wire = json.dumps([tool.model_dump(exclude_none=True) for tool in listed],
                      separators=(",", ":"), ensure_ascii=False)

    def generated_titles(node, path="$"):
        # A key inside "properties" is a parameter name -- verify_live has a real
        # one called "title" -- so only each property's own schema is searched.
        if isinstance(node, list):
            return [hit for i, item in enumerate(node)
                    for hit in generated_titles(item, f"{path}[{i}]")]
        if not isinstance(node, dict):
            return []
        hits = [path] if "title" in node else []
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                hits += [hit for name, prop in value.items()
                         for hit in generated_titles(prop, f"{path}.properties.{name}")]
            else:
                hits += generated_titles(value, f"{path}.{key}")
        return hits

    leaked = [hit for tool in listed
              for hit in generated_titles(tool.inputSchema, tool.name)]
    assert server._STRIPPED_SCHEMA_TITLES > 0, "the strip pass found nothing to strip"
    assert not leaked, f"a generated schema title is reaching clients again: {leaked}"
    assert len(wire) <= LISTING_BUDGET, (
        f"serialized tools/list is {len(wire)} chars, over the {LISTING_BUDGET} "
        "character budget"
    )


def test_every_tool_first_line_is_a_complete_useful_sentence():
    """One real client renders exactly the first physical line and no more."""
    listed = asyncio.run(server.mcp.list_tools())

    for tool in listed:
        first_line = (tool.description or "").split("\n", 1)[0].strip()
        assert len(first_line) >= 30, (
            f"{tool.name} first line is too short to identify its job: {first_line!r}"
        )
        assert first_line.endswith("."), (
            f"{tool.name} first line is not a complete sentence: {first_line!r}"
        )


def test_each_description_stays_within_the_achieved_slice_b_maximum():
    listed = asyncio.run(server.mcp.list_tools())

    for tool in listed:
        assert len(tool.description or "") <= DESCRIPTION_BUDGET, (
            f"{tool.name} description is {len(tool.description or '')} chars, over the "
            f"{DESCRIPTION_BUDGET}-character maximum"
        )


def test_next_procedures_are_static_bounded_and_gate_safe():
    """Every registered tool is checked against B's exact result-advisory allowlist."""
    registered = set(server.mcp._tool_manager._tools)

    expected_next = RESULT_NEXT_TOOLS | {
        "discover_scoring_work", "stage_scoring_results",
        "apply_staged_scoring_results", "preview_grade_adjustment",
        "preview_attempts_grant",
    }
    assert set(tools._NEXT_STEPS) == expected_next
    assert RESULT_NEXT_TOOLS < registered
    for name in registered:
        assert (name in tools._NEXT_STEPS) == (name in expected_next)
    for name, procedure in tools._NEXT_STEPS.items():
        assert isinstance(procedure, str) and procedure.strip()
        success = tools._with_next(name, {"ok": True})
        refusal = tools._with_next(name, {"ok": False, "error": "synthetic"})
        assert success == {"ok": True, "next": procedure}
        assert refusal == {"ok": False, "error": "synthetic"}
        assert tools.final_response_gate(success) == success


def test_first_lines_disclose_preview_and_canvas_write_boundaries():
    descriptions = {
        tool.name: tool.description or ""
        for tool in asyncio.run(server.mcp.list_tools())
    }
    first_lines = {
        name: description.split("\n", 1)[0]
        for name, description in descriptions.items()
    }

    for name in ("preview_roster_student_change",):
        assert "without writing" in first_lines[name]
    for name in ("preview_sis_grade_bridge", "preview_content_push"):
        assert "persist" in first_lines[name].casefold()
        assert "local" in first_lines[name]
    for name in ("apply_operation",):
        assert "Canvas" in first_lines[name]
    assert "no Canvas write" in first_lines["stage_scoring_results"]
    assert "Canvas" in first_lines["apply_staged_scoring_results"]
    assert "local roster settings" in first_lines["apply_roster_student_change"]
    assert "list_courses" in first_lines["list_courses"]
    assert "stand-ins" in first_lines["get_roster"]
    assert "Never raises" not in "\n".join(descriptions.values())


def test_the_schemas_themselves_survive_the_strip():
    """Stripping titles must not cost a name, a type, a default, or a required
    list: those are the parts a client needs to call the tool correctly."""
    listed = asyncio.run(server.mcp.list_tools())
    submissions = next(tool for tool in listed if tool.name == "get_submissions")
    schema = submissions.inputSchema

    assert schema["required"] == ["course_id", "assignment_id"]
    assert schema["properties"]["course_id"] == {"type": "string"}
    assert schema["properties"]["max_text_chars"]["default"] is None
    assert schema["properties"]["include_text"]["default"] is True


def test_all_registered_tools_use_text_only_result_transport():
    listed = asyncio.run(server.mcp.list_tools())
    assert len(listed) == 37
    registry = server.mcp._tool_manager._tools
    assert all(tool.outputSchema is None for tool in listed)
    assert all(item.fn_metadata.output_schema is None
               for item in registry.values())


def test_protocol_call_returns_one_text_block_without_structured_result():
    result = asyncio.run(server.mcp.call_tool(
        "get_product_guide", {"topic": "overview"}))
    assert len(result) == 1
    assert result[0].type == "text"
    assert isinstance(result[0].text, str)


def test_compact_preserves_unicode_tables_and_runs_final_gate(monkeypatch):
    seen = []

    def gate(payload):
        seen.append(payload)
        return {"ok": False, "error": "échec", "table": {
            "columns": ["élève"], "rows": [["Zoë"]],
        }}

    monkeypatch.setattr(server.tools, "final_response_gate", gate)
    wire = server._compact({"raw": "discarded"})

    assert seen == [{"raw": "discarded"}]
    assert "échec" in wire and "Zoë" in wire
    assert "\\u00e9" not in wire
    assert json.loads(wire)["table"]["rows"] == [["Zoë"]]


def test_scoring_packet_rubric_label_passes_final_gate_and_identity_name_fails(
    monkeypatch, tmp_path,
):
    vault = feedback_vault.Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign("900001", real_name="Invented Student")
    pseudonym = feedback_vault._REGISTRY_WORDS[1]
    vault.set_pseudonym("900001", pseudonym)
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    monkeypatch.setattr(tools, "_open_vault", lambda: (vault, None))
    monkeypatch.setattr(
        tools.config, "active_courses", lambda: [{"id": "111", "name": "Course"}]
    )

    session = {
        "session_id": "session-1", "session_kind": "scoring_assignment",
        "course_id": "111",
        "assignment_name": "Quiz",
        "assignment_id": "assignment-1",
        "created": "2026-01-01T08:00:00",
        "storage_model": "shared_work.v1",
        "scoring_basis": {"source": "canvas_rubric", "label": "Test Rubric"},
        "scoring_rubric_text": "Award credit for a correct explanation.",
        "students": [{"user_id": "900001", "status": "pending"}],
        "privacy_artifacts": {"safe_bundle": str(tmp_path / "bundle.json")},
        "status": "ready",
    }
    (tmp_path / "bundle.json").write_text(json.dumps({
        "contract_version": "2.0",
        "quiz_title": "Quiz",
        "students": [{
            "pseudonym": pseudonym,
            "responses": [{
                "item_id": "item-1",
                "prompt": "Explain the answer.",
                "response": "A fabricated response with enough words to score.",
                "possible": 10,
            }],
        }],
    }), encoding="utf-8")
    from api.powergrader import session_store
    from api.shared_work import SharedWorkStore
    monkeypatch.setattr(session_store, "SharedWorkStore",
                        lambda: SharedWorkStore(root=str(tmp_path / "workspace")))
    session_store.activate_scoring_session(session)
    wire = server._compact(tools.get_scoring_packet("session-1"))
    result = json.loads(wire)

    assert result["ok"] is True, result
    assert result["rubric"] == {"label": "Test Rubric", "included": True}
    assert "Award credit for a correct explanation." in result["contract"]
    assert result["included_context"] is True

    blocked_wire = server._compact({
        "ok": True,
        "students": [{"pseudonym": pseudonym}],
        "rubric": {"name": "Identity-bearing field"},
    })
    blocked = json.loads(blocked_wire)
    assert blocked["ok"] is False
    assert "identity field 'name'" in " ".join(blocked["violations"])


def test_each_registered_wrapper_returns_one_gated_text_block(_synthetic_mcp):
    async def call_all():
        results = []
        for name in _synthetic_mcp["names"]:
            results.append((name, await server.mcp.call_tool(
                name, _synthetic_mcp["required_arguments"](name))))
        return results

    results = asyncio.run(call_all())
    assert len(results) == 37
    assert len(_synthetic_mcp["calls"]) == 37
    assert len(_synthetic_mcp["gated"]) == 37
    for name, content in results:
        assert len(content) == 1
        assert content[0].type == "text"
        assert json.loads(content[0].text) == {
            "ok": True,
            "delegate": name,
            "table": {"columns": ["élève"], "rows": [["Zoë"]]},
        }


def test_wrapper_protocol_preserves_structured_failure(_synthetic_mcp, monkeypatch):
    def refused(*args, **kwargs):
        return {"ok": False, "error": "synthetic refusal"}

    monkeypatch.setattr(server.tools, "list_courses", refused)
    content = asyncio.run(server.mcp.call_tool("list_courses", {}))
    assert len(content) == 1
    assert json.loads(content[0].text) == {
        "ok": False,
        "delegate": None,
        "table": {"columns": ["élève"], "rows": [["Zoë"]]},
    }
