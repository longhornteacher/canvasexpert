"""The list and packet share one session visibility/freshness contract."""
import pytest

from api.mcp_server import tools


@pytest.mark.parametrize("candidate, expected", [
    ({}, True),
    ({"course": "222"}, False),
    ({"assignment": "700020"}, False),
    ({"created": "2026-01-01T08:00:00"}, False),
    ({"created": "2025-12-31T08:00:00"}, False),
    ({"visible": False}, False),
    ({"readable": False}, False),
])
def test_newer_flags_compare_only_strictly_newer_visible_same_assignment_sessions(
    _scoring_visibility_case, _rows, candidate, expected,
):
    session_id = _scoring_visibility_case(**candidate)
    listed = tools.list_scoring_sessions()
    row = next(row for row in _rows(listed["sessions"]) if row["scoring_session_id"] == session_id)
    # Page zero must carry the resolved scoring contract; later pages may omit it.
    packet = tools.get_scoring_packet(session_id, offset=1, include_context=False)

    assert packet["ok"] is True
    assert row["newer_session_exists"] is expected
    assert packet["newer_session_exists"] is expected
