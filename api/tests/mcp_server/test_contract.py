"""Contract test for api/mcp_server/contract.py.

A shape agreement at the FastMCP registration boundary: the versioned,
disk-frozen tool schema must describe exactly the tools and parameters the
live server actually registers. Parametrized over the live registry itself
(``contract.live_contract``), so a newly registered tool is covered without
a new test -- only a version bump and a regenerated schema file.
"""
from api.mcp_server import contract, server


def test_current_schema_matches_the_live_fastmcp_registry():
    assert contract.TOOL_SCHEMA_VERSION == 60
    expected = contract.load_contract()
    live = contract.live_contract(server.mcp)
    assert live == expected


def test_no_bare_session_id_parameter_reaches_the_live_mcp_surface():
    """A remote bridge strips any tool argument literally named ``session_id``
    before it reaches this server (docs/handoffs/scoring-session-id-rename.md),
    so no registered tool may accept or require that exact name again.

    Asserted over ``live_contract`` -- the real FastMCP registry, not our own
    schema snapshot -- so a future tool that reintroduces a bare
    ``session_id`` parameter fails this test without needing a new one.
    """
    live = contract.live_contract(server.mcp)

    for tool in live["tools"]:
        assert "session_id" not in tool["properties"], (
            f"{tool['name']} accepts a bare session_id parameter"
        )
        assert "session_id" not in tool["required"], (
            f"{tool['name']} requires a bare session_id parameter"
        )
