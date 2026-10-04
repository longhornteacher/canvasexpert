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


# Observed truncation in a real client landed near 2,300 characters, and Claude
# Code cuts the block near 2,050. We cannot hold every client to that, but we can
# stop the block growing: any addition has to earn its place by displacing
# something. Raised once from 2,200 to the measured 2,669 (rounded up) for the
# preview, agent-commentary, self-refresh and warn-before-push rules; the
# teacher-facing rules come first and the cross-device, content and attachment
# hints last, because the tail is what gets cut.
INSTRUCTION_BUDGET = 2700
# Slice B moved post-call procedure into bounded result advisories. Raised once
# from 17,717 for the staged-content push pair: preview_content_push carries the
# delivery options as named parameters rather than one opaque object, so the
# teacher's "publish it in module 3, due Friday" survives into the schema.
# Raised once for stage_content and push_content_live, then cut hard by
# retiring the calendar and bell write pairs, the teacher-schedule write, and
# get_seating_context: 12 tools built to feed the classroom display, which is
# gone. Their reads stay. Note what the two numbers teach, because the next
# person here will face the same choice: trimming push_content_live's date
# parameters bought 109 characters, and dropping 12 tools bought 4,850. Tools
# are the unit that costs, not their options.
# Raised once for preview_assignment_update/apply_assignment_update, the
# id-addressed publish/date patch pair for an assignment that already
# exists: both descriptions were already trimmed to single sentences before
# raising this, so the remaining cost is the two tools' own name/schema
# structure, not wordy prose.
# The current unified scoring surface has one preparation, one packet, one staged
# write, one apply, and one optional identity-free list tool.
# v54 keeps the measured listing ceiling while replacing the old direct submit.
# Raised once to the measured 18,569 for nine tools added while the title
# assertion above was failing on verify_live's real `title` parameter and
# masking this one: shared work-item handoff (85ce67a, 4 tools, +982), push
# verification and tiered recovery (149c2a4, 3 tools, +1,097), and reviewed
# grade adjustment (23f32c9, 2 tools, +652). Held at 15,950 through b2f28b7.
# The v2 scoring result removes compulsory pedagogy and staging inputs; its
# live schema size remains under the existing measured ceiling.
# Raised to the measured 18,902 when that work landed on top of the v62
# stage_attachment tool, which had fit under the older ceiling by itself.
# Raised to the measured 18,964 for the scoring-lane effort-credit brief:
# ScoringResult (stage_scoring_results's per-row shape) gains two optional
# fields, insincere and late_days.
# Raised to the measured 20,291 for the reviewed attempts-grant pair
# (preview_attempts_grant, apply_attempts_grant, +748): extra attempts and a
# reopened window for a pseudonym list or the whole class, across regular,
# Classic, and New Quiz assignments.
# Raised to the measured 20,535 for stage_scoring_results grade_mode
# (post_score or feedback_only, +244): a numeric draft score in feedback
# with no gradebook score.
# Raised to the measured 20,793 for the retained assignment-history read.
# Raised to the measured 22,352 for the four feedback-only revision tools
# (prepare_feedback_revision, get_feedback_revision_packet,
# stage_feedback_revisions, apply_staged_feedback_revisions, +1,559).
# Raised to the measured 22,613 for prepare_scoring_session's late_policy (+261).
# Raised to the measured 22,962 for refresh_scoring_session (+349).
# Raised by 986 measured characters for the three durable score-rule/ledger
# tools added in schema v73; the descriptions and result summaries remain short.
# Raised to the measured 24,279 for get_scoring_preview (schema v74, +331).
# Schema v76 retires writing-history and learning-objective tools; the live
# v76 listing measures 21,762 characters.
LISTING_BUDGET = 21762
DESCRIPTION_BUDGET = 343

RESULT_NEXT_TOOLS = {
    "get_scoring_packet",
    "get_scoring_preview",
    "prepare_scoring_session",
    "refresh_scoring_session",
    "preview_sis_grade_bridge",
    "preview_sis_grade_bridge_reconciliation",
    "preview_roster_student_change",
    "preview_content_push",
    "preview_differentiated_quiz_push",
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
    assert "resubmit the same results to stage_scoring_results" in instructions
    assert "bounded scoring guidance" in instructions


def test_the_agent_refreshes_itself_and_the_old_permission_rules_are_gone():
    instructions = server._SERVER_INSTRUCTIONS

    assert "mirror_refresh_in_progress" not in instructions
    assert "at most four total calls" not in instructions
    assert "refresh it yourself" in instructions
    for tool in ("refresh_mirror", "refresh_course_structure", "refresh_scoring_session"):
        assert tool in instructions
    assert "explicit teacher request" not in instructions
    assert "never read back" not in instructions


def test_scoring_preparation_wait_and_open_session_rules_are_explicit():
    instructions = server._SERVER_INSTRUCTIONS

    assert "use_existing_mirror=true" in instructions
    assert "scoring_session_already_open" in instructions
    assert "never re-prepare it" in instructions
    assert "work locally" in instructions


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
    for name in ("apply_sis_grade_bridge", "apply_content_push"):
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
    assert schema["properties"]["max_text_chars"] == {"default": 2000, "type": "integer"}
    assert schema["properties"]["include_text"]["default"] is True


def test_all_registered_tools_use_text_only_result_transport():
    listed = asyncio.run(server.mcp.list_tools())
    assert len(listed) == 62
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
    assert len(results) == 62
    assert len(_synthetic_mcp["calls"]) == 62
    assert len(_synthetic_mcp["gated"]) == 62
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
