"""New Quiz writing is unsupported by the scoring write surface."""
from api.mcp_server import server, tools


def test_new_quiz_has_no_assignment_type_specific_mcp_tools_or_parameters():
    names = set(server.mcp._tool_manager._tools)
    assert {"prepare_scoring_session", "list_scoring_sessions",
            "get_scoring_packet", "submit_scoring_results"} <= names
    assert not names.intersection({
        "stage_scores", "preview_assignment_scores", "apply_assignment_scores",
        "preview_new_quiz_scores", "apply_new_quiz_scores",
    })

    prepare = next(tool for tool in server.mcp._tool_manager._tools.values()
                   if tool.name == "prepare_scoring_session")
    submit = next(tool for tool in server.mcp._tool_manager._tools.values()
                  if tool.name == "submit_scoring_results")
    for public_tool in (prepare, submit):
        properties = public_tool.parameters.get("properties") or {}
        assert not any("quiz" in key.casefold() or "assignment_type" in key.casefold()
                       for key in properties)

    assert callable(tools.submit_scoring_results)


def test_signed_new_quiz_finalization_members_are_retired():
    for name in (
        "_prepare_new_quiz_finalization",
        "_finalize_new_quiz_results",
        "_new_quiz_http_scope",
        "_new_quiz_preflight",
        "_new_quiz_apply",
        "_new_quiz_scoring_questions",
        "_new_quiz_public_questions",
    ):
        assert not hasattr(tools, name)


def test_submit_scoring_results_schema_names_the_canvas_score_row():
    submit = next(tool for tool in server.mcp._tool_manager._tools.values()
                  if tool.name == "submit_scoring_results")
    schema = submit.parameters
    result = (schema.get("$defs") or {}).get("ScoringResult") or {}
    properties = result.get("properties") or {}

    assert schema["properties"]["results"]["items"] == {"$ref": "#/$defs/ScoringResult"}
    assert set(result.get("required") or []) == {
        "pseudonym", "item_id", "score", "feedback",
    }
    assert properties["pseudonym"]["type"] == "string"
    assert properties["item_id"]["type"] == "string"
    assert properties["feedback"]["type"] == "string"
    assert properties["score"]["anyOf"] == [{"type": "number"}, {"type": "null"}]
    assert "writing_process_observations" in properties
    assert "title" not in result
    assert all("title" not in (prop or {}) for prop in properties.values())
