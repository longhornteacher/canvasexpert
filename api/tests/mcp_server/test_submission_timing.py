"""Privacy laws for get_submissions latency diagnostics."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("history", [False, True])
def test_get_submissions_emits_fixed_stage_timings_without_private_values(
    evidence_mirror, monkeypatch, history,
):
    from api.mcp_server import tools

    evidence_mirror["publish"]("read_path")
    emitted = []

    def capture(event, outcome, **fields):
        emitted.append((event, outcome, fields))

    monkeypatch.setattr(tools.operational_log, "emit", capture)
    result = tools.get_submissions("1", "10", history=history)
    assert result["ok"] is True

    events = [event for event, _outcome, _fields in emitted]
    assert "mcp.get_submissions.total" in events
    assert "mcp.get_submissions.stage.vault_open" in events
    assert "mcp.get_submissions.stage.index_page" in events
    assert "mcp.get_submissions.stage.outbound_gate" in events
    assert events.count("mcp.get_submissions.stage.index_page") >= (2 if history else 3)
    for event, outcome, fields in emitted:
        assert event in {
            "mcp.get_submissions.total",
            "mcp.get_submissions.stage.vault_open",
            "mcp.get_submissions.stage.index_page",
            "mcp.get_submissions.stage.outbound_gate",
        }
        assert outcome in {"ok", "refused", "failed"}
        assert set(fields) == {"duration_ms"}
        assert type(fields["duration_ms"]) is int and fields["duration_ms"] >= 0
        assert "1" not in event and "10" not in event


def test_get_submissions_succeeds_when_timing_logging_raises(evidence_mirror, monkeypatch):
    from api.mcp_server import tools

    evidence_mirror["publish"]("read_path")

    def broken_emit(*_args, **_kwargs):
        raise OSError("synthetic log failure")

    monkeypatch.setattr(tools.operational_log, "emit", broken_emit)
    result = tools.get_submissions("1", "10")
    assert result["ok"] is True
