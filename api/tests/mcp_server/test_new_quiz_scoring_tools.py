"""The public New Quiz path is the ordinary Scoring Session submit contract."""
from api.mcp_server import server, tools


def test_new_quiz_has_no_assignment_type_specific_mcp_tools_or_parameters():
    names = set(server.mcp._tool_manager._tools)
    assert {"start_scoring_session", "list_scoring_sessions",
            "get_scoring_packet", "submit_scoring_results"} <= names
    assert not names.intersection({
        "stage_scores", "preview_assignment_scores", "apply_assignment_scores",
        "preview_new_quiz_scores", "apply_new_quiz_scores",
    })

    start = next(tool for tool in server.mcp._tool_manager._tools.values()
                 if tool.name == "start_scoring_session")
    submit = next(tool for tool in server.mcp._tool_manager._tools.values()
                  if tool.name == "submit_scoring_results")
    for public_tool in (start, submit):
        properties = public_tool.parameters.get("properties") or {}
        assert not any("quiz" in key.casefold() or "assignment_type" in key.casefold()
                       for key in properties)

    assert callable(tools.submit_scoring_results)
